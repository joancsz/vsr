# Previsão da temporada de VSR no Brasil (alvo: ISF 2027)

Previsão probabilística semanal de hospitalizações por SRAG-VSR em menores de 2 anos, por UF, região e Brasil, e dos marcos da temporada (início, pico, fim), avaliada para a decisão sobre a janela de imunização (nirsevimabe / vacina materna). Resumo para o ISF 2027 (~mar/2027). Estado em 20/10/2026, dados com corte em 18/10/2026 (última semana completa: 11/10/2026).

Documentação de referência (ler antes de mudar método ou dados):
- `docs/METODOLOGIA.md` — requisições, tratamentos, modelos, métricas e resultados numerados por seção (§0–§14). É a fonte das decisões.
- `docs/DICIONARIO_DADOS.md` — esquema de todas as tabelas (bruto → painel → previsões → decisão).
- `docs/CONTEXTO_VSR.md` — histórico do projeto: crítica inicial da proposta, decisões em ordem e achados.

## Modo de trabalho
- Início de sessão e após compactação: invocar `/ponytail:ponytail` (nível full): menor mudança completa, sem opção/abstração não pedida; cada resposta termina com 1–2 linhas sobre o que foi pulado/não checado e o risco.
- Usuária: português, conciso e direto; saída técnica densa; Python de produção, modular, notação matemática formal; sem enquadramento didático.
- A cada mudança de método ou resultado: atualizar METODOLOGIA, DICIONARIO e CONTEXTO, e o resumo abaixo.

## Ambiente
- Conda env `vsr`: `~/miniconda3/envs/vsr/python.exe` (o python padrão não tem as dependências). Versões fixadas em `environment.yml` + `environment.lock.txt`; `vsr_comum.verificar_ambiente()` falha em divergência (chamado em todo notebook). Nova dependência ⇒ fixar nos dois arquivos.
- Executar notebook: `PATH=~/miniconda3/envs/vsr:~/miniconda3/envs/vsr/Scripts:$PATH ~/miniconda3/envs/vsr/Scripts/jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=-1 X.ipynb`
- Windows: toda E/S de texto com `encoding="utf-8"` (cp1252 corrompe). Em heredoc no Bash, `\t`, `\l`, `\g` viram escapes: escrever scripts com a ferramenta Write ou strings raw.
- Variáveis: `VSR_OFFLINE=1` (01 sem rede, reprocessa o cache), `VSR_SERIE=srag` (03/04/05 com alvo `srag`, saídas `_srag`), `VSR_DATA_CONFIG` (JSON que sobrescreve a `Config` do 01).
- `ref/` (local, não versionado): geradores nbformat `build_*.py` usados no desenvolvimento, proposta em PDF, plano. Os `.ipynb` versionados são a fonte; se `ref/` não existir, editar o notebook.

## Pipeline (ordem de execução)

| Passo | Arquivo | Saídas em `data/processed/` (salvo indicação) | Tempo |
|---|---|---|---|
| 1 | `01_dados_srag_vsr.ipynb` | download SIVEP-Gripe 2019–2026 (OpenDataSUS, cache em `data/raw/SRAG`), `data/interim/casos_menores2.parquet`, `painel_semanal`, `triangulo_atraso`, `completude_atraso`, `climatologia`, `tiers_uf.csv`, `hierarquia_unidades.csv`, `matriz_S.csv`, `qc_*.csv`, `metadata.json` | ~5 min (cache) |
| 1b | `baixar_sinasc.py` (antes do 01) | `data/external/populacao.csv` (SINASC, ~1 GB de brutos em `data/raw/SINASC`) | ~3 min |
| 1c | `validar_dados.py` | `qc_validacao_externa.csv` (10 checagens contra os brutos) | ~2 min |
| 2 | `02_eda.ipynb` | `eda_marcos_temporada.parquet` (verdade dos marcos, definições alternativas) | ~1 min |
| 3 | `03_nowcasting.ipynb` | `previsoes_nowcast`, `avaliacao_nowcast` | ~1 min |
| 4 | `04_modelos.ipynb` | `previsoes_backtest`, `avaliacao_backtest` | ~10 min |
| 5 | `05_marcos.ipynb` | `previsoes_marcos`, `avaliacao_marcos` | ~4 min |
| 6 | `06_decisao.ipynb` | `decisao_curva`, `decisao_janelas`, `decisao_gatilho` | ~1 min |
| S | `0N_*_srag.ipynb` | mesmas saídas com sufixo `_srag` | ~15 min |

Módulos (cada um com autoteste em `__main__`, imprime `ok`):
- `vsr_comum.py` — constantes e definições *a priori* (`marcos_amostras`: início/fim = fração acumulada 0,10/0,90, pico = argmax da média móvel centrada de 3 sem.), janelas (`janela_minima`, `fracao_na_janela`), MEM (`mem_periodo`, `mem_limiar`, `inicio_fim_limiar`), `carregar_painel`, `verificar_ambiente`.
- `vsr_nowcast.py` — cubo do triângulo (`cubo`), completude (`F_maturado`, `F_janela`), nowcast Gamma-Poisson (`amostras`, `nowcast_padrao` com `METODO_PADRAO`), `prever_origem` do 03.
- `vsr_modelos.py` — série em tempo real (`serie_origem`), modelos, reconciliação (`mint`, `bottom_up`, `schaake`), backtest semanal (`prever_origem`, `backtest`), marcos (`prever_marcos_origem`, `forma_curva`, `ensemble_ponderado`, `backtest_marcos`), métricas (`wis`, `pontuar`, `wis_relativo`, `pontuar_marcos`), `SERIE`/`sufixo()`.

## Dados (decisões fixadas)
- Unidade: casos de SRAG hospitalizados (`HOSPITAL = 1`) em < 2 anos, semana de sintomas (domingo; ano epidemiológico pela quarta-feira; 2020 e 2025 têm SE 53). Critério principal: UF de **residência** (recuperada pelo município; 0,03% imputada pela notificação). Faixa principal `00-23m`.
- Alvo principal `vsr` (PCR ou antígeno positivo). `vsr_est_inf`/`vsr_est_sup` = limites da correção por testagem, nunca alvo. `srag` só como sensibilidade.
- Disponibilidade = `DT_DIGITA`; triângulo de atraso com D = 26 semanas; reconstrução em tempo real otimista (sem snapshots reais).
- Hierarquia BR → 5 regiões → 27 UFs (`y = S y_UF`). Tiers por VSR mínimo 2023–2025: A ≥ 300 (15 UFs), B ≥ 100 (7), C < 100 (RR, AL, MT, PI, TO).
- Período 2020–2021 = `quebra_npi` (fora do treino); treino a partir de 02/01/2022, janela de 156 semanas. Averiguação cresce 10–100× por UF até 2025 ⇒ definições de marcos relativas ao total da temporada.
- População < 2 anos = NV(a−1) + NV(a−2) do SINASC (BR 5,87 mi em 2019 → 4,85 mi em 2026).

## Métodos e resultados (detalhes e IC em METODOLOGIA)
- **Nowcast (§6.1)** `gamma_rw`: $y_t \sim \text{Pois}(\lambda_t F(d))$, faltantes $\sim \text{Pois}(\varepsilon\lambda_t(1-F))$, $\varepsilon \sim \text{Gama}(4,4)$, *prior* Gama de passeio aleatório com tendência ($k = 10$), $F$ por chain-ladder nas últimas 52 semanas; ajustado em 2023, avaliado em 2024–2026. WIS log relativo ao chain-ladder: 0,85 (BR), 0,72 (regiões), 0,66/0,42/0,39 (UF A/B/C); cobertura de 90% ~0,90–0,95.
- **Série na origem (§11.1):** versão + mediana do nowcast em o−26…o−1; a semana d = 0 fica fora (`DESCARTE_RECENTE = 1`: incluí-la piorou h = 0 e h ≥ 1); NB e LightGBM partem de cada amostra do nowcast; `nowcast` entra no ensemble só em h = 0.
- **Previsão semanal (§11, 137 origens 31/12/2023–09/08/2026, h = 0–4):** naive sazonal (baseline), climatologia, ETS, ARIMA-Fourier, NB harmônica local, LightGBM global (Poisson + NB), `lgbm_taxa` (offset populacional), ensemble (média de quantis). WIS log relativo ao naive, h = 1–4, validação BR/região/UF: ensemble **0,55/0,55/0,70** (único calibrado: cob. 90% 0,97/0,94/0,85); `lgbm_global` 0,61/0,57/**0,59**. `lgbm_taxa` ≈ `lgbm_global`. Climatologia pior que o naive.
- **Hierarquia (H2):** ganho vem do *pooling* (LightGBM 0,79× NB local nas UFs; 0,71× no tier C); MinT sem ganho. Amostras conjuntas (Schaake shuffle, `_conj`) corrigem parte da subdispersão nos agregados (cob. 90% MinT BR 0,40 → 0,73).
- **Marcos (§12, 102 origens SE 1–35 de 2024–2026):** trajetórias = versão + amostra do nowcast + amostras do modelo; marcos por amostra com a mesma função da verdade. Antecedência 1–8 sem: erro ~2 sem em início/pico (BR + regiões), 2–3 nas UFs A, 4–5 no fim. Ensemble vence a climatologia; **nenhum modelo vence o naive sazonal no *timing*** (ensemble pico 25/63, p = 0,13). `forma_curva` (molde deslocado/esticado) e `ensemble_pond` (pesos 1/RPS) não ajudam.
- **Definições alternativas (§10.3, §12.6):** MEM antecipa o início 3–4 sem (limiar não estacionário com averiguação crescente); limiar de taxa (17,6/100 mil/sem) fica a 1–2 sem, sem início em 12–24%; conclusões do 05 mantidas.
- **Decisão (§14, LOSO 2023–2025):** fev–ago (30 sem) cobre 0,92–0,93; janela regional de mesma duração empata; gatilho $P(\text{início} \le s+3) > 0{,}7$ cobre 0,91/0,89 (BR + regiões / UF A) com 27/24 semanas (+12–22% de cobertura por semana), antecedência 3–5 sem, sem falso alarme nos agregados. **H4 = argumento de custo**, evidência fraca (2 temporadas).
- **Achados de contexto:** Norte e Nordeste são tardios em 2023–2025 (contra a premissa da proposta; em 2026 o Norte começou na SE 6); desde 2024 nenhuma temporada cruza a virada do ano; 71% das UF-temporadas têm ≥ 90% do VSR em fev–ago.
- **Sensibilidade `srag` (§11.8):** ranking mantido (ensemble rel. naive 0,80/0,73/0,77; LightGBM 0,76/0,73/0,73; `gamma_rw` melhor que chain-ladder); *timing* também sem vitória sobre o naive. Conclusões não dependem do alvo.

## Regras
- Treinar só com a versão conhecida na origem; nunca usar `*_imput_ref` nem `vsr_est_*` como alvo; climatologia só `expanding`.
- Semente fixa `SEMENTE = 20261019` (+ índice da origem); backtests reprodutíveis.
- Definições *a priori* (0,10/0,90, MM3, tiers, `gamma_rw`) foram fixadas antes de olhar a avaliação: não reajustar com os resultados de 2024–2026.
- Atalhos com limite conhecido: `# shortcut: <limite>, <quando trocar>`. Antes de sobrescrever saídas longas, backup fora do repositório.

## Status e pendências
- Feito (20/10/2026): itens 1–7 do plano (nowcast integrado, amostras conjuntas, forma da curva, população, sensibilidade dos marcos, `06_decisao`, sensibilidade `srag`).
- Pendentes:
  1. molde de dependência próprio do LightGBM e incerteza de parâmetros nas amostras conjuntas (cob. 90% ~0,7 nos agregados);
  2. usar a previsão a partir de o − 1 como *prior* do nowcast de d = 0;
  3. *timing*: informação externa (vizinhos, outros vírus) ou mais temporadas; rever §14 com 2026 completa;
  4. decidir `vacina_materna_ini` (data de início da vacina materna);
  5. snapshots reais: coletor `capturar_snapshot_srag.py` e rotina em `OPERACAO_SEMANAL.md` criados em 20/10/2026; falta acumular uma temporada para a reconstrução real;
  6. confirmar no dicionário do SIVEP se `PCR_RESUL = 2` inclui testes só para SARS-CoV-2;
  7. sensibilidade `incluir_hospital_ausente = True` (outra `raiz`);
  8. resumo ISF ~mar/2027.
