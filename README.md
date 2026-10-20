# RSV season forecasting in Brazil

Weekly probabilistic forecasts of RSV-confirmed severe acute respiratory infection (SARI/SRAG) hospitalizations in children under 2, by state (UF), region and Brazil, plus forecasts of the season milestones (onset, peak, end). The forecasts are evaluated for the decision on the immunization window (nirsevimab / maternal vaccine).

Detailed documentation (in Portuguese):
- [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md) — data requests, processing, models, metrics and results (§0–§14).
- [`docs/DICIONARIO_DADOS.md`](docs/DICIONARIO_DADOS.md) — schema of every table (raw → panel → forecasts → decision).
- [`docs/CONTEXTO_VSR.md`](docs/CONTEXTO_VSR.md) — project history and decisions.

## Data

| Source | What | Where |
|---|---|---|
| SIVEP-Gripe (OpenDataSUS, dataset `srag-2019-a-2026`) | Individual SARI notifications, 2019–2026, one parquet per year, republished weekly | downloaded by `01`, cached in `data/raw/SRAG` |
| SINASC (OpenDataSUS) | Live births by state, used for the under-2 population: pop(a) = births(a−1) + births(a−2) | downloaded by `baixar_sinasc.py` into `data/raw/SINASC` (~1 GB) |

Fixed definitions:
- **Unit:** hospitalized SARI cases (`HOSPITAL = 1`) aged < 2 years, by week of symptom onset (Sunday-start epidemiological weeks), by state of **residence**.
- **Main target `vsr`:** RSV-positive by PCR or antigen. `srag` (all SARI) is used only as a sensitivity analysis.
- **Reporting delay:** availability = typing date (`DT_DIGITA`); delay triangle up to 26 weeks, corrected by nowcasting.
- **Hierarchy:** Brazil → 5 regions → 27 states. States grouped in tiers A/B/C by minimum RSV count in 2023–2025.
- **2020–2021** (COVID-19 non-pharmaceutical interventions) excluded from training; training starts 2022-01-02.

`data/` is not versioned: everything is rebuilt by the pipeline. Code is MIT-licensed; the data are public OpenDataSUS data under their own terms.

## Setup

```bash
conda env create -f environment.yml        # or: conda create -n vsr --file environment.lock.txt (win-64, exact builds)
conda activate vsr
```

Every notebook calls `vsr_comum.verificar_ambiente()`, which fails if an installed version differs from the pinned ones.

## Running the experiment

Run in this order from the repository root (each notebook with `jupyter nbconvert`):

```bash
python baixar_sinasc.py                     # population (before 01)
jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=-1 01_dados_srag_vsr.ipynb
python validar_dados.py                     # optional external QC
for nb in 02_eda 03_nowcasting 04_modelos 05_marcos 06_decisao; do
  jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=-1 $nb.ipynb
done
```

Sensitivity with the `srag` target (outputs suffixed `_srag`):

```bash
for nb in 03_nowcasting 04_modelos 05_marcos; do
  VSR_SERIE=srag jupyter nbconvert --to notebook --execute $nb.ipynb --output ${nb}_srag.ipynb
done
```

Environment variables:
- `VSR_OFFLINE=1` — `01` skips the network and reprocesses the cache.
- `VSR_SERIE=srag` — `03/04/05` use the `srag` target.
- `VSR_DATA_CONFIG=<file.json>` — overrides fields of the `Config` dataclass in `01`.

Seeds are fixed (`SEMENTE = 20261019` + origin index), so backtests are reproducible. Total runtime is ~25 min with the cache (plus ~15 min for the `srag` runs).

## Notebooks and scripts

| Step | File | What it does | Main outputs (`data/processed/`) |
|---|---|---|---|
| 0 | `baixar_sinasc.py` | Downloads SINASC and builds the under-2 population by state and year | `data/external/populacao.csv` |
| 1 | `01_dados_srag_vsr.ipynb` | Downloads SIVEP-Gripe, filters cases, builds the weekly panel, delay triangle, completeness, climatology, state tiers, hierarchy and QC tables | `painel_semanal`, `triangulo_atraso`, `completude_atraso`, `climatologia`, `tiers_uf.csv`, `matriz_S.csv`, `qc_*.csv`, `metadata.json` |
| 1c | `validar_dados.py` | 10 checks of the processed data against the raw files | `qc_validacao_externa.csv` |
| 2 | `02_eda.ipynb` | Exploratory analysis; observed season milestones (truth) and alternative definitions (MEM, rate threshold) | `eda_marcos_temporada.parquet` |
| 3 | `03_nowcasting.ipynb` | Corrects reporting delay: Gamma-Poisson random-walk nowcast vs. chain-ladder | `previsoes_nowcast`, `avaliacao_nowcast` |
| 4 | `04_modelos.ipynb` | Weekly backtest (h = 0–4) of seasonal naive, climatology, ETS, ARIMA-Fourier, harmonic NB, global LightGBM and an ensemble; hierarchical reconciliation (MinT, bottom-up, Schaake shuffle); WIS evaluation | `previsoes_backtest`, `avaliacao_backtest` |
| 5 | `05_marcos.ipynb` | Forecasts of onset, peak and end from sampled trajectories, evaluated by lead time | `previsoes_marcos`, `avaliacao_marcos` |
| 6 | `06_decisao.ipynb` | Immunization window evaluation: fixed windows vs. forecast-based trigger (leave-one-season-out 2023–2025) | `decisao_curva`, `decisao_janelas`, `decisao_gatilho` |
| S | `0N_*_srag.ipynb` | Same as 03/04/05 with the `srag` target | outputs with suffix `_srag` |

Modules (each runs a self-test with `python <module>.py`, printing `ok`):
- `vsr_comum.py` — constants, milestone definitions, window helpers, MEM, panel loading, environment check.
- `vsr_nowcast.py` — delay cube, completeness, Gamma-Poisson nowcast.
- `vsr_modelos.py` — real-time series, models, reconciliation, backtests, milestone forecasts, metrics (WIS, RPS).

## Main results (cut-off 2026-10-18)

- Nowcast `gamma_rw` beats chain-ladder (relative log WIS 0.85 Brazil, 0.72 regions, 0.39–0.66 states), 90% coverage ~0.90–0.95.
- Weekly forecasts (h = 1–4): ensemble relative WIS vs. seasonal naive 0.55/0.55/0.70 (Brazil/regions/states), the only calibrated model; LightGBM best in states (0.59).
- Milestones: ~2-week error in onset/peak at 1–8 weeks lead; no model beats the seasonal naive on timing.
- Decision: a fixed Feb–Aug window covers 92–93% of RSV hospitalizations; a forecast-based trigger covers 89–91% with fewer weeks (weak evidence, 2 seasons).

Details and confidence intervals in [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md).

## License

[MIT](LICENSE).
