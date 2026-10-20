# Contexto do projeto: previsão da temporada de VSR no Brasil

Handoff de uma sessão no claude.ai (19/10/2026) para continuar no Claude Code; histórico de decisões e resultados até 20/10/2026. O resumo atual do projeto está em `CLAUDE.md` (raiz).

> Os caminhos `ref/build_*.py` citados abaixo são geradores locais dos notebooks, não versionados (`ref/` está no `.gitignore`); os notebooks versionados são a fonte. `ref/baixar_sinasc.py` foi movido para a raiz (`baixar_sinasc.py`).

## 1. Projeto

- **Título:** Previsão probabilística do início e do pico da temporada de VSR nos estados brasileiros: reconciliação hierárquica e avaliação orientada à janela de imunização.
- **Alvo:** ISF 2027 (20–23/jun/2027, Paphos). Prazo do resumo de 2027 ainda não publicado; em 2026 foi março/abril. Planejar o resumo para ~março/2027.
- **Problema:** o SUS usa janela sazonal única nacional (nirsevimabe desde fev/2026, sazonalidade fev–ago; vacina materna no fim de 2025), mas a temporada começa em momentos diferentes por região (Norte/Nordeste mais cedo).
- **Hipóteses originais da proposta:**
  - H1: modelos probabilísticos superam benchmarks (naive sazonal, ETS, SARIMA) em h = 1–4 semanas.
  - H2: reconciliação hierárquica (Brasil → região → UF, MinT) melhora a acurácia, sobretudo nas UFs pequenas.
  - H3: ensemble mais robusto, inclusive nas temporadas atípicas pós-pandemia.
  - H4: janelas definidas pela previsão regional cobrem mais internações que a janela fixa fev–ago.
- **Dados:** SRAG/SIVEP-Gripe 2019–2026 (OpenDataSUS, arquivos `INFLUDaa-dd-mm-aaaa.parquet` num bucket S3; o nome muda a cada republicação), InfoGripe (referência de atraso), INMET (opcional), SINASC/IBGE (população < 2 anos), notas técnicas do MS.

## 2. Diagnóstico da extração inicial (notebook antigo)

- **2025 não foi baixado** (timeout) e o script preencheu com zeros sem avisar. A temporada pré-imunização mais recente estava ausente.
- **A averiguação não é estacionária:** os casos de VSR por UF cresceram 10–100× entre 2019 e 2024 por expansão da testagem (ex.: MT 0→374, AL 1→252, PE 2 em 2019). Esse problema pesa mais que a quebra de 2020. Para a maioria das UFs, as temporadas comparáveis são 2023–2026.
- **Atraso de digitação:** a completude é de ~15% na semana 0, ~65% na semana 1 e ~90% na semana 3. O nowcasting é obrigatório. Referência: Bastos et al. (2019, Stat Med), método do InfoGripe.
- **`DT_DIGITA` não registra o resultado laboratorial.** A reconstrução em tempo real feita com ela é otimista.
- Bugs menores: código "SE" ambíguo (Sergipe × Sudeste); clip na curva de completude; uso da UF de notificação em vez da de residência.
- **2026:** o pico de março a julho já está praticamente completo. O problema é a mudança de regime (imunização) confundida com o aumento da testagem. Usar como teste fora de distribuição.

## 3. Conclusões da crítica (decisões em aberto, mas recomendadas)

- **Hierárquico:** o ganho esperado da reconciliação MinT é pequeno no Brasil e nas regiões. O ganho real para UFs pequenas deve vir de **pooling**: um gradient boosting global em log-taxa com efeitos de UF, ou uma binomial negativa com efeitos aleatórios. Reformular H2 como a comparação de quatro estratégias:
  1. modelos locais
  2. bottom-up
  3. MinT-shrink via amostras (para contagens, reconciliar amostras e não quantis)
  4. modelo global
- **Coerência de contagens não garante coerência de pico/início** (o pico do agregado ≠ soma dos picos).
- **Poder estatístico:**
  - O WIS semanal é testável (milhares de pontos).
  - O erro de início e pico tem ~30–40 pares UF-temporada: apenas descritivo, com teste de sinal pareado.
  - H3 não é testável como está. Reformular para "robustez em 2026, a primeira temporada com imunização".
- **Descompasso entre horizonte e decisão:** previsões de 1–4 semanas não decidem a janela de imunização (compra e logística levam meses). Reformular H4:
  1. janela climatológica regional vs. janela nacional fixa (leave-one-season-out em 2023–2025);
  2. curva cobertura × duração da janela;
  3. gatilho adaptativo: iniciar quando P(início em 4 semanas) > p.
  - **Teste rápido feito (dados reais, 19/10/2026): H4 é argumento de custo, não de cobertura.** 71% dos pares UF-ano de 2022–2025 têm ≥ 90% do VSR dentro de fev–ago (quase todos em 2024). Exceção: 2025, com temporada tardia em AM, SE, PB, RN e RO (0,60–0,88). A pergunta passa a ser se uma janela regional mais curta cobre o mesmo que fev–ago, com 2025 como contraexemplo.
- **Tiers de UF:** A = modelável por UF; B = só com pooling, início/pico avaliados na região; C = só no agregado regional. Regra *a priori*: mínimo de casos de VSR em 2023–2025 ≥ 300 (A) ou ≥ 100 (B). Estimativa preliminar:
  - A: SP, PR, RS, SC, RJ, MG, DF, BA, CE, MS, GO, PE, ES, PB, AM
  - C: PI, TO, MT, AL, RR
- **Avaliação:** WIS em escala log (Bosse et al., 2023); WIS relativo pareado contra o baseline (Cramer et al., 2022); origem rolante nas temporadas de 2024–2025; 2026 como teste fora de distribuição.
- **Definição de início e pico:** fixar *a priori* (limiar de taxa, fração acumulada ou MEM; o MEM pede ~5 temporadas, o que é limítrofe aqui). Derivar a distribuição de início e de pico das trajetórias amostradas.
- **Alvo de sensibilidade:** SRAG total em menores de 2 anos (menos afetada pela testagem). Se as conclusões se mantiverem nos dois alvos, o argumento fica muito mais forte.
- **Snapshots reais:** agendar o download semanal do OpenDataSUS a partir de agora e verificar se o InfoGripe guarda versões históricas.

## 4. Estado atual: notebook de dados `01_dados_srag_vsr.ipynb`

**Rodou com os dados reais (19/10/2026):** 2019–2026 completos (2025 e 2026 publicados em 19/10/2026), corte 18/10/2026, painel até a SE iniciada em 11/10/2026. As 17 validações internas e as 10 de `validar_dados.py` (externas, recontagem a partir do bruto) passam.

O notebook é gerado por `ref/build_nb.py` (nbformat), que escreve `01_dados_srag_vsr.ipynb` na raiz; editar o gerador, não o `.ipynb`. Parâmetros na dataclass `Config`; sobrescritos por JSON via a variável `VSR_DATA_CONFIG`. Execução headless: `jupyter nbconvert --to notebook --execute --inplace 01_dados_srag_vsr.ipynb` no ambiente conda `vsr`.

**Ambiente:** `environment.yml` (versões diretas fixadas) e `environment.lock.txt` (lock explícito); `vsr_comum.verificar_ambiente()` interrompe a execução se as versões divergirem. **Documentação:** `docs/METODOLOGIA.md` (requisições, tratamentos, definições e plano de modelagem, do início ao fim) e `docs/DICIONARIO_DADOS.md` (esquema de todas as tabelas). Atualizar ambos a cada mudança de método.

### Achados da validação com dados reais

- **Alvo principal: `vsr` (confirmado).** Sensibilidade: `srag`.
- **Denominador da positividade:** `POS_PCROUT`, `POS_PCRFLU`, `PCR_FLUASU`, `PCR_VSR` (e `POS_AN_OUT`) só são preenchidos quando o teste detectou algum agente: 0% de preenchimento com `PCR_RESUL = 2`, 93–99% com `PCR_RESUL = 1`. A definição antiga de `testado_painel` exclui todos os negativos. O `vsr_est` antigo (≈ 54% de toda a SRAG < 2 anos) virou **dois limites**: `vsr_est_inf` (denominador `lab_resultado`; 0,34–0,41 da SRAG em 2023–2026) e `vsr_est_sup` (definição antiga; 0,52–0,56). Fração com resultado laboratorial: 0,85 (2019), 0,86, 0,88, 0,91, 0,93, 0,93, 0,93, 0,91 (2026).
- **Norte não começa mais cedo nestes dados:** pico (média móvel de 3 semanas) do Norte na SE 21–23 em 2023–2025, depois do Sudeste (SE 12–19) e do Centro-Oeste; o Nordeste também é tardio (SE 19–23). Só 2019 (SE 10) bate com a literatura, mas a averiguação de 2019 é baixa. Contradiz a premissa do problema (JOGH, 2026); confirmado no `02_eda` com datas de início (média 2023–2025: início CO 8,3 · SE 9,7 · S 13,0 · NE 14,0 · N 14,7; exceção: 2026, Norte na SE 6). Confrontar com a literatura antes de reescrever a motivação. Nenhuma temporada recente cruza a virada do ano (de 2024 em diante, ≤ 4% do VSR regional nas SE 1–4/49–53; até 0,21 em 2022–2023) ⇒ `semana_inicio_temporada` vazio (SE 1) serve.
- **Ficha 2025/2026 sem campo de nirsevimabe:** os 194 campos são os mesmos de 2019; a imunização só entra por data (`imunizacao_ini`).
- **2019:** 34% sem `DT_DIGITA` (disponibilidade imputada pela notificação); o triângulo de 2019 não serve para atraso.
- **Esparsidade:** 61–71% das semanas de 2023–2025 com zero VSR em MT, PI e TO.
- **Perdas:** a filtragem por `HOSPITAL = 1` remove 16.249 casos < 2 anos (2019–2026): 5.270 com `HOSPITAL = 2`, 762 com 9 e 10.217 vazios (~2% ao ano). Sensibilidade: `incluir_hospital_ausente = True`. O filtro de UF agora exige só a UF do critério em uso: os 191 casos sem UF de notificação (40 de VSR, quase todos do ES) ficam no painel por residência; VSR por ano = bruto.

### Pipeline

1. **Download:**
   - Descoberta dos links por API CKAN → listagem S3 → páginas do portal (as páginas funcionaram na máquina da usuária). Fallback em `URLS_CONHECIDAS`.
   - Retry com backoff, retomada por `Range`, validação do Parquet, cache por sha256, manifesto em `data/raw/SRAG/manifesto_download.json`.
   - Para se faltar um ano em `anos_obrigatorios`. Saídas manuais: `arquivos_manuais` (url) e `arquivos_locais` (caminho).
2. **Leitura:**
   - Colunas por aliases (`COLUNAS`). O filtro de idade < 24 meses é aplicado por row group.
   - Colunas extras por regex (`NIRSE|PALIV|VSR|...`) viram `X_*`.
3. **Limpeza**, com cada etapa registrada em `qc_filtros.csv`:
   - datas mistas (ISO e BR);
   - deduplicação entre arquivos (mantém a publicação mais recente; cada arquivo contém só o próprio ano epidemiológico, então remove 0);
   - `HOSPITAL = 1` (ou vazio, com `incluir_hospital_ausente`; coluna `hospital_ausente`);
   - UF de residência (recuperada pelo município IBGE e, em último caso, pela de notificação);
   - marcadores laboratoriais;
   - datas de disponibilidade.
4. **Definições:**
   - VSR = `PCR_VSR = 1` ou `AN_VSR = 1`.
   - `testado_painel` = VSR positivo, ou `POS_PCROUT` ∈ {1,2}, ou `POS_AN_OUT` ∈ {1,2} (≈ teste com agente detectado; ver achados).
   - `lab_resultado` = `PCR_RESUL` ou `RES_AN` ∈ {1,2}, ou VSR positivo (83 positivos com código fora de {1,2}).
   - Disponibilidade do VSR = max(`DT_DIGITA`, `DT_PCR`/`DT_RES_AN` do teste positivo).
   - Semana epidemiológica de domingo; o ano é o da quarta-feira. O painel termina na última semana completa (sábado ≤ data de corte).
5. **Cobertura:** P(arquivo Y+δ | SE k), estimada nos anos com arquivos vizinhos presentes. Semana com cobertura < 0,99 ⇒ `coberta = False` e contagens NaN.
6. **Painel:** critério_uf × nível × unidade × faixa (00-05m, 06-11m, 12-23m, 00-23m) × semana. Região e Brasil são somas das UFs (coerente).
7. **`vsr_est_inf` / `vsr_est_sup` (limites, fora do caminho principal):** `srag × p̃`, com p̃ = positividade trailing de 3 semanas e shrinkage (κ = 5) em direção à região (fallback: Brasil); denominador `lab_resultado` (inf) ou `testado_painel` (sup). Garantido `vsr ≤ inf ≤ sup` (verificado). Positividades: `positividade_lab` e `positividade_painel`.
8. **Triângulo de atraso:** D = 26 semanas, com o último bucket como ≥ D. Completude por região com as 3 temporadas maturadas mais recentes. Flags `semana_incompleta` (< 0,95) e `atraso_ate_corte`.
9. **Temporadas e climatologia:**
   - `temporada` = ano epidemiológico; opcionalmente deslocada por `semana_inicio_temporada[regiao]`. `semana_temporada` vai de 1 a 53 (2020 e 2025 têm SE 53); na climatologia, a semana 53 usa as temporadas que a têm e, sem nenhuma, a média das semanas 52 e 1 (`n_temporadas = 0`).
   - Temporadas de referência: ≥ 90% das semanas cobertas, fora 2020–21 e da era de imunização.
   - Climatologia `expanding` (só temporadas < alvo) e `referencia` (todas, só para EDA).
10. **Imputação 2020–21:** `*_imput_passado` (sem vazamento; na prática só 2019) e `*_imput_ref` (EDA). Os valores observados são preservados.
11. **Hierarquia, tiers e população:** matriz S, tiers A/B/C e população opcional (`uf, ano, pop_menores2` ⇒ taxas por 100 mil).
12. **Validações:** coerência BR = Σ regiões = Σ UFs; Σ faixas = total; triângulo = painel; semanas sem arquivo são NaN; limites ≥ `vsr`; positividades em [0,1]; chave única; (temporada, semana_temporada) único em 1..53; painel termina na última semana completa. Externas em `validar_dados.py` → `qc_validacao_externa.csv`.

### Saídas

**`data/interim/`**
- `casos_menores2.parquet`

**`data/processed/`**
- `painel_semanal.parquet` (chave `criterio_uf, nivel, unidade, faixa_etaria, semana`)
- `triangulo_atraso.parquet`
- `completude_atraso.parquet`
- `climatologia.parquet`
- `hierarquia_unidades.csv`, `matriz_S.csv`, `tiers_uf.csv`
- `qc_filtros.csv`, `qc_por_ano.csv`, `qc_colunas_por_arquivo.csv`, `qc_validacao.csv`
- `metadata.json` (data de corte, `semana_final_painel`, `serie_alvo`, `vsr_est_limites`, fontes, config, descrição das colunas, hashes)
- `qc_validacao_externa.csv` (gerado por `validar_dados.py`)

### Reconstrução da versão em tempo real

```python
o = pd.Timestamp(origem)  # domingo
t = tri[(tri.serie == "vsr") & (tri.criterio_uf == "residencia") & (tri.semana <= o)]
t = t[t.atraso <= (o - t.semana).dt.days // 7]
versao = t.groupby(["uf", "semana"])["casos"].sum()
```

### Regras para os notebooks seguintes

- Alvo principal `vsr`; sensibilidade `srag`; `vsr_est_inf`/`vsr_est_sup` só como faixa (nunca como alvo pontual).
- Treinar só com `coberta`. Excluir `quebra_npi` por padrão (ou usar dummy).
- Usar só a climatologia `expanding` com `temporada_alvo` = a temporada prevista.
- Nunca usar `*_imput_ref` em avaliação fora da amostra.
- 2026 = teste fora de distribuição.

## 5. Pendências imediatas

1. ~~Rodar o notebook com os dados reais e garantir 2025.~~ Feito em 19/10/2026.
2. Conferir no dicionário de dados do SIVEP:
   - se `PCR_RESUL = 2` pode ser teste só para SARS-CoV-2 (define quão longe `vsr_est_inf` está do valor real); o padrão de preenchimento de `POS_PCROUT`/`POS_AN_OUT` já está confirmado nos dados (só com agente detectado);
   - `DT_PCR`, `DT_RES_AN`, `SG_UF` vs `SG_UF_NOT`;
   - campos da ficha 2025/2026 (imunização).
   - Indicadores: em `qc_por_ano.csv`, `painel` ≈ 0 em algum ano ⇒ a regra do denominador precisa de revisão; em `qc_colunas_por_arquivo.csv`, as colunas ausentes.
3. ~~População de menores de 2 anos~~: feito em 20/10/2026 pelo SINASC (`ref/baixar_sinasc.py` → `data/external/populacao.csv`, NV(a−1) + NV(a−2)); ligada no 01 (`pop_menores2`, `*_taxa100k`).
4. Decidir `vacina_materna_ini`. `semana_inicio_temporada`: manter SE 1.
5. ~~Filtro de UF por critério.~~ Feito.
6. Agendar o download semanal para acumular snapshots reais.

## 6. Próximos notebooks

- `02_eda` (**iniciado**, gerado por `ref/build_eda.py`; funções reutilizáveis em `vsr_comum.py`; saída `eda_marcos_temporada.parquet`). Feito: séries, averiguação, marcos *a priori* (início/fim = fração acumulada 0,10/0,90; pico = argmax da MM3), ordem regional, fev–ago e janela mínima de 90%, tiers, completude, 2026 × climatologia. Pendente:
  - ~~confirmar a definição *a priori* de início/pico~~: aceita pela usuária em 20/10/2026 (0,10/0,90 e MM3), fixada;
  - ~~sensibilidade dos marcos (MEM, limiar de taxa)~~: feito (§10.3, §12.6): MEM antecipa o início 3–4 semanas (limiares não estacionários com averiguação crescente); taxa fica a 1–2 semanas, mas sem início em 12–24% das unidade-temporadas;
  - curva cobertura × duração da janela (base de `06_decisao`);
  - ~~marcos de 2026 recalculados após nowcasting~~: feito (`nowcast_aplicado`).
- `03_nowcasting` (**feito, 20/10/2026**; `ref/build_nowcast.py`, `vsr_nowcast.py`, ~1 min): `gamma_rw` (Gamma-Poisson, passeio aleatório + tendência, superdispersão Gama, $F$ por chain-ladder nas últimas 52 semanas; ajustado em 2023). WIS log relativo ao chain-ladder antigo 0,85 (BR), 0,72 (regiões), 0,66/0,42/0,39 (UF A/B/C); cobertura de 90% ~0,90–0,95. **Integrado** ao `vsr_modelos` (`serie_origem`): mediana nas semanas o − 26 … o − 1, amostras como ponto de partida de NB/LightGBM e das trajetórias dos marcos. A semana d = 0 continua fora da série (`DESCARTE_RECENTE = 1`, decisão empírica: com ela h = 0 e h ≥ 1 pioram); o `nowcast` entra no ensemble em h = 0. METODOLOGIA §6.1, §11.1.
- `04_modelos` (**refeito, 20/10/2026**; `ref/build_modelos.py`, ~10 min): 137 origens, h = 0–4, com nowcast `gamma_rw`, amostras conjuntas (Schaake shuffle: `nb_bottom_up_conj`, `nb_mint_conj`, `lgbm_global` conjunto) e `lgbm_taxa` (offset populacional). WIS relativo ao naive (validação BR/região/UF): ensemble 0,55/0,55/0,70; lgbm_global 0,61/0,57/0,59 (antes 0,68/0,60/0,61); lgbm_taxa ≈ lgbm_global (não substitui). Amostras conjuntas corrigem boa parte da subdispersão nos agregados (cobertura de 90% do MinT no Brasil 0,40 → 0,73) e melhoram o WIS do MinT (BR 0,81 → 0,69). H2: pooling (LightGBM 0,79× NB local nas UFs); MinT segue sem ganho. METODOLOGIA §11.5:
  - H1 sustentada (todos < naive sazonal; climatologia pior);
  - H2: o ganho vem do pooling (LightGBM global 0,83× o NB local nas UFs, 0,76× no tier C); MinT não ajuda;
  - H3: ensemble 1º–2º em todos os níveis e o único calibrado.
  - Pendências (§11.6): ~~marcos previstos~~ (feito em `05_marcos`); amostras conjuntas entre UFs (NB/MinT subdispersos nos agregados); ~~nowcast com incerteza~~ (feito em `03_nowcasting`, falta integrar); sensibilidade `srag`.
- Dependências novas: `statsmodels` 0.15.0, `lightgbm` 4.7.0 (API nativa, sem scikit-learn).
- `05_marcos` (**refeito, 20/10/2026**; `ref/build_marcos.py`, ~4 min): trajetórias com o nowcast amostrado, `forma_curva` (molde deslocado/esticado, grade τ × s, Poisson-Gama) e `ensemble_pond` (pesos 1/RPS das temporadas anteriores). Antecedência 1–8: erro ~2 semanas em início e pico (BR + regiões), 2–3 nas UFs A. Ensemble vence a climatologia; **nenhum modelo vence o naive sazonal** (ensemble: início 32/63, pico 25/63, p = 0,13 — antes o naive vencia no pico). `forma_curva` perde para o naive (pico 21/63, p = 0,01); `ensemble_pond` = ensemble. Sensibilidade à definição da verdade (MEM, taxa): conclusões mantidas. METODOLOGIA §12.
- `06_decisao` (**feito, 20/10/2026**; `ref/build_decisao.py`, ~1 min): LOSO 2023–2025. Fev–ago (30 sem) cobre 0,92–0,93; janela regional de mesma duração empata; janela regional de 90% perde cobertura (0,88/0,84) por 4–7 semanas a menos; **gatilho adaptativo** P(início ≤ s + 3) > 0,7 cobre 0,91/0,89 com 27/24 semanas (+12% a +22% de cobertura por semana), antecedência 3–5 semanas, sem falso alarme nos agregados. H4 = argumento de custo; evidência fraca (2 temporadas). METODOLOGIA §14.
- Sensibilidade `srag` (**feito, 20/10/2026**; `VSR_SERIE=srag`, `0N_*_srag.ipynb`): ranking mantido, ganho menor sobre o naive (ensemble 0,80/0,73/0,77); *timing* sem vitória sobre o naive. METODOLOGIA §11.8.
- Dependência nova: `scipy` 1.18.1 fixada (já vinha com statsmodels; usada no teste binomial).

## 7. Preferências da usuária

- Comunicação concisa e direta, em português.
- Saídas técnicas densas; Python de produção, modular, com notação matemática formal.
- Sem enquadramento didático.
