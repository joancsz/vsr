"""População de menores de 2 anos por UF e ano a partir do SINASC (OpenDataSUS).

pop_menores2(a) = NV(a − 1) + NV(a − 2), nascidos vivos por UF de residência da mãe (CODMUNRES) e ano
de nascimento (arquivo do ano). Sem mortalidade infantil (~1,3% de superestimação) nem migração.
Saída: data/external/populacao.csv (uf, ano, pop_menores2) + data/external/qc_populacao.csv.
Uso: python baixar_sinasc.py [ano_ini ano_fim]  (anos do painel; padrão 2019 2026).
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parent
BRUTO = RAIZ / "data" / "raw" / "SINASC"
SAIDA = RAIZ / "data" / "external"
S3 = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br"
HDR = {"User-Agent": "Mozilla/5.0 (vsr-pipeline)"}
UF_IBGE = {11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO", 21: "MA", 22: "PI", 23: "CE",
           24: "RN", 25: "PB", 26: "PE", 27: "AL", 28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP",
           41: "PR", 42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF"}


def baixar(ano: int) -> Path:
    """Baixa SINASC_{ano}_csv.zip (cache: pula se o tamanho local = Content-Length)."""
    url = f"{S3}/SINASC/csv/SINASC_{ano}_csv.zip"
    dest = BRUTO / Path(url).name
    tam = int(requests.head(url, headers=HDR, timeout=60).headers.get("Content-Length", -1))
    if dest.exists() and dest.stat().st_size == tam:
        return dest
    BRUTO.mkdir(parents=True, exist_ok=True)
    parcial = dest.with_suffix(".part")
    with requests.get(url, headers=HDR, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(parcial, "wb") as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
    if parcial.stat().st_size != tam:
        raise RuntimeError(f"{url}: tamanho {parcial.stat().st_size} ≠ {tam}")
    parcial.replace(dest)
    return dest


def nascidos_por_uf(arq: Path) -> pd.Series:
    with zipfile.ZipFile(arq) as z:
        nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(nome) as f:
            d = pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", usecols=["CODMUNRES"], dtype=str)
    uf = pd.to_numeric(d["CODMUNRES"].str.strip('"').str[:2], errors="coerce").map(UF_IBGE)
    return uf.value_counts(dropna=False).rename_axis("uf")


def main(ano_ini: int = 2019, ano_fim: int = 2026) -> pd.DataFrame:
    nv, qc = {}, []
    for a in range(ano_ini - 2, ano_fim):
        try:
            c = nascidos_por_uf(baixar(a))
        except requests.HTTPError as e:
            print(f"SINASC {a} indisponível: {e}")
            continue
        qc.append({"ano_nascimento": a, "nascidos": int(c.sum()), "sem_uf": int(c[c.index.isna()].sum())})
        nv[a] = c[c.index.notna()]
    disp = sorted(nv)
    linhas = []
    for a in range(ano_ini, ano_fim + 1):
        # shortcut: ano de nascimento sem arquivo usa o último disponível; trocar quando o SINASC publicar
        anos = [max(x for x in disp if x <= a - k) for k in (1, 2)]
        pop = nv[anos[0]].add(nv[anos[1]], fill_value=0)
        linhas += [{"uf": u, "ano": a, "pop_menores2": int(v), "anos_nascimento": f"{anos[1]}+{anos[0]}"}
                   for u, v in pop.items()]
    out = pd.DataFrame(linhas).sort_values(["ano", "uf"])
    assert out.groupby("ano")["uf"].nunique().eq(27).all(), "faltam UFs"
    SAIDA.mkdir(parents=True, exist_ok=True)
    out.to_csv(SAIDA / "populacao.csv", index=False, encoding="utf-8")
    pd.DataFrame(qc).to_csv(SAIDA / "qc_populacao.csv", index=False, encoding="utf-8")
    return out


if __name__ == "__main__":
    o = main(*map(int, sys.argv[1:3]))
    print(o.groupby("ano").agg(pop_br=("pop_menores2", "sum"), nascimentos=("anos_nascimento", "first")))
