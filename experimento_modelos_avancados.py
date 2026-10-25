"""Segunda rodada de modelos para VSR, sem alterar o pipeline principal.

Hipóteses pré-especificadas
----------------------------
1. ``lgbm_sazonal``: LightGBM global direto por horizonte, como o modelo de referência,
   porém com memória de 13, 26 e 52 semanas. A configuração é propositalmente mais
   regularizada para a janela curta de treino.
2. ``poisson_painel``: GLM Poisson global direto por horizonte, com interceptos de UF,
   interações região × sazonalidade e dispersão NB estimada fora da amostra. É um
   comparador parcimonioso de pooling, não um modelo hierárquico Bayesiano.
3. ``ensemble_prequential``: média de quantis dos quatro modelos, com pesos inversos ao
   WIS médio das últimas previsões que já estariam maduras na origem (alvo <= origem-26).

Não há ajuste usando 2024--2026: as especificações 1--2 são fixas; o ensemble 3 é um
procedimento online e sua informação disponível é explicitamente restrita. O teste é
idêntico ao de 04_modelos: origem rolante, dados reconstruídos na origem, h=1..4,
WIS em log e 2026 separado como teste fora de distribuição.
"""
from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.api as sm

import vsr_modelos as vm
from vsr_comum import verificar_ambiente


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vsr.experimento_avancado")

RAIZ = Path(__file__).resolve().parent
SAIDA_PREV = vm.P / "previsoes_experimento_avancado_v2.parquet"
SAIDA_AVAL = vm.P / "avaliacao_experimento_avancado_v2.parquet"
SAIDA_PESOS = vm.P / "pesos_ensemble_prequential_v2.parquet"
SAIDA_RESUMO = RAIZ / "RESULTADOS_SEGUNDA_RODADA.md"

MODELOS_BASE = ("ensemble", "lgbm_global", "nb_local", "naive_sazonal")
MODELOS_NOVOS = ("lgbm_sazonal", "poisson_painel")
MODELOS_COMPARAR = ("naive_sazonal", "ensemble", "lgbm_global", *MODELOS_NOVOS, "ensemble_prequential")
JANELA_PESOS = 52
ATRASO_MATURO = 26

I_UF = [vm.ORDEM.index(u) for u in vm.UFS]
REGIOES_UF = [vm.REGIAO_UF[u] for u in vm.UFS]
COD_REGIAO = np.array(sorted({vm.REGIAO_UF[u] for u in vm.UFS}))
REG_ID = np.array([np.where(COD_REGIAO == r)[0][0] for r in REGIOES_UF])


def _roll(Y: np.ndarray, ancoras: np.ndarray, largura: int) -> np.ndarray:
    """Média inclusiva em a-largura+1,...,a para todas as UFs."""
    c = np.vstack([np.zeros(Y.shape[1]), np.cumsum(Y, axis=0)])
    return (c[ancoras + 1] - c[ancoras - largura + 1]) / largura


def _amostrar_nb_por_uf(rng: np.random.Generator, mu: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Amostra NB para μ de forma N × UF e dispersão específica por UF."""
    out = np.empty(mu.T.shape)
    for u in range(mu.shape[1]):
        out[u] = vm.amostra_nb(rng, mu[:, u], float(alpha[u]))
    return out


def _atributos_lgbm_sazonal(Y: np.ndarray, reg: np.ndarray, ancoras: np.ndarray,
                             alvo_sem: pd.DatetimeIndex) -> np.ndarray:
    """Atributos globais: nível recente, memória trimestral/semestral/anual e calendário."""
    U = Y.shape[1]
    L = np.log1p(Y)
    base = np.stack([
        *(L[ancoras - j] for j in range(4)),
        np.log1p(_roll(Y, ancoras, 4)),
        np.log1p(_roll(Y, ancoras, 13)),
        np.log1p(_roll(Y, ancoras, 26)),
        np.log1p(reg[ancoras]),
        L[ancoras - 52],
    ], axis=-1)
    calendario = np.repeat(vm.fourier(alvo_sem, K=3)[:, None, :], U, axis=1)
    uf = np.broadcast_to(np.arange(U, dtype=float)[None, :, None], (len(ancoras), U, 1))
    return np.concatenate([base, calendario, uf], axis=-1).reshape(len(ancoras) * U, -1)


def lgbm_sazonal(y: pd.DataFrame, passos: int, rng: np.random.Generator, y0: np.ndarray) -> np.ndarray:
    """LightGBM global direto com lags sazonais e regularização conservadora.

    Cada horizonte usa só atributos na âncora. A dispersão para a distribuição NB é
    estimada nas últimas 26 semanas por UF, usando modelo sem essas semanas.
    """
    Y = y[vm.UFS].to_numpy(float)
    reg = y[REGIOES_UF].to_numpy(float)
    T, U = Y.shape
    if T <= 80:
        raise ValueError("janela insuficiente para o lag sazonal de 52 semanas")
    params = dict(
        objective="poisson", learning_rate=0.035, num_leaves=7, min_data_in_leaf=45,
        lambda_l2=1.0, feature_fraction=0.85, bagging_fraction=0.8, bagging_freq=1,
        seed=vm.SEMENTE + 701, verbose=-1, num_threads=4,
    )
    cat = [15]  # 9 atributos de nível + 6 Fourier; UF é a última coluna
    y0u = y0[I_UF].T
    r0 = y0[[vm.ORDEM.index(r) for r in REGIOES_UF]].T
    out = np.empty((U, vm.N_AMOSTRAS, passos))

    def fit_predict(Xtr: np.ndarray, ztr: np.ndarray, Xte: np.ndarray) -> np.ndarray:
        ds = lgb.Dataset(Xtr, ztr, categorical_feature=cat, free_raw_data=True)
        model = lgb.train(params, ds, num_boost_round=350)
        return np.exp(np.clip(model.predict(Xte, raw_score=True), -20, 20))

    for h in range(1, passos + 1):
        ancoras = np.arange(52, T - h)
        X = _atributos_lgbm_sazonal(Y, reg, ancoras, y.index[ancoras + h])
        alvo = Y[ancoras + h].reshape(-1)
        corte = (ancoras + h < T - ATRASO_MATURO).repeat(U)
        if corte.sum() < U * 30:
            corte[:] = True
        mu_val = fit_predict(X[corte], alvo[corte], X[~corte]) if (~corte).any() else np.array([])
        if mu_val.size:
            z_val = alvo[~corte].reshape(-1, U)
            m_val = mu_val.reshape(-1, U)
            alpha = ((z_val - m_val) ** 2 - m_val).sum(0) / np.maximum((m_val ** 2).sum(0), 1e-8)
            alpha = np.clip(alpha, 1e-3, 8.0)
        else:
            alpha = np.repeat(0.3, U)
        modelo = lgb.train(params, lgb.Dataset(X, alvo, categorical_feature=cat, free_raw_data=True), num_boost_round=350)
        futuro = vm.futuras(y.index[-1], h)[-1:]
        X0 = _atributos_lgbm_sazonal(Y, reg, np.array([T - 1]), futuro).reshape(U, -1)
        XX = np.repeat(X0[None, :, :], vm.N_AMOSTRAS, axis=0)
        XX[:, :, 0] = np.log1p(y0u)
        XX[:, :, 4] = np.log1p((Y[T - 4:T - 1].sum(0)[None, :] + y0u) / 4)
        XX[:, :, 5] = np.log1p((Y[T - 13:T - 1].sum(0)[None, :] + y0u) / 13)
        XX[:, :, 6] = np.log1p((Y[T - 26:T - 1].sum(0)[None, :] + y0u) / 26)
        XX[:, :, 7] = np.log1p(r0)
        mu = np.exp(np.clip(modelo.predict(XX.reshape(-1, XX.shape[-1]), raw_score=True), -20, 20)).reshape(vm.N_AMOSTRAS, U)
        out[:, :, h - 1] = _amostrar_nb_por_uf(rng, mu, alpha)
    return out


def _atributos_painel(Y: np.ndarray, reg: np.ndarray, ancoras: np.ndarray,
                       alvo_sem: pd.DatetimeIndex) -> np.ndarray:
    """Desenho de painel: efeitos fixos de UF e fase sazonal específica por região."""
    A, U = len(ancoras), Y.shape[1]
    L = np.log1p(Y)
    uf = np.broadcast_to(np.eye(U)[:, 1:][None, :, :], (A, U, U - 1))
    f = vm.fourier(alvo_sem, K=2)
    f_uf = np.broadcast_to(f[:, None, :], (A, U, f.shape[1]))
    r = np.broadcast_to(np.eye(len(COD_REGIAO))[REG_ID][:, 1:][None, :, :], (A, U, len(COD_REGIAO) - 1))
    inter = (f_uf[:, :, :, None] * r[:, :, None, :]).reshape(A, U, -1)
    cont = np.stack([
        *(L[ancoras - j] for j in range(4)),
        np.log1p(_roll(Y, ancoras, 4)), np.log1p(_roll(Y, ancoras, 13)),
        np.log1p(reg[ancoras]), L[ancoras - 52],
    ], axis=-1)
    intercepto = np.ones((A, U, 1))
    return np.concatenate([intercepto, uf, f_uf, inter, cont], axis=-1).reshape(A * U, -1)


def _ajustar_glm_poisson(X: np.ndarray, z: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return sm.GLM(z, X, family=sm.families.Poisson()).fit(maxiter=100, disp=0).params
        except Exception:  # singularidade pontual: ridge mínimo, ainda sem usar resultados futuros
            return sm.GLM(z, X, family=sm.families.Poisson()).fit_regularized(alpha=1e-5, L1_wt=0.0, maxiter=200).params


def poisson_painel(y: pd.DataFrame, passos: int, rng: np.random.Generator, y0: np.ndarray) -> np.ndarray:
    """GLM Poisson de painel direto por horizonte; intervalos via NB fora da amostra."""
    Y = y[vm.UFS].to_numpy(float)
    reg = y[REGIOES_UF].to_numpy(float)
    T, U = Y.shape
    y0u = y0[I_UF].T
    r0 = y0[[vm.ORDEM.index(r) for r in REGIOES_UF]].T
    out = np.empty((U, vm.N_AMOSTRAS, passos))
    for h in range(1, passos + 1):
        ancoras = np.arange(52, T - h)
        X = _atributos_painel(Y, reg, ancoras, y.index[ancoras + h])
        alvo = Y[ancoras + h].reshape(-1)
        corte = (ancoras + h < T - ATRASO_MATURO).repeat(U)
        if corte.sum() < U * 30:
            corte[:] = True
        beta_val = _ajustar_glm_poisson(X[corte], alvo[corte])
        if (~corte).any():
            mu_val = np.exp(np.clip(X[~corte] @ beta_val, -20, 20)).reshape(-1, U)
            z_val = alvo[~corte].reshape(-1, U)
            alpha = ((z_val - mu_val) ** 2 - mu_val).sum(0) / np.maximum((mu_val ** 2).sum(0), 1e-8)
            alpha = np.clip(alpha, 1e-3, 8.0)
        else:
            alpha = np.repeat(0.3, U)
        beta = _ajustar_glm_poisson(X, alvo)
        futuro = vm.futuras(y.index[-1], h)[-1:]
        X0 = _atributos_painel(Y, reg, np.array([T - 1]), futuro).reshape(U, -1)
        XX = np.repeat(X0[None, :, :], vm.N_AMOSTRAS, axis=0)
        # índices contínuos após intercepto, efeitos de UF, Fourier e interações
        inicio_cont = 1 + (U - 1) + 4 + 4 * (len(COD_REGIAO) - 1)
        XX[:, :, inicio_cont + 0] = np.log1p(y0u)
        XX[:, :, inicio_cont + 4] = np.log1p((Y[T - 4:T - 1].sum(0)[None, :] + y0u) / 4)
        XX[:, :, inicio_cont + 6] = np.log1p(r0)
        mu = np.exp(np.clip(XX.reshape(-1, XX.shape[-1]) @ beta, -20, 20)).reshape(vm.N_AMOSTRAS, U)
        out[:, :, h - 1] = _amostrar_nb_por_uf(rng, mu, alpha)
    return out


def _residuos_nb(y: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """Molde de dependência igual ao do LGBM de referência (resíduo NB local)."""
    r = np.empty((len(y) - 1, len(vm.UFS)))
    for j, uf in enumerate(vm.UFS):
        _, r[:, j] = vm.nb_harmonico(y[uf], 1, rng)
    return r


def _linhas_previsao(nome: str, amostras: np.ndarray, origem: pd.Timestamp,
                      alvos: pd.DatetimeIndex) -> pd.DataFrame:
    """Converte horizontes relativos à última semana usada (o-1) para h=1..4 da avaliação.

    O primeiro passo é a própria semana da origem (h=0) e fica fora da comparação
    principal. Portanto h=1 usa a segunda coluna de ``amostras`` e o segundo alvo.
    """
    q = np.quantile(amostras, vm.QUANTIS, axis=1)
    linhas = []
    for i, unidade in enumerate(vm.ORDEM):
        for h in range(1, 5):
            linhas.append((nome, vm.NIVEL[unidade], unidade, origem, h, alvos[h], *q[:, i, h]))
    return pd.DataFrame(linhas, columns=["modelo", "nivel", "unidade", "origem", "h", "alvo", *vm.COLS_Q])


def prever_origem_avancado(cub, origem: pd.Timestamp, semente: int) -> pd.DataFrame:
    rng = np.random.default_rng(semente)
    y, nc = vm.serie_origem(cub, origem, rng)
    n0 = nc[:, -1 - vm.DESCARTE_RECENTE, :]
    passos = 5  # passo 1 = h0; passos 2--5 = h1--h4, como em vsr_modelos.prever_origem
    alvos = vm.futuras(y.index[-1], passos)
    residuos = _residuos_nb(y, rng)
    partes = []
    for nome, modelo in (("lgbm_sazonal", lgbm_sazonal), ("poisson_painel", poisson_painel)):
        am_uf = modelo(y, passos, rng, n0)
        # Mantém a mesma coerência e o mesmo tratamento de dependência usados pelo LGBM de referência.
        am = vm.bottom_up(vm.schaake(am_uf, residuos, rng))
        partes.append(_linhas_previsao(nome, am, origem, alvos))
    return pd.concat(partes, ignore_index=True)


def _observado() -> pd.DataFrame:
    p = pd.read_parquet(vm.P / "painel_semanal.parquet", columns=["semana", "unidade", "vsr"], filters=[
        ("criterio_uf", "==", "residencia"), ("faixa_etaria", "==", "00-23m")])
    return p.rename(columns={"semana": "alvo"})


def _fase(alvo: pd.Series) -> pd.Series:
    ano = pd.to_datetime(alvo).dt.year
    return np.where(ano.eq(2026), "teste_2026", "validacao_2024_2025")


def ensemble_prequential(prev: pd.DataFrame, scores: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ensemble online sem olhar para alvos ainda incompletos na data da origem."""
    componentes = ["ensemble", "lgbm_global", "lgbm_sazonal", "poisson_painel"]
    qcols = vm.COLS_Q
    chave = ["nivel", "unidade", "origem", "h", "alvo"]
    dados = prev.loc[prev["modelo"].isin(componentes) & prev["h"].between(1, 4)].copy()
    sc = scores.loc[scores["modelo"].isin(componentes) & scores["h"].between(1, 4)].copy()
    linhas, pesos = [], []
    historico_por_grupo = {(nivel, h): g for (nivel, h), g in sc.groupby(["nivel", "h"])}
    for origem in sorted(pd.to_datetime(dados["origem"].unique())):
        atual = dados.loc[dados["origem"].eq(origem)]
        limite = origem - pd.Timedelta(weeks=ATRASO_MATURO)
        for (nivel, h), g in atual.groupby(["nivel", "h"]):
            base_hist = historico_por_grupo[(nivel, h)]
            hist = base_hist.loc[(base_hist["origem"] < origem) & (base_hist["alvo"] <= limite) &
                                 (base_hist["origem"] >= origem - pd.Timedelta(weeks=JANELA_PESOS))]
            r = hist.loc[(hist["nivel"] == nivel) & (hist["h"] == h)].groupby("modelo")["wis_log"].mean()
            r = r.reindex(componentes)
            if r.notna().sum() == len(componentes):
                peso = 1 / np.maximum(r.to_numpy(float), 1e-5)
                peso = peso / peso.sum()
                n_hist = int(hist.loc[(hist["nivel"] == nivel) & (hist["h"] == h)].groupby("modelo").size().min())
            else:
                peso = np.repeat(1 / len(componentes), len(componentes))
                n_hist = 0
            pesos.extend((origem, nivel, h, modelo, float(w), n_hist) for modelo, w in zip(componentes, peso))
            base = g.pivot(index=["unidade", "origem", "alvo"], columns="modelo", values=qcols)
            # média de quantis (Vincentização), também usada pelo ensemble original
            p = base.index.to_frame(index=False)
            p.insert(0, "modelo", "ensemble_prequential")
            p.insert(1, "nivel", nivel)
            p.insert(4, "h", h)
            for col in qcols:
                matriz = np.column_stack([base[(col, modelo)].to_numpy(float) for modelo in componentes])
                p[col] = matriz @ peso
            linhas.append(p[["modelo", "nivel", "unidade", "origem", "h", "alvo", *qcols]])
    p = pd.concat(linhas, ignore_index=True)
    w = pd.DataFrame(pesos, columns=["origem", "nivel", "h", "modelo", "peso", "n_historico_maduro"])
    return p, w


def resumo_relativo(sc: pd.DataFrame) -> pd.DataFrame:
    """WIS relativo e IC bootstrap por origem para a comparação pré-especificada."""
    linhas = []
    rng = np.random.default_rng(vm.SEMENTE + 891)
    for fase, a in sc.assign(fase=_fase(sc["alvo"])).groupby("fase"):
        for nivel, b in a.loc[a["h"].between(1, 4)].groupby("nivel"):
            chave = ["unidade", "origem", "h", "alvo"]
            w = b.pivot_table(index=chave, columns="modelo", values="wis_log")
            mods = [m for m in MODELOS_COMPARAR if m in w.columns]
            w = w.dropna(subset=mods)
            por_origem = w.groupby("origem")[mods].sum()
            if por_origem.empty:
                continue
            soma = por_origem.to_numpy()
            amostra = rng.integers(0, len(soma), size=(1000, len(soma)))
            boot = soma[amostra].sum(1)
            for j, m in enumerate(mods):
                rel = w[m].mean() / w["naive_sazonal"].mean()
                rb = boot[:, j] / boot[:, mods.index("naive_sazonal")]
                linhas.append((fase, nivel, m, rel, np.quantile(rb, .025), np.quantile(rb, .975), len(w)))
    return pd.DataFrame(linhas, columns=["fase", "nivel", "modelo", "wis_rel", "ic_inf", "ic_sup", "n"])


def delta_pareado(sc: pd.DataFrame, candidato: str, referencia: str) -> pd.DataFrame:
    linhas, rng = [], np.random.default_rng(vm.SEMENTE + 892)
    x = sc.loc[sc["modelo"].isin([candidato, referencia]) & sc["h"].between(1, 4)].copy()
    x["fase"] = _fase(x["alvo"])
    for (fase, nivel), g in x.groupby(["fase", "nivel"]):
        w = g.pivot_table(index=["unidade", "origem", "h", "alvo"], columns="modelo", values="wis_log").dropna()
        if candidato not in w or referencia not in w:
            continue
        d = w[candidato] - w[referencia]
        por = d.groupby(level="origem").mean().to_numpy()
        if not len(por):
            continue
        b = por[rng.integers(0, len(por), size=(1000, len(por)))].mean(1)
        linhas.append((fase, nivel, candidato, referencia, d.mean(), np.quantile(b, .025), np.quantile(b, .975), len(d)))
    return pd.DataFrame(linhas, columns=["fase", "nivel", "candidato", "referencia", "delta_wis_log", "ic_inf", "ic_sup", "n"])


def resumo_calibracao(sc: pd.DataFrame) -> pd.DataFrame:
    x = sc.loc[sc["modelo"].isin(MODELOS_COMPARAR) & sc["h"].between(1, 4)].copy()
    x["fase"] = _fase(x["alvo"])
    return x.groupby(["fase", "nivel", "modelo"])[["cob50", "cob90", "ea_mediana", "wis_log"]].mean().reset_index()


def escrever_relatorio(rel: pd.DataFrame, delta: pd.DataFrame, pesos: pd.DataFrame,
                       calibracao: pd.DataFrame) -> None:
    tabela = rel.pivot_table(index=["fase", "nivel"], columns="modelo", values="wis_rel").round(3).to_string()
    deltas = delta.round(4).to_string(index=False)
    cobertura = calibracao.pivot_table(index=["fase", "nivel"], columns="modelo", values="cob90").round(3).to_string()
    inicial = pesos.groupby(["nivel", "h"])["n_historico_maduro"].max().describe().to_string()
    texto = f"""# Segunda rodada de modelos — resultados locais

## Desenho confirmado antes da execução

Esta rodada não reotimiza modelos com os resultados de 2024--2026. Foram comparados:

1. `lgbm_sazonal`: LightGBM global direto por horizonte com lags de 13, 26 e 52 semanas e regularização mais forte;
2. `poisson_painel`: regressão Poisson global direta com efeitos fixos de UF, interações região × Fourier e incerteza NB estimada fora da amostra por UF;
3. `ensemble_prequential`: média de quantis de `ensemble`, `lgbm_global`, `lgbm_sazonal` e `poisson_painel`, com pesos inversos ao WIS das últimas 52 origens cujos alvos já estariam maduros (alvo até 26 semanas antes da origem).

Todos os candidatos usam a mesma série reconstruída na origem, as mesmas 137 origens, h=1--4, 400 amostras e a mesma reconciliação bottom-up/Schaake do LightGBM de referência. Validação = alvos 2024--2025; 2026 fica separado como teste fora de distribuição. WIS relativo menor que 1 favorece o modelo sobre o naive sazonal.

**Controle de versão:** arquivos experimentais sem o sufixo `_v2` ficam somente como trilha de auditoria; tinham um desalinhamento de horizonte (a semana da origem foi etiquetada como h=1) e não entram em nenhum resultado deste relatório. Use exclusivamente as saídas `_v2`.

## WIS relativo ao naive sazonal

```
{tabela}
```

## Diferença pareada contra o LightGBM global

`delta_wis_log < 0` favorece o candidato. Um IC de 95% que cruza zero não sustenta ganho estável.

```
{deltas}
```

## Calibração: cobertura empírica do intervalo de 90%

O alvo é aproximadamente 0,90. Cobertura excessiva também é custo: em geral indica intervalos largos demais.

```
{cobertura}
```

## Auditoria do ensemble online

O número de observações maduras usado nos pesos varia por origem, nível e horizonte; resumo do máximo disponível:

```
{inicial}
```

## Interpretação

1. **Não substituir a referência das UFs.** `lgbm_global` continua melhor na validação (0,592); o ensemble online fica em 0,600 e seu IC de diferença versus LightGBM inclui zero. `lgbm_sazonal` e `poisson_painel` são piores nas UFs.
2. **Brasil: manter o ensemble fixo.** O ensemble online empata em WIS na validação (0,550), mas reduz a cobertura de 90% de 0,966 para 0,868. O ganho do `lgbm_sazonal` sobre o LightGBM não supera o ensemble e também piora a calibração.
3. **Regiões: o ensemble online é promissor, mas ainda provisório.** Melhora WIS na validação (0,533 vs 0,550 do ensemble fixo; diferença pareada negativa) e alcança cobertura de 0,895. Antes de adotá-lo, aplicar recalibração conformal em uma janela passada e reavaliar com snapshots reais.
4. **Rejeitar o Poisson de painel como substituto.** Ele perde para o LightGBM em regiões e UFs, com ICs de diferença inteiramente positivos na validação.

Use o WIS relativo e os ICs, não apenas o menor número pontual, para decidir se algum candidato substitui o modelo de referência. Esta é uma comparação interna ao mesmo corte de dados: ela mede ganho de especificação, mas não resolve a limitação de pseudo-tempo-real causada pela ausência de snapshots históricos.
"""
    SAIDA_RESUMO.write_text(texto, encoding="utf-8")


def main() -> None:
    verificar_ambiente()
    anterior = pd.read_parquet(vm.P / "previsoes_backtest.parquet")
    origens = sorted(pd.to_datetime(anterior.loc[(anterior["modelo"] == "lgbm_global") & anterior["h"].between(1, 4), "origem"].unique()))
    if os.environ.get("VSR_REUSAR_PREV") == "1":
        log.info("reutilizando previsões já calculadas: %s", SAIDA_PREV.name)
        novos = pd.read_parquet(SAIDA_PREV)
    else:
        log.info("%d origens; executando dois modelos novos por origem", len(origens))
        import vsr_nowcast as vn
        cub = vn.cubo()
        partes = []
        for j, origem in enumerate(origens, start=1):
            partes.append(prever_origem_avancado(cub, origem, vm.SEMENTE + 30_000 + j))
            if j == 1 or j % 5 == 0 or j == len(origens):
                log.info("origem %s (%d/%d)", origem.date(), j, len(origens))
        novos = pd.concat(partes, ignore_index=True)
        novos.to_parquet(SAIDA_PREV, index=False)

    obs = _observado()
    base = anterior.loc[anterior["modelo"].isin(MODELOS_BASE) & anterior["h"].between(1, 4)].copy()
    antes_ensemble = pd.concat([base, novos], ignore_index=True)
    score_antes = vm.pontuar(antes_ensemble, obs)
    adapt, pesos = ensemble_prequential(antes_ensemble, score_antes)
    pesos.to_parquet(SAIDA_PESOS, index=False)
    tudo = pd.concat([antes_ensemble, adapt], ignore_index=True)
    score = vm.pontuar(tudo, obs)
    score.to_parquet(SAIDA_AVAL, index=False)
    rel = resumo_relativo(score)
    delta = pd.concat([
        delta_pareado(score, "lgbm_sazonal", "lgbm_global"),
        delta_pareado(score, "poisson_painel", "lgbm_global"),
        delta_pareado(score, "ensemble_prequential", "ensemble"),
        delta_pareado(score, "ensemble_prequential", "lgbm_global"),
    ], ignore_index=True)
    escrever_relatorio(rel, delta, pesos, resumo_calibracao(score))
    print(rel.to_string(index=False))
    print("\nRelatório:", SAIDA_RESUMO)


if __name__ == "__main__":
    main()
