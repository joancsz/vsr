"""Nowcasting do VSR com incerteza: Gamma-Poisson com passeio aleatório no nível (análogo conjugado de
Bastos et al., 2019, sem INLA) e duas fontes para a distribuição do atraso.

Modelo, por unidade u e semana t com atraso decorrido d = (o − t)/7 na origem o:
    y_t | λ_t ~ Poisson(λ_t F(d))              (casos já disponíveis)
    N_t − y_t | λ_t, ε ~ Poisson(ε λ_t (1 − F(d))),  ε ~ Gamma(φ, φ)   (casos por chegar; φ = ∞: Poisson)
    λ_t ~ Gamma(k, k / m_t),  m_t = λ̂_{t−1} · g_t   (passeio aleatório; g = tendência, opcional)
⇒ λ_t | y_t ~ Gamma(k + y_t, k/m_t + F(d)). k = 0 é a priori plana (Gamma(y + 1, F): chain-ladder
com incerteza). Cada semana é amostrada da sua posteriori marginal (ignora a correlação ao longo do passeio).
Método e resultados: docs/METODOLOGIA.md §6.1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from vsr_modelos import COLS_Q, D, N_AMOSTRAS, NIVEL, ORDEM, QUANTIS, REGIAO_UF, S, UFS, carregar_triangulo

REFS = ["BR", *sorted(set(REGIAO_UF.values()))]       # unidade de referência da distribuição do atraso
REF_IDX = np.array([REFS.index(REGIAO_UF.get(u, u)) for u in ORDEM])
JANELA_ATRASO = 52                                     # semanas (chain-ladder recente)
ANOS_MATURADO = 3
METODO_PADRAO = dict(fonte="janela", k=10.0, tendencia=True, phi=4.0)   # `gamma_rw`, escolhido em 2023 (§6.1)


def cubo() -> tuple[np.ndarray, pd.DatetimeIndex]:
    """A[u, t, d]: casos da unidade u (33, ordem de ORDEM) com sintomas em t e disponíveis com atraso d."""
    tri = carregar_triangulo()
    semanas = pd.date_range(tri["semana"].min(), tri["semana"].max(), freq="7D")
    a = np.zeros((len(semanas), len(UFS), D + 1))
    a[semanas.get_indexer(tri["semana"]), [UFS.index(u) for u in tri["uf"]], tri["atraso"].to_numpy()] = tri["casos"]
    return np.einsum("ij,tjd->itd", S.to_numpy(float), a), semanas


def _sel(semanas: pd.DatetimeIndex, origem: pd.Timestamp, n: int) -> np.ndarray:
    fim = semanas.get_loc(origem)
    return np.arange(max(fim - n + 1, 0), fim + 1)


def F_maturado(A: np.ndarray, semanas: pd.DatetimeIndex, origem: pd.Timestamp) -> np.ndarray:
    """F_r(d) com semanas maturadas (t + D ≤ o) dos ANOS_MATURADO anos anteriores (chain-ladder antigo do 04)."""
    idx = _sel(semanas, origem - pd.Timedelta(weeks=D), 52 * ANOS_MATURADO)
    h = A[[ORDEM.index(r) for r in REFS]][:, idx, :].sum(1)
    return np.cumsum(h, 1) / np.maximum(h.sum(1, keepdims=True), 1e-12)


def F_janela(A: np.ndarray, semanas: pd.DatetimeIndex, origem: pd.Timestamp, n: int = JANELA_ATRASO) -> np.ndarray:
    """F_r(d) por chain-ladder (fatores de desenvolvimento) nas últimas n semanas, incluindo as incompletas."""
    idx = _sel(semanas, origem, n)
    dec = (len(semanas[:semanas.get_loc(origem) + 1]) - 1 - idx)           # semanas decorridas até a origem
    C = np.cumsum(A[[ORDEM.index(r) for r in REFS]][:, idx, :], 2)        # acumulado por atraso
    F = np.ones((len(REFS), D + 1))
    for d in range(D - 1, -1, -1):
        linhas = dec >= d + 1
        num, den = C[:, linhas, d + 1].sum(1), C[:, linhas, d].sum(1)
        F[:, d] = F[:, d + 1] * np.where(num > 0, den / np.maximum(num, 1e-12), 1.0)
    return np.clip(F, 1e-3, 1.0)


def amostras(A: np.ndarray, semanas: pd.DatetimeIndex, origem: pd.Timestamp, F: np.ndarray, k: float,
             tendencia: bool, rng, phi: float = np.inf, n: int = N_AMOSTRAS) -> tuple[np.ndarray, np.ndarray]:
    """Amostras de N_t para as semanas o − D … o: (unidades × D+1 semanas, da mais antiga à origem, × n).

    Devolve também y (unidades × D+1), o conhecido na origem.
    """
    i0 = semanas.get_loc(origem)
    j = np.arange(D, -1, -1)                                     # atraso decorrido, da semana mais antiga à origem
    y = np.stack([A[:, i0 - jj, :jj + 1].sum(1) for jj in j], 1)  # unidades × semanas
    Fd = F[REF_IDX][:, j]                                        # unidades × semanas
    a, b = np.empty_like(y), np.empty_like(y)
    m = np.maximum(y[:, 0], 0.5)
    ant = m.copy()
    for s in range(len(j)):
        if k > 0:
            a[:, s], b[:, s] = k + y[:, s], k / m + Fd[:, s]
        else:
            a[:, s], b[:, s] = y[:, s] + 1, Fd[:, s]
        lam = a[:, s] / b[:, s]
        g = np.clip(lam / np.maximum(ant, 0.5), 2 / 3, 1.5) if tendencia and s > 0 else 1.0
        ant, m = lam, np.maximum(lam * g, 0.5)
    lam = rng.gamma(a[..., None], 1 / b[..., None], size=(*y.shape, n))
    if np.isfinite(phi):     # erro de F e superdispersão do atraso não captados pela Poisson
        lam = lam * rng.gamma(phi, 1 / phi, size=lam.shape)
    return y[..., None] + rng.poisson(lam * (1 - Fd)[..., None]), y


def nowcast_padrao(A: np.ndarray, semanas: pd.DatetimeIndex, origem: pd.Timestamp, rng):
    """`amostras` com METODO_PADRAO (usado por vsr_modelos.serie_origem)."""
    m = METODO_PADRAO
    return amostras(A, semanas, origem, F_janela(A, semanas, origem), m["k"], m["tendencia"], rng, m["phi"])


def prever_origem(A, semanas, origem, metodos: dict[str, dict], rng, d_max: int = 8) -> pd.DataFrame:
    """Quantis do nowcast para d = 0..d_max em cada método {nome: {fonte, k, tendencia[, phi]}}; `sem_correcao` = y."""
    linhas = []
    sem_alvo = [origem - pd.Timedelta(weeks=d) for d in range(D, -1, -1)]
    Fs = {"maturado": F_maturado(A, semanas, origem), "janela": F_janela(A, semanas, origem)}
    y = None
    for nome, cfg in metodos.items():
        am, y = amostras(A, semanas, origem, Fs[cfg["fonte"]], cfg["k"], cfg["tendencia"], rng, cfg.get("phi", np.inf))
        q = np.quantile(am, QUANTIS, axis=2)                     # quantis × unidades × semanas
        for s in range(D - d_max, D + 1):
            for i, u in enumerate(ORDEM):
                linhas.append((nome, NIVEL[u], u, origem, D - s, sem_alvo[s], *q[:, i, s]))
    for s in range(D - d_max, D + 1):
        for i, u in enumerate(ORDEM):
            linhas.append(("sem_correcao", NIVEL[u], u, origem, D - s, sem_alvo[s], *np.repeat(y[i, s], len(QUANTIS))))
    return pd.DataFrame(linhas, columns=["modelo", "nivel", "unidade", "origem", "d", "alvo", *COLS_Q])


if __name__ == "__main__":   # autotestes
    rng = np.random.default_rng(0)
    # 1) F = 1 ⇒ nowcast = conhecido, sem incerteza
    A = np.zeros((len(ORDEM), 60, D + 1)); A[:, :, 0] = 5
    sem = pd.date_range("2024-01-07", periods=60, freq="7D")
    am, y = amostras(A, sem, sem[-1], np.ones((len(REFS), D + 1)), 4.0, True, rng)
    assert np.allclose(am, y[..., None])
    # 2) chain-ladder recupera um atraso constante conhecido
    p = np.array([0.2, 0.5, 0.2, 0.1] + [0.0] * (D - 3))
    A = np.zeros((len(ORDEM), 60, D + 1)); A[:] = 100 * p
    F = F_janela(A, sem, sem[-1], 52)
    assert np.allclose(F[:, :4], np.cumsum(p)[:4]), F[0, :5]
    # 3) a priori plana: média da posteriori ≈ (y + 1)/F
    A = np.zeros((len(ORDEM), 60, D + 1)); A[:, -1, 0] = 20
    Fp = np.ones((len(REFS), D + 1)); Fp[:, 0] = 0.25
    am, y = amostras(A, sem, sem[-1], Fp, 0, False, rng, n=20000)
    assert abs(am[0, -1].mean() - (20 + (21 * 0.75 / 0.25))) < 1.5, am[0, -1].mean()
    print("ok")
