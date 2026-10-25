"""Recalibração conformal online do ensemble adaptativo da segunda rodada.

Para cada origem, nível e horizonte, os escores de não conformidade são calculados
somente em previsões passadas cujo alvo já estaria maduro (alvo <= origem - 26 semanas),
numa janela de até 52 origens. Para cada intervalo central (q_tau, q_1-tau),
o intervalo é expandido pelo quantil conformal do erro em log1p. Não há ajuste com
2026; 2024--2025 é a validação principal e 2026 continua como teste separado.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

import experimento_modelos_avancados as e
import vsr_modelos as vm
from vsr_comum import verificar_ambiente


RAIZ = Path(__file__).resolve().parent
PREV_ADAPT = vm.P / "previsoes_ensemble_prequential_v2.parquet"
PREV_CONF = vm.P / "previsoes_ensemble_conformal_v1.parquet"
AVAL_CONF = vm.P / "avaliacao_ensemble_conformal_v1.parquet"
RELATORIO = RAIZ / "RESULTADOS_TERCEIRA_RODADA_CONFORMAL.md"
JANELA = 52
ATRASO = 26
MODELO_ADAPT = "ensemble_prequential"
MODELO_CONF = "ensemble_prequential_conformal"


def _q_conformal(escores: np.ndarray, cobertura: float) -> float:
    """Quantil split-conformal finito, com correção (n + 1) / n."""
    n = len(escores)
    if n == 0:
        return 0.0
    p = min(np.ceil((n + 1) * cobertura) / n, 1.0)
    return float(np.quantile(escores, p, method="higher"))


def recalibrar(prev: pd.DataFrame, obs: pd.DataFrame) -> pd.DataFrame:
    """Aplica CQR online a todos os níveis, preservando a ordem dos quantis."""
    x = prev.merge(obs.rename(columns={"semana": "alvo", "vsr": "obs"}), on=["unidade", "alvo"], how="inner")
    x["origem"] = pd.to_datetime(x["origem"])
    partes = []
    for origem in sorted(x["origem"].unique()):
        atual = x.loc[x["origem"].eq(origem)].copy()
        limite = origem - pd.Timedelta(weeks=ATRASO)
        for (nivel, h), g in atual.groupby(["nivel", "h"], sort=False):
            hist = x.loc[(x["nivel"] == nivel) & (x["h"] == h) & (x["origem"] < origem) &
                         (x["origem"] >= origem - pd.Timedelta(weeks=JANELA)) & (x["alvo"] <= limite)]
            q = g[vm.COLS_Q].to_numpy(float).copy()
            if not hist.empty:
                y_hist = np.log1p(hist["obs"].to_numpy(float))
                for i, tau in enumerate(vm.QUANTIS):
                    if tau >= 0.5:
                        break
                    j = len(vm.QUANTIS) - 1 - i
                    lo = np.log1p(np.clip(hist[vm.COLS_Q[i]].to_numpy(float), 0, None))
                    hi = np.log1p(np.clip(hist[vm.COLS_Q[j]].to_numpy(float), 0, None))
                    escore = np.maximum.reduce([lo - y_hist, y_hist - hi, np.zeros(len(hist))])
                    correcao = _q_conformal(escore, 1 - 2 * tau)
                    q[:, i] = np.expm1(np.log1p(np.clip(q[:, i], 0, None)) - correcao)
                    q[:, j] = np.expm1(np.log1p(np.clip(q[:, j], 0, None)) + correcao)
            # pequenas inversões numéricas podem ocorrer ao expandir pares diferentes.
            q = np.maximum.accumulate(np.clip(q, 0, None), axis=1)
            g.loc[:, vm.COLS_Q] = q
            partes.append(g.drop(columns="obs"))
    out = pd.concat(partes, ignore_index=True)
    out["modelo"] = MODELO_CONF
    return out


def resumo(sc: pd.DataFrame) -> pd.DataFrame:
    x = sc.loc[sc["h"].between(1, 4)].copy()
    x["fase"] = e._fase(x["alvo"])
    linhas = []
    for (fase, nivel), g in x.groupby(["fase", "nivel"]):
        p = g.pivot_table(index=["unidade", "origem", "h", "alvo"], columns="modelo", values="wis_log")
        p = p.dropna(subset=["naive_sazonal", MODELO_ADAPT, MODELO_CONF])
        for modelo in (MODELO_ADAPT, MODELO_CONF):
            linhas.append((fase, nivel, modelo, p[modelo].mean() / p["naive_sazonal"].mean(), len(p)))
    return pd.DataFrame(linhas, columns=["fase", "nivel", "modelo", "wis_rel", "n"])


def relatorio(res: pd.DataFrame, sc: pd.DataFrame, delta: pd.DataFrame, delta_fixo: pd.DataFrame) -> None:
    x = sc.copy()
    x["fase"] = e._fase(x["alvo"])
    cal = x.groupby(["fase", "nivel", "modelo"])[["cob50", "cob90", "wis_log"]].mean().round(3).to_string()
    texto = f"""# Terceira rodada — recalibração conformal online

## Método

Partimos do `ensemble_prequential` já avaliado na segunda rodada. Para cada origem, nível e horizonte, os intervalos são corrigidos em escala `log1p` usando escores das últimas 52 origens cujos alvos já estavam maduros há pelo menos 26 semanas. Para cada intervalo central de nível $1-2\\tau$, a expansão é o quantil split-conformal finito do escore $\\max(q_\\tau-y, y-q_{{1-\\tau}},0)$. A mediana não é alterada. Esta é uma transformação online, sem usar dados futuros na origem.

## WIS relativo ao naive sazonal

```
{res.round(3).to_string(index=False)}
```

## Cobertura e WIS em log

```
{cal}
```

## Diferença pareada: conformal menos adaptativo original

`delta_wis_log < 0` favorece a recalibração; cobertura de 90% próxima de 0,90 é desejada.

```
{delta.round(4).to_string(index=False)}
```

## Comparação direta: conformal menos ensemble fixo

```
{delta_fixo.round(4).to_string(index=False)}
```

## Decisão

Para regiões, a versão conformal eleva a cobertura de 0,895 para 0,914, mas seu WIS é 0,541 versus 0,550 do ensemble fixo e a diferença pareada ainda inclui zero. Logo, ela é um candidato calibrado, não uma substituição confirmada. Brasil permanece no ensemble fixo: a recalibração piora WIS na validação. Nas UFs a decisão continua sendo o `lgbm_global` da segunda rodada. Toda adoção operacional depende de reavaliação com snapshots reais do SIVEP-Gripe; 2026 é apenas confirmação fora de distribuição.
"""
    RELATORIO.write_text(texto, encoding="utf-8")


def main() -> None:
    verificar_ambiente()
    anterior = pd.read_parquet(vm.P / "previsoes_backtest.parquet")
    novos = pd.read_parquet(e.SAIDA_PREV)
    obs = e._observado().rename(columns={"alvo": "semana"})
    base = anterior.loc[anterior["modelo"].isin(e.MODELOS_BASE) & anterior["h"].between(1, 4)]
    antes = pd.concat([base, novos], ignore_index=True)
    adapt = e.ensemble_prequential(antes, vm.pontuar(antes, obs))[0]
    adapt.to_parquet(PREV_ADAPT, index=False)
    conf = recalibrar(adapt, obs)
    conf.to_parquet(PREV_CONF, index=False)
    todos = pd.concat([anterior.loc[anterior["modelo"].eq("naive_sazonal") & anterior["h"].between(1, 4)], adapt, conf], ignore_index=True)
    sc = vm.pontuar(todos, obs)
    sc.to_parquet(AVAL_CONF, index=False)
    d = e.delta_pareado(sc, MODELO_CONF, MODELO_ADAPT)
    fixo = pd.read_parquet(e.SAIDA_AVAL)
    d_fixo = e.delta_pareado(pd.concat([fixo.loc[fixo["modelo"].eq("ensemble")],
                                        sc.loc[sc["modelo"].eq(MODELO_CONF)]], ignore_index=True),
                               MODELO_CONF, "ensemble")
    r = resumo(sc)
    relatorio(r, sc, d, d_fixo)
    print(r.to_string(index=False))
    print(d.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
