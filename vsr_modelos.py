"""Modelagem do VSR semanal: dados em tempo real, nowcast, modelos, reconciliação e avaliação.

Cada origem `o` (domingo) usa só o que se sabia ao fim da semana `o`: a versão reconstruída pelo
triângulo de atraso; nas últimas D + 1 semanas, o nowcast probabilístico `gamma_rw` (vsr_nowcast, §6.1).
Todos os modelos devolvem amostras (unidades × N_AMOSTRAS × PASSOS); os quantis saem das amostras.
Definições e justificativas: docs/METODOLOGIA.md §11.
"""
from __future__ import annotations

import logging
import os
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.statespace.sarimax import SARIMAX

from vsr_comum import P, marcos_amostras

log = logging.getLogger("vsr.modelos")

QUANTIS = np.round(np.r_[0.01, 0.025, np.arange(0.05, 0.951, 0.05), 0.975, 0.99], 3)   # 23 níveis (CDC)
COLS_Q = [f"q{q:g}" for q in QUANTIS]
HORIZONTES = (0, 1, 2, 3, 4)       # h = 0 é a própria semana da origem (modelo `nowcast`)
N_AMOSTRAS = 400
D = 26                             # atraso máximo do triângulo (semanas)
INICIO_TREINO = pd.Timestamp("2022-01-02")   # 1ª semana após quebra_npi
JANELA_TREINO = 156                # semanas
PERIODO = 52.1775                  # semanas por ano
SEMENTE = 20261019
SERIE = os.environ.get("VSR_SERIE", "vsr")   # alvo; "srag" = sensibilidade (§11.8), saídas com sufixo _srag
# A semana da origem (d = 0, F ≈ 0,09) não entra como dado: com ela, o h = 0 piorou (WIS log validação BR 0,140 → 0,176;
# UF 0,262 → 0,280) e h = 1–4 também (§11.1). O `nowcast` dessa semana entra só como membro do ensemble em h = 0.
DESCARTE_RECENTE = 1

UNIDADES = pd.read_csv(P / "hierarquia_unidades.csv")
S = pd.read_csv(P / "matriz_S.csv", index_col=0)               # 33 unidades × 27 UFs
UFS = list(S.columns)
ORDEM = list(S.index)
REGIAO_UF = UNIDADES.loc[UNIDADES["nivel"] == "UF"].set_index("unidade")["pai"].to_dict()
NIVEL = UNIDADES.set_index("unidade")["nivel"].to_dict()
AGREGADOS = [u for u in ORDEM if NIVEL[u] != "UF"]


# --------------------------------------------------------------------------- dados em tempo real
def carregar_triangulo() -> pd.DataFrame:
    t = pd.read_parquet(P / "triangulo_atraso.parquet",
                        filters=[("serie", "==", SERIE), ("criterio_uf", "==", "residencia")])
    return t.groupby(["uf", "semana", "atraso"], as_index=False)["casos"].sum()   # soma as faixas


def serie_origem(cub: tuple[np.ndarray, pd.DatetimeIndex], origem: pd.Timestamp,
                 rng) -> tuple[pd.DataFrame, np.ndarray]:
    """Série de treino na origem (33 unidades, janela fixa) e amostras do nowcast (33 × D+1 semanas × N).

    Semanas t < o − D: versão (todos os atrasos já conhecidos); o − D … o: mediana do nowcast `gamma_rw`.
    As últimas DESCARTE_RECENTE semanas saem da série (não de `nc`).
    """
    import vsr_nowcast as vn   # import local: vsr_nowcast importa deste módulo
    A, semanas = cub
    nc, _ = vn.nowcast_padrao(A, semanas, origem, rng)
    i0 = semanas.get_loc(origem)
    ini = max(semanas.get_loc(INICIO_TREINO) if INICIO_TREINO in semanas else 0, i0 - JANELA_TREINO + 1)
    v = A[:, ini:i0 + 1, :].sum(2)
    v[:, -(D + 1):] = np.median(nc, 2)
    y = pd.DataFrame(v.T, index=semanas[ini:i0 + 1], columns=ORDEM)
    return y.iloc[:len(y) - DESCARTE_RECENTE], nc


# --------------------------------------------------------------------------- utilidades
def sufixo() -> str:
    """Sufixo dos arquivos de saída: "" para o alvo principal, "_srag" etc. na sensibilidade."""
    return "" if SERIE == "vsr" else f"_{SERIE}"


def semana_epi(semanas: pd.DatetimeIndex) -> np.ndarray:
    qua = semanas + pd.Timedelta(days=3)
    return ((qua.dayofyear - 1) // 7 + 1).to_numpy()


def fourier(semanas: pd.DatetimeIndex, K: int = 2) -> np.ndarray:
    w = 2 * np.pi * semana_epi(semanas)[:, None] * np.arange(1, K + 1)[None, :] / PERIODO
    return np.hstack([np.sin(w), np.cos(w)])


def amostra_nb(rng, mu: np.ndarray, alpha: float) -> np.ndarray:
    mu = np.clip(mu, 1e-9, 1e7)
    if alpha <= 1e-8:
        return rng.poisson(mu).astype(float)
    n = 1.0 / alpha
    return rng.negative_binomial(n, n / (n + mu)).astype(float)


def futuras(ultima: pd.Timestamp, passos: int) -> pd.DatetimeIndex:
    return pd.date_range(ultima + pd.Timedelta(weeks=1), periods=passos, freq="7D")


# --------------------------------------------------------------------------- modelos locais
def naive_sazonal(y: pd.Series, passos: int, rng) -> np.ndarray:
    """y_{t+h} = y_{t+h−52}, incerteza por bootstrap dos resíduos sazonais em log1p."""
    l = np.log1p(y.to_numpy())
    e = l[52:] - l[:-52]
    i = len(l) - 52 + np.arange(passos)
    base = l[np.where(i >= len(l), i - 52, i)]       # passos > 52 (temporada de 53 semanas): 2 anos atrás
    return np.clip(np.expm1(base[None, :] + rng.choice(e, (N_AMOSTRAS, passos))), 0, None)


def climatologia(clim: pd.DataFrame, unidade: str, alvos: pd.DataFrame, rng) -> np.ndarray:
    """exp(N(μ, σ)) − 1 com a climatologia expanding da temporada de cada alvo."""
    c = clim.loc[clim["unidade"] == unidade].set_index(["temporada_alvo", "semana_temporada"])
    k = pd.MultiIndex.from_arrays([alvos["temporada"], alvos["semana_temporada"]])
    mu = c["media_log1p"].reindex(k).to_numpy()
    sd = c["dp_log1p"].reindex(k).to_numpy()
    sd = np.where(np.isfinite(sd), sd, np.nanmedian(c["dp_log1p"]))
    return np.clip(np.expm1(mu[None, :] + sd[None, :] * rng.standard_normal((N_AMOSTRAS, len(mu)))), 0, None)


def nb_harmonico(y: pd.Series, passos: int, rng, ant0: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Binomial negativa NB2: log μ_t = β0 + Fourier(K=2) + γ log1p(y_{t−1}); simulação recursiva.

    `ant0` (N): ponto de partida da recursão por amostra (amostras do nowcast da última semana).
    Devolve (amostras N × passos, resíduos de um passo y − μ̂ no treino, alinhados a y.index[1:]).
    """
    v = y.to_numpy()
    if v.sum() == 0:
        return np.zeros((N_AMOSTRAS, passos)), np.zeros(len(v) - 1)
    X = np.column_stack([np.ones(len(v) - 1), fourier(y.index[1:]), np.log1p(v[:-1])])
    alpha = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            r = sm.NegativeBinomial(v[1:], X, loglike_method="nb2").fit(disp=0, maxiter=300)
            beta, alpha = r.params[:-1], float(r.params[-1])
            if not (r.mle_retvals["converged"] and np.all(np.isfinite(r.params)) and 0 < alpha < 50):
                raise ValueError("NB não convergiu")
        except Exception:  # noqa: BLE001 - fallback documentado: Poisson
            beta, alpha = sm.GLM(v[1:], X, family=sm.families.Poisson()).fit().params, 0.0
    resid = v[1:] - np.exp(np.clip(X @ beta, None, 15))
    Xf = np.column_stack([np.ones(passos), fourier(futuras(y.index[-1], passos))])
    out = np.empty((N_AMOSTRAS, passos))
    ant = np.full(N_AMOSTRAS, v[-1]) if ant0 is None else np.asarray(ant0, float)
    for k in range(passos):
        mu = np.exp(np.clip(Xf[k] @ beta[:-1] + beta[-1] * np.log1p(ant), None, 15))
        out[:, k] = ant = amostra_nb(rng, mu, alpha)
    return out, resid


# shortcut: ETS e ARIMA partem da mediana do nowcast (sem propagar sua incerteza); trocar se pesarem no ensemble
def ets(y: pd.Series, passos: int, rng) -> np.ndarray:
    """ETS(A,Ad,N) em log1p; simulação do modelo de espaço de estados."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = ETSModel(np.log1p(y.to_numpy()), error="add", trend="add", damped_trend=True).fit(disp=False)
        sim = r.simulate(passos, repetitions=N_AMOSTRAS, anchor="end", rng=rng)
    return np.clip(np.expm1(np.asarray(sim).reshape(passos, N_AMOSTRAS).T), 0, None)


def arima_fourier(y: pd.Series, passos: int, rng) -> np.ndarray:
    """ARIMA(2,0,0) com constante e sazonalidade harmônica (Fourier K=2) em log1p.

    Substitui o SARIMA com s = 52, instável em séries semanais curtas (regressão harmônica dinâmica).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = SARIMAX(np.log1p(y.to_numpy()), exog=fourier(y.index), order=(2, 0, 0), trend="c").fit(disp=False)
        sim = r.simulate(passos, repetitions=N_AMOSTRAS, anchor="end", exog=fourier(futuras(y.index[-1], passos)),
                         rng=rng)
    return np.clip(np.expm1(np.asarray(sim).reshape(passos, N_AMOSTRAS).T), 0, None)


# --------------------------------------------------------------------------- modelo global
def _linhas_lgbm(Y: np.ndarray, reg: np.ndarray, ancoras: np.ndarray, alvo_sem: pd.DatetimeIndex) -> np.ndarray:
    """Atributos na âncora a (uma linha por âncora × UF): log1p(y_a, …, y_{a−3}), log1p(média de 4 semanas),
    log1p(total da região em a), Fourier da semana-alvo, código da UF (categórico, última coluna)."""
    L, U = np.log1p(Y), Y.shape[1]
    c = np.vstack([np.zeros(U), np.cumsum(Y, axis=0)])
    mm4 = (c[ancoras + 1] - c[ancoras - 3]) / 4
    X = np.stack([*(L[ancoras - j] for j in range(4)), np.log1p(mm4), np.log1p(reg[ancoras])], axis=-1)
    f = np.repeat(fourier(alvo_sem)[:, None, :], U, axis=1)
    uf = np.broadcast_to(np.arange(U, dtype=float)[None, :, None], (len(ancoras), U, 1))
    return np.concatenate([X, f, uf], axis=-1).reshape(len(ancoras) * U, -1)


def lgbm_global(y: pd.DataFrame, passos: int, rng, y0: np.ndarray, pop: pd.DataFrame | None = None) -> np.ndarray:
    """LightGBM global (todas as UFs, UF como categoria), objetivo Poisson, um modelo por passo.

    Dispersão NB α por passo estimada nas últimas 26 semanas com um modelo ajustado sem elas
    (fora da amostra); o modelo final usa toda a janela. Na previsão, a última semana de cada amostra vem
    de `y0` (33 × N, amostras do nowcast). Com `pop` (semanas de y e futuras × ORDEM): atributos em taxa
    por 100 mil e offset log(pop/1e5) no objetivo (log μ = log(pop/1e5) + f(x)). Devolve UFs × N × passos.
    """
    Y = y[UFS].to_numpy()
    reg = y[[REGIAO_UF[u] for u in UFS]].to_numpy()
    T, U = Y.shape
    par = dict(objective="poisson", learning_rate=0.05, num_leaves=15, min_data_in_leaf=20, bagging_fraction=0.8,
               bagging_freq=1, feature_fraction=0.9, seed=SEMENTE, verbose=-1, num_threads=4)
    cat = [10]   # coluna da UF: 6 defasagens/médias + 4 termos de Fourier

    def treinar(X, alvo, off):
        ds = lgb.Dataset(X, alvo, categorical_feature=cat, init_score=off)
        return lgb.train(par, ds, num_boost_round=300)

    def prever(m, X, off):
        return np.exp(m.predict(X, raw_score=True) + (0 if off is None else off))

    y0u = y0[[ORDEM.index(u) for u in UFS]].T                                   # N × U
    r0 = y0[[ORDEM.index(REGIAO_UF[u]) for u in UFS]].T
    if pop is None:
        lpop, eu, er = None, np.ones((T, U)), np.ones((T, U))
    else:
        pu = pop[UFS].reindex(y.index.append(futuras(y.index[-1], passos))).to_numpy()
        lpop = np.log(pu / 1e5)
        eu, er = 1e5 / pu[:T], 1e5 / pop[[REGIAO_UF[u] for u in UFS]].reindex(y.index).to_numpy()
    Yf, regf = Y * eu, reg * er
    out = np.empty((U, N_AMOSTRAS, passos))
    for k in range(1, passos + 1):
        anc = np.arange(3, T - k)
        X = _linhas_lgbm(Yf, regf, anc, y.index[anc + k])
        alvo = Y[anc + k].reshape(-1)
        off = None if lpop is None else lpop[anc + k].reshape(-1)
        corte = (anc + k < T - 26).repeat(U)
        val = treinar(X[corte], alvo[corte], None if off is None else off[corte])
        mu_v = prever(val, X[~corte], None if off is None else off[~corte])
        alpha = max(float(((alvo[~corte] - mu_v) ** 2 - mu_v).sum() / (mu_v ** 2).sum()), 1e-3)
        m = treinar(X, alvo, off)
        X = np.repeat(_linhas_lgbm(Yf, regf, np.array([T - 1]), futuras(y.index[-1], k)[-1:])[None], N_AMOSTRAS, 0)
        X[..., 0] = np.log1p(y0u * eu[-1])                                       # atributos com y_a
        X[..., 4] = np.log1p((Yf[T - 4:T - 1].sum(0) + y0u * eu[-1]) / 4)
        X[..., 5] = np.log1p(r0 * er[-1])
        offk = None if lpop is None else np.tile(lpop[T - 1 + k], N_AMOSTRAS)
        mu = prever(m, X.reshape(N_AMOSTRAS * U, -1), offk).reshape(N_AMOSTRAS, U)
        out[:, :, k - 1] = amostra_nb(rng, mu.T, alpha)
    return out


def populacao_semanal() -> pd.DataFrame | None:
    """pop_menores2 por semana × unidade (painel, 00-23m); None se o painel não tiver população."""
    p = pd.read_parquet(P / "painel_semanal.parquet", filters=[("criterio_uf", "==", "residencia"),
                        ("faixa_etaria", "==", "00-23m")])
    if "pop_menores2" not in p:
        return None
    w = p.pivot_table(index="semana", columns="unidade", values="pop_menores2")[ORDEM]
    fut = pd.date_range(w.index[-1] + pd.Timedelta(weeks=1), periods=60, freq="7D")
    return w.reindex(w.index.append(fut)).ffill()     # semanas futuras: população do último ano


# --------------------------------------------------------------------------- reconciliação
def cov_shrink(R: np.ndarray, piso: float = 1e-2) -> np.ndarray:
    """Covariância com shrinkage para a diagonal (Schäfer & Strimmer, 2005; como em hts::MinT)."""
    n = R.shape[0]
    X = R - R.mean(0)
    sd = X.std(0, ddof=1)
    sd = np.where(sd > 0, sd, 1.0)
    Z = X / sd
    corr = Z.T @ Z / (n - 1)
    w2 = Z ** 2
    v = (w2.T @ w2 - (Z.T @ Z) ** 2 / n) / (n * (n - 1))
    fora = ~np.eye(R.shape[1], dtype=bool)
    lam = float(np.clip(v[fora].sum() / (corr[fora] ** 2).sum(), 0, 1))
    W = np.cov(X, rowvar=False)
    W = lam * np.diag(np.diag(W)) + (1 - lam) * W
    return W + np.diag(np.maximum(piso - np.diag(W), 0))


def mint(amostras: np.ndarray, residuos: np.ndarray) -> np.ndarray:
    """MinT-shrink sobre amostras: ỹ = S G ŷ, G = (S'W⁻¹S)⁻¹ S'W⁻¹; amostras 33 × N × passos."""
    Sm = S.to_numpy(float)
    Wi = np.linalg.pinv(cov_shrink(residuos))
    G = np.linalg.solve(Sm.T @ Wi @ Sm, Sm.T @ Wi)
    rec = np.einsum("ij,jnk->ink", Sm @ G, amostras)
    return np.clip(rec, 0, None)   # contagens: negativos (raros) truncados em 0


def bottom_up(amostras_uf: np.ndarray) -> np.ndarray:
    return np.einsum("ij,jnk->ink", S.to_numpy(float), amostras_uf)


def schaake(amostras: np.ndarray, residuos: np.ndarray, rng) -> np.ndarray:
    """Schaake shuffle (Clark et al., 2004): reordena as N amostras de cada unidade e passo pelos postos de N
    blocos de `passos` semanas consecutivas de resíduos históricos (T × unidades). Marginais intactas;
    dependência entre unidades e entre horizontes = a do molde. amostras: unidades × N × passos.
    """
    # shortcut: N = 400 > blocos disponíveis (~150), blocos repetidos; aceitável enquanto JANELA_TREINO = 156
    U, N, K = amostras.shape
    b = rng.integers(0, len(residuos) - K + 1, N)
    M = residuos[b[:, None] + np.arange(K)] + rng.uniform(0, 1e-9, (N, K, U))   # N × K × U; desempate
    posto = M.argsort(0).argsort(0).transpose(2, 0, 1)                            # U × N × K
    return np.take_along_axis(np.sort(amostras, axis=1), posto, axis=1)


# --------------------------------------------------------------------------- uma origem
MODELOS_ENSEMBLE = ("naive_sazonal", "climatologia", "nb_local", "lgbm_global", "ets", "arima_fourier", "nowcast")


def prever_origem(cub: tuple[np.ndarray, pd.DatetimeIndex], clim: pd.DataFrame, calendario: pd.DataFrame,
                  origem: pd.Timestamp, rng, pop: pd.DataFrame | None = None) -> pd.DataFrame:
    """Modelos a partir da última semana usada (o − 1), h = 0..4; NB e LightGBM partem de cada amostra do
    nowcast dessa semana. `nowcast` (amostras de o) só em h = 0, como membro do ensemble."""
    y, nc = serie_origem(cub, origem, rng)
    n0 = nc[:, -1 - DESCARTE_RECENTE, :]                   # 33 × N: última semana usada
    passos = len(HORIZONTES) + DESCARTE_RECENTE - 1
    alvos_sem = futuras(y.index[-1], passos)
    alvos = calendario.reindex(alvos_sem)
    am: dict[str, np.ndarray] = {}                         # modelo → 33 × N × passos (NaN = não se aplica)
    vazio = lambda: np.full((len(ORDEM), N_AMOSTRAS, passos), np.nan)  # noqa: E731

    am["naive_sazonal"], am["climatologia"], am["nb_local"] = vazio(), vazio(), vazio()
    am["ets"], am["arima_fourier"] = vazio(), vazio()
    residuos = np.zeros((len(y) - 1, len(ORDEM)))
    for i, u in enumerate(ORDEM):
        am["naive_sazonal"][i] = naive_sazonal(y[u], passos, rng)
        am["climatologia"][i] = climatologia(clim, u, alvos, rng)
        am["nb_local"][i], residuos[:, i] = nb_harmonico(y[u], passos, rng, n0[i])
        if u in AGREGADOS:
            for nome, f in (("ets", ets), ("arima_fourier", arima_fourier)):
                try:
                    am[nome][i] = f(y[u], passos, rng)
                except Exception as e:  # noqa: BLE001 - falha pontual: modelo ausente nesta origem
                    log.warning("%s %s %s: %s", nome, u, origem.date(), e)
    i_uf = [ORDEM.index(u) for u in UFS]
    am["nb_bottom_up"] = bottom_up(am["nb_local"][i_uf])
    am["nb_mint"] = mint(am["nb_local"], residuos)
    conj = schaake(am["nb_local"], residuos, rng)          # amostras conjuntas entre unidades (§11.6)
    am["nb_bottom_up_conj"] = bottom_up(conj[i_uf])
    am["nb_mint_conj"] = mint(conj, residuos)
    # shortcut: molde de dependência do LightGBM = resíduos do NB local; trocar por resíduos próprios fora da amostra
    am["lgbm_global"] = bottom_up(schaake(lgbm_global(y, passos, rng, n0), residuos[:, i_uf], rng))
    if pop is not None:
        am["lgbm_taxa"] = bottom_up(schaake(lgbm_global(y, passos, rng, n0, pop), residuos[:, i_uf], rng))
    am["nowcast"] = vazio()
    am["nowcast"][:, :, DESCARTE_RECENTE - 1] = nc[:, -1, :]

    q = {m: np.quantile(a, QUANTIS, axis=1) for m, a in am.items()}   # quantis × 33 × passos
    q["ensemble"] = np.nanmean(np.stack([q[m] for m in MODELOS_ENSEMBLE]), axis=0)
    linhas = []
    for m, a in q.items():
        for i, u in enumerate(ORDEM):
            for k in range(passos):
                if np.isnan(a[0, i, k]):
                    continue
                linhas.append((m, NIVEL[u], u, origem, k - DESCARTE_RECENTE + 1, alvos_sem[k], *a[:, i, k]))
    return pd.DataFrame(linhas, columns=["modelo", "nivel", "unidade", "origem", "h", "alvo", *COLS_Q])


def backtest(origens: list[pd.Timestamp]) -> pd.DataFrame:
    import vsr_nowcast as vn
    cub = vn.cubo()
    pop = populacao_semanal()
    clim = pd.read_parquet(P / "climatologia.parquet", filters=[
        ("criterio_uf", "==", "residencia"), ("faixa_etaria", "==", "00-23m"), ("serie", "==", SERIE),
        ("tipo", "==", "expanding")])
    pain = pd.read_parquet(P / "painel_semanal.parquet", columns=["semana", "temporada", "semana_temporada"],
                           filters=[("criterio_uf", "==", "residencia"), ("unidade", "==", "BR"),
                                    ("faixa_etaria", "==", "00-23m")]).set_index("semana")
    partes = []
    for j, o in enumerate(origens):
        partes.append(prever_origem(cub, clim, pain, o, np.random.default_rng(SEMENTE + j), pop))
        if j % 10 == 0:
            log.info("origem %s (%d/%d)", o.date(), j + 1, len(origens))
    return pd.concat(partes, ignore_index=True)


# --------------------------------------------------------------------------- marcos previstos
# LightGBM (direto, h ≤ 5) e ETS (sem sazonalidade) não geram uma temporada inteira
MODELOS_MARCOS = ("naive_sazonal", "climatologia", "nb_local", "arima_fourier", "forma_curva")
MARCOS = ("inicio", "pico", "fim")
COLS_P = [f"p{k}" for k in range(1, 54)]


def domingo_se1(ano: int) -> pd.Timestamp:
    """Domingo da SE 1 do ano epidemiológico (semana da 1ª quarta-feira)."""
    j = pd.Timestamp(ano, 1, 1)
    return j + pd.Timedelta(days=(2 - j.weekday()) % 7 - 3)


def semanas_temporada(t: int) -> pd.DatetimeIndex:
    return pd.date_range(domingo_se1(t), domingo_se1(t + 1) - pd.Timedelta(weeks=1), freq="7D")


TAUS = np.arange(-10, 11)                  # deslocamento (semanas)
ESCALAS = np.array([0.8, 0.9, 1.0, 1.12, 1.25])


def molde_temporadas(y: pd.Series, t: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Temporadas < t inteiras em `y`: curvas normalizadas (n × 52), totais e semanas de pico (MM3)."""
    curvas, tot, pico = [], [], []
    for tt in range(t - 10, t):
        sem = semanas_temporada(tt)[:52]
        if sem[0] < y.index[0] or sem[-1] > y.index[-1]:
            continue
        v = y.reindex(sem).to_numpy()
        if v.sum() > 0:
            curvas.append(v / v.sum()); tot.append(v.sum())
            pico.append(np.convolve(v, np.ones(3) / 3, "same").argmax() + 1)
    return np.array(curvas), np.array(tot), np.array(pico)


def forma_curva(y: pd.Series, y_molde: pd.Series, t: int, k_obs: int, passos: int, rng) -> np.ndarray:
    """Curva da temporada t como molde deslocado e esticado: μ_w = A · c(w_p + (w − w_p − τ)/s).

    c = média das curvas normalizadas das temporadas anteriores de `y_molde` (MM3); w_p = pico do molde.
    A ~ Gama(a0, b0) com média e CV (≥ 0,3) dos totais anteriores de `y` ⇒ verossimilhança marginal
    Poisson-Gama fechada para cada (τ, s), temperada pela dispersão de Pearson φ do melhor ajuste
    (quase-verossimilhança). Prior τ ~ N(0, σ_τ²), σ_τ = dp dos picos anteriores (≥ 2); s uniforme.
    Trajetórias das semanas k_obs+1 … k_obs+passos: (τ, s) da posteriori na grade, A | τ, s da Gama
    posteriori, contagens NB1 com variância φμ. Devolve N × passos.
    """
    curvas, _, pico = molde_temporadas(y_molde, t)
    _, tot, _ = molde_temporadas(y, t)
    if len(curvas) == 0 or tot.sum() == 0:
        return np.zeros((N_AMOSTRAS, passos))
    c = np.convolve(curvas.mean(0), np.ones(3) / 3, "same") + 1e-6
    wp = c.argmax() + 1.0
    w = np.arange(1, k_obs + passos + 1, dtype=float)
    G = np.array([[np.interp(wp + (w - wp - ta) / s, np.arange(1, 53), c) for s in ESCALAS] for ta in TAUS])
    G /= c.sum()                                               # τ × s × semanas; soma ≈ 1 na temporada
    m, cv = tot.mean(), max(tot.std() / tot.mean() if len(tot) > 1 else 0.0, 0.3)
    a0, b0 = 1 / cv ** 2, 1 / (cv ** 2 * m)
    yo = y.iloc[-k_obs:].to_numpy() if k_obs else np.zeros(0)
    Go = G[..., :k_obs]
    sy, sc = yo.sum(), Go.sum(-1)
    ll = (yo * np.log(Go)).sum(-1) - (a0 + sy) * np.log(b0 + sc)   # termos constantes em (τ, s) omitidos
    i, j = np.unravel_index(ll.argmax(), ll.shape)
    mu_b = (a0 + sy) / (b0 + sc[i, j]) * Go[i, j]
    phi = max(float(((yo - mu_b) ** 2 / np.maximum(mu_b, 1e-9)).sum() / max(k_obs - 2, 1)), 1.0) if k_obs > 2 else 1.0
    st = max(pico.std() if len(pico) > 1 else 0.0, 2.0)
    lp = ll / phi - 0.5 * (TAUS[:, None] / st) ** 2
    pr = np.exp(lp - lp.max()).ravel(); pr /= pr.sum()
    k = rng.choice(pr.size, N_AMOSTRAS, p=pr)
    ii, jj = np.unravel_index(k, ll.shape)
    A = rng.gamma(a0 + sy / phi, 1 / (b0 + sc[ii, jj] / phi))
    mu = A[:, None] * G[ii, jj, k_obs:]
    if phi <= 1.01:
        return rng.poisson(mu).astype(float)
    return rng.negative_binomial(np.maximum(mu / (phi - 1), 1e-9), 1 / phi).astype(float)


def prever_marcos_origem(cub: tuple[np.ndarray, pd.DatetimeIndex], clim: pd.DataFrame, origem: pd.Timestamp,
                         rng) -> pd.DataFrame:
    """Distribuição de início, pico e fim da temporada corrente na origem.

    Trajetória = versão da origem até o − D, amostra i do nowcast em o − D … o, amostras do modelo até o fim
    da temporada (NB parte da amostra i do nowcast); os marcos de cada amostra seguem vsr_comum.marcos_amostras. `ensemble` é a
    mistura, com pesos iguais, das amostras dos modelos disponíveis na unidade.
    """
    y, nc = serie_origem(cub, origem, rng)
    t = (origem + pd.Timedelta(days=3)).year                 # temporada = ano epidemiológico (início na SE 1)
    sem = semanas_temporada(t)
    k_obs = int((sem <= y.index[-1]).sum())
    pos = pd.date_range(origem - pd.Timedelta(weeks=D), origem, freq="7D").get_indexer(sem[:k_obs])
    rec = pos >= 0                                           # semanas da temporada cobertas pelo nowcast
    passos = len(sem) - k_obs
    alvos = pd.DataFrame({"temporada": t, "semana_temporada": np.arange(k_obs + 1, len(sem) + 1)})
    linhas = []
    for i, u in enumerate(ORDEM):
        am = {"naive_sazonal": naive_sazonal(y[u], passos, rng), "climatologia": climatologia(clim, u, alvos, rng),
              "nb_local": nb_harmonico(y[u], passos, rng, nc[i, -1 - DESCARTE_RECENTE])[0]}
        # molde da UF com poucos casos nas temporadas anteriores: o da região
        mol = y[u] if u in AGREGADOS or molde_temporadas(y[u], t)[1].sum() >= 300 else y[REGIAO_UF[u]]
        am["forma_curva"] = forma_curva(y[u], mol, t, k_obs, passos, rng)
        if u in AGREGADOS:
            try:
                am["arima_fourier"] = arima_fourier(y[u], passos, rng)
            except Exception as e:  # noqa: BLE001
                log.warning("arima_fourier %s %s: %s", u, origem.date(), e)
        am["ensemble"] = np.vstack(list(am.values()))
        obs = np.tile(y[u].reindex(sem[:k_obs]).to_numpy(), (N_AMOSTRAS, 1))
        obs[:, rec] = nc[i][pos[rec]].T
        for m, a in am.items():
            mk = marcos_amostras(np.hstack([np.tile(obs, (len(a) // N_AMOSTRAS, 1)), a]))
            for nome in MARCOS:
                v = mk[nome]
                ok = ~np.isnan(v)
                p = np.bincount(v[ok].astype(int), minlength=54)[1:54] / len(v)
                linhas.append((m, NIVEL[u], u, origem, t, k_obs + 1, nome, *p, 1 - ok.mean()))
    return pd.DataFrame(linhas, columns=["modelo", "nivel", "unidade", "origem", "temporada", "semana_origem",
                                         "marco", *COLS_P, "p_nan"])


def _contexto():
    import vsr_nowcast as vn
    cub = vn.cubo()
    clim = pd.read_parquet(P / "climatologia.parquet", filters=[
        ("criterio_uf", "==", "residencia"), ("faixa_etaria", "==", "00-23m"), ("serie", "==", SERIE),
        ("tipo", "==", "expanding")])
    return cub, clim


def backtest_marcos(origens: list[pd.Timestamp]) -> pd.DataFrame:
    cub, clim = _contexto()
    partes = []
    for j, o in enumerate(origens):
        partes.append(prever_marcos_origem(cub, clim, o, np.random.default_rng(SEMENTE + 10_000 + j)))
        if j % 10 == 0:
            log.info("marcos: origem %s (%d/%d)", o.date(), j + 1, len(origens))
    return pd.concat(partes, ignore_index=True)


def pontuar_marcos(prev: pd.DataFrame, verdade: pd.DataFrame) -> pd.DataFrame:
    """RPS (CRPS discreto) em semanas, erro absoluto da mediana, P(|erro| ≤ 1), cobertura de 80%.

    `verdade`: nivel, unidade, temporada, marco, semana_real. A massa em NaN (trajetória toda nula)
    fica sem semana e é penalizada no RPS. `antecedencia` = semana_real − semana_origem (> 0: evento futuro).
    """
    x = prev.merge(verdade, on=["nivel", "unidade", "temporada", "marco"], how="inner")
    Pm = x[COLS_P].to_numpy()
    F = np.cumsum(Pm, 1)
    y = x["semana_real"].to_numpy().astype(int)
    w = np.arange(1, 54)
    x["rps"] = ((F - (w[None, :] >= y[:, None])) ** 2).sum(1)
    q = lambda a: np.where((F >= a).any(1), (F >= a).argmax(1) + 1.0, np.nan)  # noqa: E731
    x["mediana"], q10, q90 = q(0.5), q(0.1), q(0.9)
    x["ea_mediana"] = np.abs(x["mediana"] - y)
    x["p_1sem"] = (Pm * (np.abs(w[None, :] - y[:, None]) <= 1)).sum(1)
    x["cob80"] = (q10 <= y) & (y <= q90)
    x["antecedencia"] = y - x["semana_origem"]
    return x.drop(columns=COLS_P)


def ensemble_ponderado(prev: pd.DataFrame, sc: pd.DataFrame) -> pd.DataFrame:
    """`ensemble_pond`: mistura das distribuições dos modelos de MODELOS_MARCOS com pesos ∝ 1/RPS médio do
    modelo nas temporadas anteriores (antecedência 1–8, todas as unidades, por marco); sem histórico, pesos
    iguais. Pesos renormalizados entre os modelos disponíveis na unidade. Sem vazamento: só temporadas < t."""
    hist = sc.loc[sc["antecedencia"].between(1, 8) & sc["modelo"].isin(MODELOS_MARCOS)]
    base = prev.loc[prev["modelo"].isin(MODELOS_MARCOS)]
    partes = []
    for (t, mc), g in base.groupby(["temporada", "marco"]):
        r = hist.loc[(hist["temporada"] < t) & (hist["marco"] == mc)].groupby("modelo")["rps"].mean()
        w = pd.Series(1.0, g.index) if r.empty else g["modelo"].map(1 / r).fillna((1 / r).mean())
        chave = ["nivel", "unidade", "origem", "temporada", "semana_origem", "marco"]
        g = g.assign(_w=w / w.groupby([g[c] for c in chave]).transform("sum"))
        cols = COLS_P + ["p_nan"]
        mix = g[cols].mul(g["_w"], axis=0).groupby([g[c] for c in chave]).sum().reset_index()
        partes.append(mix.assign(modelo="ensemble_pond"))
    return pd.concat(partes, ignore_index=True)[prev.columns]


# --------------------------------------------------------------------------- avaliação
def wis(qs: np.ndarray, y: np.ndarray) -> np.ndarray:
    """WIS = (2/K) Σ_k ρ_τk(y − q_k) (Bracher et al., 2021, com os 23 quantis); qs: n × K."""
    d = y[:, None] - qs
    return 2 * np.mean(np.maximum(QUANTIS * d, (QUANTIS - 1) * d), axis=1)


def pontuar(prev: pd.DataFrame, obs: pd.DataFrame) -> pd.DataFrame:
    """Junta o observado final (`obs`: unidade, semana, vsr) e calcula WIS natural e log, cobertura."""
    x = prev.merge(obs.rename(columns={"semana": "alvo", "vsr": "obs"}), on=["unidade", "alvo"], how="inner")
    qs, y = x[COLS_Q].to_numpy(), x["obs"].to_numpy()
    x["wis"] = wis(qs, y)
    x["wis_log"] = wis(np.log1p(np.clip(qs, 0, None)), np.log1p(y))   # Bosse et al. (2023)
    x["ea_mediana"] = np.abs(x["q0.5"] - y)
    x["cob50"] = (x["q0.25"] <= y) & (y <= x["q0.75"])
    x["cob90"] = (x["q0.05"] <= y) & (y <= x["q0.95"])
    return x.drop(columns=COLS_Q)


def wis_relativo(sc: pd.DataFrame, por: list[str], base: str = "naive_sazonal", col: str = "wis_log",
                 n_boot: int = 1000, semente: int = SEMENTE) -> pd.DataFrame:
    """WIS médio de cada modelo ÷ do baseline no conjunto comum de alvos, com IC 95% por bootstrap
    de origens (blocos semanais). Com sobreposição completa, igual ao WIS relativo pareado de
    Cramer et al. (2022): as médias geométricas sobre os outros modelos se cancelam."""
    chave = ["nivel", "unidade", "origem", "h"]
    w = sc.pivot_table(index=chave + [c for c in por if c not in chave], columns="modelo", values=col).reset_index()
    rng = np.random.default_rng(semente)
    out = []
    for g, d in w.groupby(por):
        mods = [m for m in sc["modelo"].unique() if m in d and d[m].notna().any()]   # modelos do grupo
        d = d.dropna(subset=mods)                                                   # alvos comuns
        por_or = d.groupby("origem")[mods].sum()
        b = rng.choice(len(por_or), (n_boot, len(por_or)))
        soma = por_or.to_numpy()[b].sum(1)                                          # n_boot × modelos
        rel_b = soma / soma[:, [mods.index(base)]]
        rel = d[mods].mean() / d[base].mean()
        for j, m in enumerate(mods):
            out.append({**dict(zip(por, g)), "modelo": m, "wis_rel": rel[m],
                        "ic_inf": np.quantile(rel_b[:, j], 0.025), "ic_sup": np.quantile(rel_b[:, j], 0.975),
                        "n": len(d)})
    return pd.DataFrame(out)


if __name__ == "__main__":   # autotestes
    y = np.array([3.0, 10.0])
    pontual = np.repeat(np.array([[5.0], [5.0]]), len(QUANTIS), 1)
    assert np.allclose(wis(pontual, y), np.abs(y - 5)), "WIS de previsão pontual = erro absoluto"
    rng = np.random.default_rng(0)
    base = rng.poisson(5, (len(UFS), 50, 2)).astype(float)
    coer = bottom_up(base)
    assert np.allclose(mint(coer, rng.normal(size=(100, len(ORDEM)))), coer), "MinT preserva o coerente"
    # Schaake: marginais intactas e correlação de postos entre unidades ≈ a do molde
    R = rng.multivariate_normal([0, 0], [[1, 0.8], [0.8, 1]], 300)
    a = rng.gamma(2.0, 3.0, (2, 400, 1))
    b = schaake(a, R, rng)
    assert np.allclose(np.sort(b, 1), np.sort(a, 1))
    rk = lambda x: x.argsort().argsort()  # noqa: E731
    assert abs(np.corrcoef(rk(b[0, :, 0]), rk(b[1, :, 0]))[0, 1] - 0.79) < 0.1
    # atraso todo em d = 0 ⇒ F = 1: a série da origem reproduz a versão e o nowcast não tem incerteza
    sem = pd.date_range("2021-01-03", periods=200, freq="7D")
    A = np.zeros((len(ORDEM), 200, D + 1)); A[:, :, 0] = rng.poisson(5, (len(ORDEM), 200))
    ys, nc = serie_origem((A, sem), sem[-1], rng)
    fim = 200 - DESCARTE_RECENTE
    assert np.allclose(ys.to_numpy().T, A[:, fim - len(ys):fim, 0]) and np.ptp(nc, 2).max() == 0
    # ensemble_pond: sem histórico = média simples; massa total preservada
    pv = pd.DataFrame([{"modelo": m, "nivel": "BRASIL", "unidade": "BR", "origem": pd.Timestamp("2024-02-04"), "temporada": 2024,
                        "semana_origem": 5, "marco": "pico", **dict(zip(COLS_P, np.eye(53)[k])), "p_nan": 0.0}
                       for m, k in (("naive_sazonal", 9), ("nb_local", 19))])
    ep = ensemble_ponderado(pv, pv.head(0).assign(antecedencia=[], rps=[]))
    assert np.isclose(ep.loc[0, "p10"], 0.5) and np.isclose(ep.loc[0, "p20"], 0.5)
    # forma_curva: temporada deslocada em τ = 3 é recuperada depois do pico
    idx = pd.date_range(domingo_se1(2021), domingo_se1(2025), freq="7D")
    st = np.array([(x + pd.Timedelta(days=3)).year for x in idx])
    w = np.array([(x - domingo_se1(t)).days // 7 + 1 for x, t in zip(idx, st)])
    lam = 0.5 + np.where(st < 2024, 1500, 2000) * np.exp(-0.5 * ((w - 20 - 3 * (st == 2024)) / 5) ** 2) / 12.5
    yf = pd.Series(rng.poisson(lam).astype(float), idx).loc[:domingo_se1(2024) + pd.Timedelta(weeks=24)]
    tr = forma_curva(yf, yf, 2024, 25, 27, rng)
    pk = marcos_amostras(np.hstack([np.tile(yf.iloc[-25:].to_numpy(), (N_AMOSTRAS, 1)), tr]))["pico"]
    assert abs(np.median(pk) - 23) <= 1, np.median(pk)
    print("ok")
