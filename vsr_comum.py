"""Funções comuns aos notebooks de EDA e modelagem do projeto VSR.

- verificar_ambiente: confere as versões instaladas contra environment.yml.
- carregar_painel: leitura filtrada do painel semanal.
- marcos_temporada / janela_minima / fracao_na_janela: definições a priori de início, pico e fim
  da temporada e da cobertura de janelas de imunização (ver docs/METODOLOGIA.md, seção 10.1).
- mem_periodo / mem_limiar / inicio_fim_limiar: definições alternativas (MEM, limiar de taxa; §10.3).
"""
from __future__ import annotations

import json
import os
import re
import sys
from importlib import metadata
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

RAIZ = Path(__file__).resolve().parent
P = RAIZ / "data" / "processed"

# Definições a priori (fixadas antes de olhar os resultados de modelagem)
Q_INICIO, Q_FIM = 0.10, 0.90      # início/fim: fração acumulada da temporada
JANELA_MM = 3                     # média móvel centrada para o pico
JANELA_SUS = (2, 8)               # fevereiro a agosto (meses, inclusivo)
MEM_DELTA = 0.028                 # critério de inclinação do MEM (Vega et al., 2013): ganho mínimo de 2,8%
MEM_N_TOTAL = 30                  # valores pré-epidêmicos no total, repartidos entre as temporadas


def verificar_ambiente(arquivo: Path = RAIZ / "environment.yml") -> dict[str, str]:
    """Erro se alguma versão instalada diferir da fixada em environment.yml."""
    fixadas = dict(re.findall(r"^\s*-\s*([\w-]+)=([\w.]+)\s*$", arquivo.read_text(encoding="utf-8"), re.M))
    instaladas = {"python": sys.version.split()[0]}
    for pacote in fixadas:
        if pacote in ("python", "jupyter"):   # jupyter é metapacote sem versão própria importável
            continue
        try:
            instaladas[pacote] = metadata.version(pacote)
        except metadata.PackageNotFoundError:
            instaladas[pacote] = "ausente"
    difere = {p: (fixadas[p], v) for p, v in instaladas.items() if v != fixadas[p]}
    if difere:
        # Uso estritamente experimental: permite reproduzir o pipeline com o runtime
        # local, mantendo as versões efetivamente registradas nos metadados de saída.
        if os.environ.get("VSR_ALLOW_FLEX_ENV") == "1":
            return instaladas
        raise RuntimeError("ambiente difere de environment.yml (fixada, instalada): "
                           f"{difere}. Recrie com: conda env create -f environment.yml")
    return instaladas


def metadados() -> dict:
    return json.loads((P / "metadata.json").read_text(encoding="utf-8"))


def carregar_painel(criterio: str = "residencia", faixa: str = "00-23m") -> pd.DataFrame:
    return pd.read_parquet(P / "painel_semanal.parquet",
                           filters=[("criterio_uf", "==", criterio), ("faixa_etaria", "==", faixa)])


def marcos_amostras(Y: np.ndarray) -> dict[str, np.ndarray]:
    """Marcos de várias trajetórias de uma temporada; Y: amostras × semanas (coluna k−1 = semana k).

    pico   = argmax (1º em empate) da média móvel centrada de JANELA_MM semanas (bordas: janela truncada);
    inicio = 1ª semana com fração acumulada ≥ Q_INICIO; fim = 1ª com fração acumulada ≥ Q_FIM.
    Relativas ao total da temporada ⇒ invariantes à escala de averiguação. Total 0 ⇒ NaN.
    """
    Y = np.nan_to_num(np.asarray(Y, dtype=float))
    K, m = Y.shape[1], JANELA_MM // 2
    tot = Y.sum(1)
    c = np.pad(np.cumsum(Y, 1), ((0, 0), (1, 0)))
    acum = c[:, 1:] / np.where(tot > 0, tot, 1.0)[:, None]
    j = np.arange(K)
    lo, hi = np.maximum(j - m, 0), np.minimum(j + m, K - 1)
    mm = (c[:, hi + 1] - c[:, lo]) / (hi - lo + 1)
    out = {"inicio": (acum >= Q_INICIO).argmax(1) + 1.0, "pico": mm.argmax(1) + 1.0,
           "fim": (acum >= Q_FIM - 1e-12).argmax(1) + 1.0, "pico_valor": mm.max(1)}
    for v in out.values():
        v[tot <= 0] = np.nan
    return out | {"total": tot}


def marcos_temporada(y: pd.Series) -> dict:
    """Marcos de uma temporada observada; `y` indexada por semana_temporada 1..K, em ordem."""
    assert (y.index == np.arange(1, len(y) + 1)).all(), "semana_temporada deve ser 1..K contígua"
    return {k: (float(v[0]) if k in ("total", "pico_valor") or np.isnan(v[0]) else int(v[0]))
            for k, v in marcos_amostras(y.to_numpy()[None, :]).items()}


def janela_minima(y: pd.Series, cobertura: float = 0.9) -> dict:
    """Menor janela contígua (em semanas) que contém ≥ `cobertura` dos casos da temporada."""
    v = y.astype(float).fillna(0.0).to_numpy()
    total = v.sum()
    if total <= 0:
        return {"janela_ini": np.nan, "janela_dur": np.nan}
    c = np.concatenate([[0.0], np.cumsum(v)])
    soma = c[None, 1:] - c[:-1, None]            # soma[i, j] = casos nas semanas i..j
    ok = np.triu(soma >= cobertura * total - 1e-9)
    dur = np.where(ok, np.arange(len(v))[None, :] - np.arange(len(v))[:, None] + 1, len(v) + 1)
    i, j = np.unravel_index(dur.argmin(), dur.shape)
    return {"janela_ini": int(y.index[i]), "janela_dur": int(dur[i, j])}


def fracao_na_janela(semana: pd.Series, y: pd.Series, meses: tuple[int, int] = JANELA_SUS) -> float:
    """Fração dos casos com semana de sintomas (mês da quarta-feira) dentro de `meses`."""
    mes = (pd.to_datetime(semana) + pd.Timedelta(days=3)).dt.month
    total = y.sum()
    return float(y[mes.between(*meses)].sum() / total) if total > 0 else np.nan


def mem_periodo(y: np.ndarray, delta: float = MEM_DELTA) -> tuple[int, int]:
    """Período epidêmico (índices 0-based, inclusivos): a janela de duração r com a maior soma, com r = menor
    duração em que alongar uma semana acrescenta < delta do total (curva do máximo percentual acumulado)."""
    tot = y.sum()
    c = np.r_[0.0, np.cumsum(y)]
    melhor = lambda r: int(np.argmax(c[r:] - c[:-r]))  # noqa: E731
    p_ant = 0.0
    for r in range(1, len(y) + 1):
        p = (c[r:] - c[:-r]).max() / tot
        if r > 1 and p - p_ant < delta:
            i = melhor(r - 1)
            return i, i + r - 2
        p_ant = p
    return 0, len(y) - 1
def mem_limiar(curvas: list[np.ndarray], delta: float = MEM_DELTA, n_total: int = MEM_N_TOTAL) -> float:
    """Limiar pré-epidêmico do MEM: limite superior do IC 95% unilateral da média geométrica (log1p) dos n
    maiores valores pré-epidêmicos de cada temporada, n = round(n_total / temporadas).

    shortcut: média geométrica com IC t de Student em log1p, não as opções do pacote R `mem`; validar contra
    `mem::memmodel` se o MEM virar definição principal.
    """
    n = max(round(n_total / len(curvas)), 1)
    pre = []
    for y in curvas:
        if y.sum() == 0:
            continue
        i, _ = mem_periodo(y, delta)
        pre += list(np.sort(y[:i])[-n:])
    x = np.log1p(np.asarray(pre, float))
    if len(x) < 2:
        return np.nan
    return float(np.expm1(x.mean() + stats.t.ppf(0.95, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))))
def inicio_fim_limiar(y: np.ndarray, limiar: float) -> tuple[float, float]:
    """Semanas (1-based) da 1ª e da última semana com y ≥ limiar; NaN se nenhuma."""
    acima = np.flatnonzero(y >= limiar)
    return (float(acima[0] + 1), float(acima[-1] + 1)) if len(acima) and np.isfinite(limiar) else (np.nan, np.nan)


if __name__ == "__main__":   # autoteste das definições com uma temporada sintética
    s = pd.Series(np.exp(-0.5 * ((np.arange(1, 53) - 20) / 4) ** 2), index=range(1, 53))
    m = marcos_temporada(s)
    assert m["pico"] == 20 and m["inicio"] < 20 < m["fim"], m
    assert abs(m["inicio"] - (20 - 1.2816 * 4)) <= 1 and abs(m["fim"] - (20 + 1.2816 * 4)) <= 1, m
    j = janela_minima(s, 0.9)
    assert j["janela_dur"] in (13, 14) and 13 <= j["janela_ini"] <= 14, j
    assert np.isnan(marcos_temporada(s * 0)["pico"])
    # MEM: período epidêmico contém o pico; início < pico < fim pelo limiar
    rng0 = np.random.default_rng(0)
    cur = [rng0.poisson(5 + 400 * np.exp(-0.5 * ((np.arange(1, 53) - 20 - d) / 4) ** 2)).astype(float)
           for d in (-2, 0, 2, 1)]
    for c, d in zip(cur, (-2, 0, 2, 1)):
        i, f = mem_periodo(c)
        assert i < 19 + d < f, (i, f, d)
    ini, fim = inicio_fim_limiar(cur[1], mem_limiar(cur))
    assert ini < 20 < fim, (ini, fim)
    # vetorizado = referência pandas (rolling centrado) em contagens com empates
    rng = np.random.default_rng(1)
    Y = rng.poisson(rng.uniform(0, 20, 53), (300, 53)).astype(float)
    ma = marcos_amostras(Y)
    for i in range(300):
        y = pd.Series(Y[i], index=range(1, 54))
        mm = y.rolling(JANELA_MM, center=True, min_periods=1).mean().round(9)
        a = (y.cumsum() / y.sum()).to_numpy()
        assert ma["pico"][i] == mm.idxmax() and ma["inicio"][i] == np.argmax(a >= Q_INICIO) + 1, i
        assert ma["fim"][i] == np.argmax(a >= Q_FIM - 1e-12) + 1, i
    print("ok", m, j, verificar_ambiente())
