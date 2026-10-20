"""Validação externa das saídas de 01_dados_srag_vsr.ipynb contra os arquivos brutos.

Complementa qc_validacao.csv (coerência interna) com checagens independentes do pipeline.
Uso: python validar_dados.py  → imprime e grava data/processed/qc_validacao_externa.csv
"""
import glob
import json
import sys

import numpy as np
import pandas as pd

from vsr_comum import verificar_ambiente

sys.stdout.reconfigure(encoding="utf-8")
verificar_ambiente()
RAW, P = "data/raw/SRAG", "data/processed"
CHK: list[dict] = []


def check(nome: str, ok: bool, detalhe="") -> None:
    CHK.append({"verificacao": nome, "ok": bool(ok), "detalhe": str(detalhe)})


casos = pd.read_parquet("data/interim/casos_menores2.parquet")
painel = pd.read_parquet(f"{P}/painel_semanal.parquet")

# 1. Bruto → interim: recontagem independente (< 24 meses, HOSPITAL = 1)
cols = ["NU_IDADE_N", "TP_IDADE", "HOSPITAL", "PCR_VSR", "AN_VSR", "PCR_RESUL", "POS_PCROUT"]
raw = []
for f in sorted(glob.glob(f"{RAW}/*/INFLUD*.parquet")):
    r = pd.read_parquet(f, columns=cols).apply(pd.to_numeric, errors="coerce")
    meses = np.select([r.TP_IDADE == 1, r.TP_IDADE == 2, r.TP_IDADE == 3],
                      [r.NU_IDADE_N / 30.4375, r.NU_IDADE_N, r.NU_IDADE_N * 12], np.nan)
    raw.append(r[(meses < 24) & (r.HOSPITAL == 1)].assign(ano=int(f.split("INFLUD")[1][:2]) + 2000))
raw = pd.concat(raw)
n_raw = raw.groupby("ano").size()
n_int = casos.groupby("arquivo_ano").size()
perda = ((n_raw - n_int) / n_raw).max()
check("bruto→interim: perda por ano < 0,2%", perda < 0.002, f"máx {perda:.4%}")
v_raw = raw[(raw.PCR_VSR == 1) | (raw.AN_VSR == 1)].groupby("ano").size()
check("bruto→interim: VSR por ano (dif. ≤ 25)", (v_raw - casos.groupby("arquivo_ano").vsr.sum()).abs().max() <= 25)
check("arquivo do ano Y contém só ano_epi Y", (casos.arquivo_ano == casos.ano_epi).all())

# 2. interim → painel: recontagem por UF de residência × semana
uf = painel.query("criterio_uf == 'residencia' and nivel == 'UF' and faixa_etaria == '00-23m'")
g = casos.groupby(["uf_residencia", "semana"]).agg(srag_c=("vsr", "size"), vsr_c=("vsr", "sum")).reset_index()
m = uf.merge(g, left_on=["unidade", "semana"], right_on=["uf_residencia", "semana"], how="left").fillna(0)
check("interim→painel: srag e vsr idênticos", ((m.srag != m.srag_c) | (m.vsr != m.vsr_c)).sum() == 0)
sem = painel.semana.drop_duplicates().sort_values()
check("semanas contíguas (7 dias)", (sem.diff().dropna().dt.days == 7).all(), f"{sem.min().date()}–{sem.max().date()}")

# 3. Semana 53 não pode colidir com a 52 no índice da temporada
br = painel.query("criterio_uf == 'residencia' and unidade == 'BR' and faixa_etaria == '00-23m'")
dup = br.duplicated(["temporada", "semana_temporada"]).sum()
check("(temporada, semana_temporada) único", dup == 0,
      f"{dup} colisões; temporadas com SE53: {sorted(map(int, br.loc[br.semana_epi == 53, 'ano_epi'].unique()))}")

clim = pd.read_parquet(f"{P}/climatologia.parquet",
                       filters=[("unidade", "==", "BR"), ("semana_temporada", "==", 53)])
check("climatologia tem SE 53 em todas as temporadas-alvo", clim.groupby(["tipo", "temporada_alvo"]).size().min() > 0,
      f"n_temporadas da SE 53: {sorted(map(int, clim.n_temporadas.unique()))} (0 = média das SE 52 e 1)")

# 4. POS_PCROUT só é preenchido com PCR detectável ⇒ vsr_est_sup é limite superior; exige vsr ≤ inf ≤ sup
frac = raw.loc[raw.PCR_RESUL == 2, "POS_PCROUT"].notna().mean()
viol = (painel.vsr_est_inf > painel.vsr_est_sup + 1e-9).mean()
check("vsr ≤ vsr_est_inf ≤ vsr_est_sup", viol == 0 and (painel.vsr_est_inf >= painel.vsr - 1e-9).all(),
      f"inf > sup em {viol:.2%} das linhas; POS_PCROUT preenchido em {frac:.1%} dos PCR_RESUL=2")

# 5. Painel termina na última semana completa
corte = pd.Timestamp(json.load(open(f"{P}/metadata.json", encoding="utf-8"))["data_corte"])
check("última semana do painel completa (sábado ≤ corte)", sem.max() + pd.Timedelta(days=6) <= corte,
      f"última semana {sem.max().date()}, corte {corte.date()}")

# 6. Datas: atrasos negativos são truncados em 0 no triângulo (contados em metadata.qc_atraso)
d = (casos.dt_disp_registro - casos.dt_sin_pri).dt.days
check("disponibilidade < sintomas em < 0,1% dos casos", (d < 0).mean() < 0.001, f"{(d < 0).sum()} casos")

out = pd.DataFrame(CHK)
out.to_csv(f"{P}/qc_validacao_externa.csv", index=False)
print(out.to_string(index=False))
