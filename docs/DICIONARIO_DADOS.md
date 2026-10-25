# Dicionário de dados

Esquema de todas as tabelas do projeto, do bruto às saídas da EDA. As regras de construção estão em [`METODOLOGIA.md`](METODOLOGIA.md) (as seções são citadas como §). As contagens de linhas são da execução de 19/10/2026.

**Convenções**
- `semana` é sempre o **domingo** da semana epidemiológica.
- `ano_epi`/`semana_epi` seguem o ano da quarta-feira (MMWR/MS).
- Códigos de UF são siglas; as regiões são `NORTE`, `NORDESTE`, `CENTRO_OESTE`, `SUDESTE` e `SUL`; o Brasil é `BR`.
- Faixas etárias: `00-05m`, `06-11m`, `12-23m`, e `00-23m` (soma).
- Contagens com NaN = semana sem arquivo-fonte (nunca zero).

**Leitura rápida**

```python
from vsr_comum import carregar_painel, metadados, P
painel = carregar_painel(criterio="residencia", faixa="00-23m")   # todos os níveis
uf = painel.query("nivel == 'UF'")
```

---

## 1. Bruto: `data/raw/SRAG/<ano>/INFLUDaa-dd-mm-aaaa.parquet`

Ficha SIVEP-Gripe, com 194 campos (iguais em 2019–2026), um arquivo por ano epidemiológico de sintomas. Abaixo, só os campos usados. Os códigos seguem o dicionário do SIVEP-Gripe e devem ser **confirmados no dicionário oficial** publicado com os arquivos.

| Campo | Conteúdo | Códigos / uso |
|---|---|---|
| `NU_NOTIFIC` | número da notificação | chave de deduplicação |
| `DT_NOTIFIC` | data de notificação | fallback da disponibilidade |
| `DT_SIN_PRI` | data dos primeiros sintomas | define `semana` |
| `DT_DIGITA` | data de digitação | disponibilidade do registro |
| `DT_PCR`, `DT_RES_AN` | datas do resultado de RT-PCR e de antígeno | disponibilidade do VSR |
| `DT_ENCERRA` | data de encerramento | mantida, não usada |
| `SG_UF`, `CO_MUN_RES` | UF e município (IBGE) de residência | critério `residencia` |
| `SG_UF_NOT`, `CO_MUN_NOT` | UF e município de notificação | critério `notificacao` |
| `NU_IDADE_N`, `TP_IDADE` | idade e unidade | `TP_IDADE`: 1 dias, 2 meses, 3 anos |
| `HOSPITAL` | houve internação | 1 sim, 2 não, 9 ignorado, vazio |
| `UTI` | internação em UTI | 1 sim |
| `EVOLUCAO` | evolução | 2 óbito (por SRAG) |
| `CLASSI_FIN` | classificação final | 1 influenza, 2 outro vírus respiratório, 3 outro agente, 4 não especificado, 5 covid-19 |
| `PCR_RESUL` | resultado do RT-PCR | 1 detectável, 2 não detectável, 3 inconclusivo, 4 não realizado, 5 aguardando, 9 ignorado |
| `RES_AN` | resultado do teste de antígeno | 1 positivo, 2 negativo, 3–5 e 9 como em `PCR_RESUL` |
| `PCR_VSR`, `AN_VSR` | VSR no RT-PCR e no antígeno | 1 = marcado. **Só preenchidos com resultado detectável** |
| `POS_PCROUT`, `POS_AN_OUT` | positivo para outros vírus | 1 sim, 2 não. **Só preenchidos com resultado detectável** |
| `POS_PCRFLU`, `PCR_FLUASU`, `POS_AN_FLU` | influenza | mesmo padrão de preenchimento |
| `PCR_SARS2` | SARS-CoV-2 no RT-PCR | 1 = marcado |
| `CS_GESTANT` | gestante (extra por regex) | vira `X_CS_GESTANT` |

Manifesto: `data/raw/SRAG/manifesto_download.json`, com uma entrada por ano (`ano`, `origem`, `url`, `arquivo`, `bytes`, `sha256`, `data_publicacao`, `last_modified_servidor`, `registrado_em_utc`).

Snapshots reais: `data/raw/snapshots/SRAG/manifesto_snapshots.jsonl`, uma linha imutável por ano e data de captura, com `acao`, `ano`, `arquivo`, `bytes`, `data_captura`, `data_publicacao`, `etag`, `last_modified_servidor`, `registrado_em_utc`, `sha256` e `url`. Quando o hash já existe, `arquivo` referencia a cópia anterior e `acao = referencia_hash_existente`.

---

## 2. `data/interim/casos_menores2.parquet`: base limpa

Uma linha por notificação de SRAG hospitalizada em menores de 2 anos (585.232 linhas).

| Coluna | Tipo | Descrição |
|---|---|---|
| `arquivo_ano`, `arquivo_pub` | int, data | ano e data de publicação do arquivo de origem |
| `nu_notific` | str | número da notificação |
| `dt_sin_pri`, `dt_notific`, `dt_digita`, `dt_pcr`, `dt_res_an`, `dt_encerra` | data | datas do bruto, já interpretadas |
| `dt_disp_registro` | data | disponibilidade do registro (§3.1) |
| `dt_resultado_vsr` | data | primeiro resultado positivo de VSR |
| `dt_disp_vsr` | data | disponibilidade do VSR = máx(registro, resultado); NaT se não for VSR |
| `semana`, `ano_epi`, `semana_epi` | data, int, int | semana de primeiros sintomas |
| `semana_disp_registro`, `semana_disp_vsr` | data | semanas de disponibilidade |
| `idade_meses` | float | idade em meses (anos × 12, dias / 30,4375) |
| `faixa_etaria` | str | `00-05m`, `06-11m`, `12-23m` |
| `uf_residencia` | str | UF de residência (sempre preenchida) |
| `uf_notificacao` | str | UF de notificação; **pode ser nula** (191 casos) |
| `uf_res_recuperada` | bool | UF de residência obtida pelo município |
| `uf_res_imputada_notif` | bool | UF de residência imputada pela de notificação |
| `co_mun_res` | str | município de residência (IBGE) |
| `vsr`, `vsr_pcr`, `vsr_an` | bool | VSR confirmado (qualquer teste, PCR, antígeno) |
| `lab_resultado` | bool | `PCR_RESUL`/`RES_AN` ∈ {1, 2} ou VSR positivo |
| `testado_painel` | bool | VSR positivo ou `POS_PCROUT`/`POS_AN_OUT` ∈ {1, 2} (≈ algum agente detectado) |
| `sars2` | bool | `PCR_SARS2 = 1` |
| `classi_fin` | int | classificação final |
| `uti`, `obito` | bool | UTI; óbito por SRAG (`EVOLUCAO = 2`) |
| `hospital_ausente` | bool | `HOSPITAL` vazio; só pode ser `True` com `incluir_hospital_ausente = True` |
| `disp_reg_imputada` | bool | disponibilidade pela notificação (sem `DT_DIGITA` válida) |
| `disp_vsr_sem_data_res` | bool | VSR sem data de resultado válida |
| `X_CS_GESTANT` | str | extra do bruto |

---

## 3. `data/processed/painel_semanal.parquet`: painel principal

Chave: `criterio_uf, nivel, unidade, faixa_etaria, semana` (107.448 linhas = 2 critérios × 33 unidades × 4 faixas × 407 semanas, de 30/12/2018 a 11/10/2026).

| Coluna | Tipo | Descrição |
|---|---|---|
| `criterio_uf` | str | `residencia` (principal) ou `notificacao` |
| `nivel`, `unidade` | str | `BRASIL`/`REGIAO`/`UF` e o código da unidade |
| `faixa_etaria` | str | faixa etária, ou `00-23m` |
| `semana` | data | domingo da semana de sintomas |
| `srag` | float | SRAG hospitalizada (**alvo de sensibilidade**) |
| `vsr` | float | VSR confirmado (**alvo principal**) |
| `vsr_pcr`, `vsr_an` | float | VSR por RT-PCR e por antígeno (um caso pode ter os dois) |
| `lab_resultado`, `testado_painel` | float | denominadores da positividade (§5.1) |
| `sars2` | float | SARS-CoV-2 por PCR |
| `vsr_uti`, `vsr_obito` | float | VSR com UTI e com óbito |
| `vsr_est_inf`, `vsr_est_sup` | float | **limites** inferior e superior do VSR corrigido pela testagem (§5.2); `vsr ≤ inf ≤ sup` |
| `positividade_lab`, `positividade_painel` | float | `vsr/lab_resultado` (limite inferior) e `vsr/testado_painel` (limite superior) |
| `frac_testado_painel` | float | `testado_painel/srag` |
| `ano_epi`, `semana_epi` | int | ano e semana epidemiológica |
| `cobertura_arquivos`, `coberta` | float, bool | cobertura por arquivos-fonte; `False` ⇒ contagens NaN |
| `periodo` | str | `pre_pandemia`, `quebra_npi`, `pos_pandemia`, `imunizacao` |
| `quebra_npi`, `imunizacao`, `vacina_materna` | bool | indicadores de período |
| `atraso_ate_corte` | int | semanas entre `semana` e a semana do corte (limitado a 26) |
| `completude_esperada_vsr`, `completude_esperada_srag` | float | $F_r(\text{atraso\_ate\_corte})$ |
| `semana_incompleta` | bool | `completude_esperada_vsr < 0,95` (precisa de nowcasting) |
| `temporada`, `semana_temporada` | int | ano da temporada; semana 1..53 dentro dela |
| `{vsr,srag,vsr_est_inf,vsr_est_sup}_imput_passado` | float | observado; em `quebra_npi`, climatologia só com temporadas anteriores |
| `{…}_imput_ref` | float | observado; em `quebra_npi`, climatologia com todas as referências (só EDA) |
| `pop_menores2` | float | menores de 2 anos (SINASC, NV(a−1) + NV(a−2); só na faixa `00-23m`; METODOLOGIA §8) |
| `vsr_taxa100k`, `srag_taxa100k`, `vsr_est_inf_taxa100k`, `vsr_est_sup_taxa100k` | float | casos por 100 mil menores de 2 anos (`00-23m`) |

---

## 4. `data/processed/triangulo_atraso.parquet`: triângulo de atraso

| Coluna | Tipo | Descrição |
|---|---|---|
| `criterio_uf`, `uf`, `faixa_etaria`, `semana` | — | célula do painel (sem agregados) |
| `serie` | str | `vsr` (por `dt_disp_vsr`) ou `srag` (por `dt_disp_registro`) |
| `atraso` | int | semanas entre sintomas e disponibilidade, de 0 a 26 |
| `atraso_e_bucket_final` | bool | `atraso = 26` significa ≥ 26 (ou sem data) |
| `casos` | int | contagem; Σ sobre `atraso` = painel |

Versão conhecida na origem `o`: filtre `atraso <= (o - semana).days // 7` e some (METODOLOGIA §6).

## 5. `data/processed/completude_atraso.parquet`

| Coluna | Descrição |
|---|---|
| `serie`, `regiao` | `vsr`/`srag`; região ou `BR` |
| `atraso` | 0..26 |
| `F` | fração acumulada disponível até `atraso` |
| `n` | casos usados na estimativa |
| `anos_base` | anos das semanas maturadas usadas |

## 6. `data/processed/climatologia.parquet`

| Coluna | Descrição |
|---|---|
| `criterio_uf`, `nivel`, `unidade`, `faixa_etaria`, `serie` | série: `vsr`, `srag`, `vsr_est_inf`, `vsr_est_sup` |
| `semana_temporada` | 1..53 |
| `media_log1p`, `dp_log1p` | média e desvio-padrão de $\log(1+y)$ entre temporadas |
| `media_fracao` | média da fração da temporada naquela semana |
| `n_temporadas` | temporadas usadas; **0 na SE 53 preenchida pela média das SE 52 e 1** |
| `tipo` | `expanding` (sem vazamento; usar em modelos) ou `referencia` (EDA) |
| `temporada_alvo` | temporada prevista (`expanding`); −1 em `referencia` |
| `temporadas_usadas` | lista das temporadas |

## 7. Hierarquia e tiers

- **`hierarquia_unidades.csv`**: `unidade`, `nivel`, `pai` (BR → regiões → UFs).
- **`matriz_S.csv`**: matriz de agregação $S$ (linhas: 33 unidades; colunas: 27 UFs; 0/1).
- **`tiers_uf.csv`**:
  - `unidade`;
  - `vsr_<ano>` (VSR por temporada);
  - `min_temporadas_tier` (mínimo em 2023–2025);
  - `temporadas_tier`;
  - `tier` (A ≥ 300, B ≥ 100, C);
  - `regiao`.

## 8. `data/processed/eda_marcos_temporada.parquet`: marcos de temporada

Saída do `02_eda`; é o alvo de avaliação de início e pico. Uma linha por `nivel × unidade × temporada × serie` (`vsr`, `srag`), critério residência, `00-23m`.

| Coluna | Descrição |
|---|---|
| `semanas`, `completa` | semanas observadas; `completa` = ≥ 52 semanas, todas cobertas (2026 = `False`) |
| `total` | casos na temporada |
| `inicio`, `fim` | 1ª `semana_temporada` com fração acumulada ≥ 0,10 e ≥ 0,90 |
| `pico`, `pico_valor` | argmax e valor máximo da média móvel centrada de 3 semanas |
| `inicio_data`, `pico_data` | domingo correspondente |
| `janela_ini`, `janela_dur` | início e duração da menor janela contígua com ≥ 90% dos casos |
| `frac_fev_ago` | fração dos casos com sintomas de fevereiro a agosto |
| `referencia` | temporada de referência da climatologia |
| `regiao`, `tier` | região da unidade; tier (só para UFs) |
| `nowcast_aplicado` | `vsr` com as semanas incompletas (o − 26 … o, última semana do painel) pela mediana do nowcast `gamma_rw` (só a temporada corrente, 2026) |
| `limiar_mem` | limiar pré-epidêmico do MEM (casos/semana) calculado nas **outras** temporadas completas de 2022–2025 (METODOLOGIA §10.3); só `vsr`, temporadas ≥ 2022 |
| `inicio_mem`, `fim_mem` | 1ª e última semana com `vsr ≥ limiar_mem` (NaN se nunca) |
| `inicio_taxa`, `fim_taxa` | 1ª e última semana com taxa ≥ 17,6 casos/100 mil/semana (NaN se nunca) |

## 9. Modelagem: `previsoes_backtest.parquet` e `avaliacao_backtest.parquet`

Saídas do `04_modelos` (METODOLOGIA §11), critério residência, `00-23m`, alvo `vsr`.

**`previsoes_backtest.parquet`**: uma linha por `modelo × unidade × origem × h` (178.374 linhas (rodada de 20/10/2026)).

| Coluna | Descrição |
|---|---|
| `modelo` | `naive_sazonal`, `climatologia`, `ets`, `arima_fourier` (só agregados), `nb_local`, `nb_bottom_up`, `nb_mint`, `nb_bottom_up_conj`, `nb_mint_conj` (amostras conjuntas, Schaake), `lgbm_global` (conjunto), `lgbm_taxa` (offset populacional), `nowcast` (só `h = 0`), `ensemble` |
| `nivel`, `unidade` | como no painel |
| `origem` | domingo da origem (dados conhecidos ao fim dessa semana) |
| `h` | horizonte em semanas (0 = semana da origem) |
| `alvo` | semana prevista = `origem + 7h` dias |
| `q0.01` … `q0.99` | 23 quantis da distribuição preditiva (contagens semanais) |

**`avaliacao_backtest.parquet`**: as mesmas chaves (sem os quantis), restritas a alvos avaliáveis (temporadas 2024–2026, sem `semana_incompleta`).

**Saídas exploratórias locais da segunda rodada** (não sobrescrevem a rodada de referência): `previsoes_experimento_avancado_v2.parquet` contém os quantis de `lgbm_sazonal` e `poisson_painel`, nas mesmas colunas e chaves de `previsoes_backtest.parquet`, somente para `h = 1..4`. `avaliacao_experimento_avancado_v2.parquet` reúne essas previsões, quatro modelos de referência e `ensemble_prequential`, com o esquema de `avaliacao_backtest.parquet`. `pesos_ensemble_prequential_v2.parquet` contém `origem`, `nivel`, `h`, `modelo`, `peso` e `n_historico_maduro`; os pesos são usados apenas na análise exploratória.

**Saídas exploratórias locais da terceira rodada** (não sobrescrevem a rodada de referência): `previsoes_ensemble_prequential_v2.parquet` guarda a reconstrução do ensemble online; `previsoes_ensemble_conformal_v1.parquet` contém a mesma previsão com intervalos recalibrados; e `avaliacao_ensemble_conformal_v1.parquet` reúne `naive_sazonal`, o ensemble online e sua versão conformal. Todas mantêm as chaves e os quantis de `previsoes_backtest.parquet`, para `h = 1..4`.

| Coluna | Descrição |
|---|---|
| `obs` | `vsr` final do alvo |
| `wis`, `wis_log` | WIS em escala natural e em $\log(1+\cdot)$ |
| `ea_mediana` | erro absoluto da mediana |
| `cob50`, `cob90` | observado dentro do intervalo central de 50% e de 90% |
| `temporada`, `periodo` | temporada do alvo; `validacao` (2024–2025) ou `teste_2026` |
| `tier` | tier da UF; `agregado` para região e Brasil |

**`previsoes_marcos.parquet`** (saída do `05_marcos`, METODOLOGIA §12): uma linha por `modelo × unidade × origem × marco` (42.228 linhas).

| Coluna | Descrição |
|---|---|
| `modelo` | `naive_sazonal`, `climatologia`, `nb_local`, `arima_fourier` (só agregados), `forma_curva`, `ensemble` (mistura), `ensemble_pond` (mistura ponderada por 1/RPS das temporadas anteriores) |
| `nivel`, `unidade`, `origem` | como em `previsoes_backtest` |
| `temporada`, `semana_origem` | temporada corrente e semana da origem dentro dela |
| `marco` | `inicio`, `pico`, `fim` |
| `p1` … `p53` | probabilidade prevista de o marco cair em cada semana da temporada |
| `p_nan` | probabilidade de trajetória nula (marco indefinido) |

**`avaliacao_marcos.parquet`**: as mesmas chaves (sem `p*`), mais as colunas abaixo.

| Coluna | Descrição |
|---|---|
| `semana_real` | marco observado (`eda_marcos_temporada`); 2026 provisório, fim de 2026 excluído |
| `rps` | CRPS discreto em semanas |
| `mediana`, `ea_mediana` | semana mediana prevista e seu erro absoluto |
| `p_1sem` | probabilidade prevista a até ±1 semana do real |
| `cob80` | real dentro do intervalo de 80% |
| `antecedencia`, `faixa_antec` | semana real − semana da origem, e faixas (≤ 0, 1–2, 3–4, 5–8, ≥ 9) |
| `grupo` | `BR`, `REGIAO`, `UF A`, `UF B` ou `UF C` |

**`previsoes_nowcast.parquet`** (saída do `03_nowcasting`, METODOLOGIA §6.1): uma linha por `modelo × unidade × origem × d` (173.448 linhas).

| Coluna | Descrição |
|---|---|
| `modelo` | `sem_correcao` ($y^{(o)}$), `chain_ladder` (pipeline atual), `cl_janela`, `gamma_rw` (escolhido) |
| `nivel`, `unidade`, `origem` | como em `previsoes_backtest` |
| `d` | atraso decorrido em semanas (0 = semana da origem), 0–8 |
| `alvo` | semana de sintomas estimada = `origem − 7d` dias |
| `q0.01` … `q0.99` | 23 quantis do total final $N_t$ |

**`avaliacao_nowcast.parquet`**: as mesmas chaves (sem os quantis), mais `obs`, `wis`, `wis_log`, `ea_mediana`, `cob50`, `cob90` (como em `avaliacao_backtest`) e:

| Coluna | Descrição |
|---|---|
| `grupo` | `BR`, `REGIAO`, `UF A`, `UF B` ou `UF C` |
| `verdade_madura` | `alvo + 26 semanas ≤` última semana do triângulo (obs. definitivo) |
| `vies_log` | $\log(1 + \text{mediana}) - \log(1 + \text{obs})$ |

**Sensibilidade `srag`** (METODOLOGIA §11.8): os mesmos esquemas, com sufixo `_srag` (`previsoes_nowcast_srag`, `avaliacao_nowcast_srag`, `previsoes_backtest_srag`, `avaliacao_backtest_srag`, `previsoes_marcos_srag`, `avaliacao_marcos_srag`); `obs` e `semana_real` referem-se a `srag`. Gerados com `VSR_SERIE=srag` (notebooks `0N_*_srag.ipynb`).

## 9b. Decisão (`06_decisao`, METODOLOGIA §14)

**`decisao_curva.parquet`**: por `unidade × temporada × L` (unidades: BR, regiões, UFs do tier A; temporadas 2023–2026).

| Coluna | Descrição |
|---|---|
| `L` | duração da janela (semanas), 4–53 |
| `ini` | `semana_temporada` de início da janela de L semanas com maior cobertura na curva de treino (média das curvas normalizadas das **outras** temporadas de 2023–2025) |
| `cob_treino`, `cob` | cobertura no treino e na temporada avaliada |

**`decisao_janelas.parquet`**: por `unidade × temporada × estrategia`.

| Coluna | Descrição |
|---|---|
| `estrategia` | `regional_90` (menor L com cobertura de treino ≥ 0,9), `regional_mesma_dur` (L = duração de fev–ago), `fev_ago` (quarta-feira de fevereiro a agosto) |
| `ini`, `dur` | semana de início e duração |
| `cobertura`, `cob_por_semana` | fração dos casos da temporada dentro da janela; cobertura ÷ duração |

**`decisao_gatilho.parquet`**: por `unidade × temporada × modelo × p` (temporadas 2024–2026, origens SE 1–35).

| Coluna | Descrição |
|---|---|
| `modelo` | `naive_sazonal`, `ensemble`, `forma_curva`, `ensemble_pond` |
| `p` | limiar de $P(\text{início} \le s + 3)$, 0,3–0,9 |
| `disparo` | 1ª semana da origem com probabilidade > p (NaN: não disparou até a SE 35) |
| `dur` | duração da campanha = `dur` de `regional_90` |
| `cobertura` | fração dos casos da temporada entre `disparo` e `disparo + dur − 1` (0 sem disparo) |
| `antecedencia` | início real − disparo (semanas) |
| `falso_alarme` | sem disparo ou antecedência > 8 semanas |

## 9c. População: `data/external/populacao.csv`

Gerado por `baixar_sinasc.py` (SINASC, OpenDataSUS; brutos em `data/raw/SINASC/`).

| Coluna | Descrição |
|---|---|
| `uf`, `ano` | sigla da UF; ano epidemiológico (2019–2026) |
| `pop_menores2` | NV(ano − 1) + NV(ano − 2), nascidos vivos por UF de residência da mãe |
| `anos_nascimento` | anos de nascimento usados (ex.: `2024+2025`) |

`qc_populacao.csv`: nascidos vivos e registros sem UF por ano de nascimento.

## 10. Auditoria

| Arquivo | Conteúdo |
|---|---|
| `qc_filtros.csv` | `etapa`, `linhas` restantes, `removidas` (METODOLOGIA §3) |
| `qc_por_ano.csv` | por `ano_epi`: `casos`, `vsr`, frações `lab` e `painel`, `sem_data_res_vsr`, `disp_reg_imputada`, `uf_res_imput`, `hospital_ausente`, `sem_uf_notificacao`, `positividade_painel`, `positividade_lab` |
| `qc_colunas_por_arquivo.csv` | por arquivo: linhas, linhas < 2 anos, colunas ausentes e extras, mapeamento de nomes |
| `qc_validacao.csv` | validações internas (`verificacao`, `ok`, `detalhe`) |
| `qc_validacao_externa.csv` | validações de `validar_dados.py` |
| `metadata.json` | ver abaixo |

**`metadata.json`**
- `gerado_em_utc`, `data_corte`, `semana_final_painel`;
- `serie_alvo` (`vsr`);
- `vsr_est_limites` (definição e nota dos limites);
- `hospital_ausente_incluido`;
- `fontes` (manifesto);
- `config` (todos os parâmetros);
- `temporadas_referencia`, `faixa_total`, `chave_painel`;
- `qc_atraso` (atrasos negativos e sem data);
- `colunas_painel` (descrição curta das colunas);
- `versoes` (Python, pandas, numpy);
- `arquivos` (linhas e sha256 de cada saída).
