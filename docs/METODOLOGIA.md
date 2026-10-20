# Metodologia

Previsão probabilística do início e do pico da temporada de VSR nos estados brasileiros: reconciliação hierárquica e avaliação orientada à janela de imunização.

Documento vivo: descreve, na ordem de execução, cada requisição de dados, cada tratamento e cada definição usada do dado bruto até a avaliação dos modelos. Os números citados são da execução de 19/10/2026 (corte 18/10/2026) e mudam a cada republicação dos dados. O esquema das tabelas está em [`DICIONARIO_DADOS.md`](DICIONARIO_DADOS.md).

---

## 0. Reprodutibilidade

| Etapa | Artefato | Execução |
|---|---|---|
| Ambiente | `environment.yml` (versões diretas fixadas), `environment.lock.txt` (lock explícito win-64, com builds) | `conda env create -f environment.yml` ou `conda create -n vsr --file environment.lock.txt` |
| Checagem do ambiente | `vsr_comum.verificar_ambiente()` | chamada no início de cada notebook e de `validar_dados.py`; erro se alguma versão diferir da fixada |
| Dados | `01_dados_srag_vsr.ipynb` | `jupyter nbconvert --to notebook --execute --inplace 01_dados_srag_vsr.ipynb` (`VSR_OFFLINE=1`: reprocessa o cache sem rede) |
| População | `baixar_sinasc.py` | `python baixar_sinasc.py` → `data/external/populacao.csv` (antes do 01) |
| Validação externa | `validar_dados.py` | `python validar_dados.py` → `data/processed/qc_validacao_externa.csv` |
| EDA | `02_eda.ipynb` | idem ao 01 |
| Nowcast | `03_nowcasting.ipynb`; funções em `vsr_nowcast.py` (autoteste: `python vsr_nowcast.py`) | idem (~1 min) |
| Modelos | `04_modelos.ipynb`; funções em `vsr_modelos.py` (autoteste: `python vsr_modelos.py`) | idem (~10 min) |
| Marcos previstos | `05_marcos.ipynb` | idem (~4 min) |
| Decisão | `06_decisao.ipynb` | idem (~1 min) |
| Sensibilidade `srag` | `03/04/05_*_srag.ipynb` | `VSR_SERIE=srag jupyter nbconvert --execute 0N_x.ipynb --output 0N_x_srag.ipynb` |

Os notebooks versionados são a fonte executável. Durante o desenvolvimento eles foram gerados por scripts locais (nbformat), não versionados; editar o `.ipynb` diretamente é o caminho agora. Parâmetros do notebook 01 na dataclass `Config`, sobrescritos por JSON via a variável de ambiente `VSR_DATA_CONFIG`. O `metadata.json` registra a configuração usada, as versões do software, as fontes (URL, sha256, data de publicação) e o hash de cada saída.

---

## 1. Requisições: obtenção dos dados

**Fonte.** SRAG hospitalizada do SIVEP-Gripe, conjunto `srag-2019-a-2026` do OpenDataSUS (`https://dadosabertos.saude.gov.br`). Um arquivo por ano, `INFLUDaa-dd-mm-aaaa.parquet`, num bucket S3 (`s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/<ano>/`). O nome traz a data de publicação e muda a cada republicação; os anos correntes são republicados semanalmente.

**Descoberta dos links**, em três rotas, por ordem, até achar todos os anos; para cada ano fica a publicação mais recente encontrada:

1. API CKAN `package_show?id=srag-2019-a-2026`;
2. listagem do bucket S3 (`list-type=2`, prefixo `SRAG/<ano>/`);
3. páginas HTML do conjunto e de cada recurso.

Se nenhuma rota responder, usa-se `URLS_CONHECIDAS` (links fixos). Saídas manuais: `arquivos_manuais` ({ano: url}) e `arquivos_locais` ({ano: caminho}). Na execução de 19/10/2026, a API devolveu conteúdo não JSON e a listagem do S3 não é pública; as páginas funcionaram.

**Download.**
- Requisição HTTP em streaming, timeouts de 30 s (conexão) e 300 s (leitura).
- Até 6 tentativas, com espera de $\min(2^t, 60)$ s.
- Retomada por cabeçalho `Range` a partir do arquivo `.part`; se o servidor ignorar o `Range` (resposta ≠ 206), recomeça.
- Ao final, verificação do tamanho contra `Content-Length` e leitura dos metadados do Parquet (arquivo inválido é apagado).

**Cache.** Um arquivo existente, válido e com sha256 igual ao do manifesto anterior não é baixado de novo. O manifesto (`data/raw/SRAG/manifesto_download.json`) guarda origem, URL, bytes, sha256, data de publicação e `Last-Modified`. Se faltar algum ano de `anos_obrigatorios`, o pipeline **para**: semanas de anos ausentes nunca viram zero.

**Ainda não integrados:** InfoGripe (referência de atraso), INMET (covariáveis opcionais), SINASC/IBGE (população < 2 anos, formato `uf, ano, pop_menores2`, ativado por `arquivo_populacao`).

---

## 2. Leitura

- Colunas mapeadas por nomes canônicos e candidatos (`COLUNAS`); obrigatórias: `DT_SIN_PRI`, `DT_DIGITA`, `NU_IDADE_N`, `TP_IDADE`, `HOSPITAL`, `SG_UF_NOT`, e ao menos uma entre `PCR_VSR` e `AN_VSR`.
- Colunas extras por regex (`NIRSE|PALIV|VSR|ANTICORP|VAC.*GEST|GESTA`) viram `X_*`. Os 194 campos são idênticos de 2019 a 2026: **não há campo de nirsevimabe**, e só `CS_GESTANT` casa com a regex.
- Leitura por *row group*, com tudo como texto e o filtro de idade já aplicado: idade em meses $= n/30{,}4375$ (`TP_IDADE = 1`, dias), $n$ (2, meses) ou $12n$ (3, anos); mantém $0 \le$ idade $< 24$. Crianças de 1 ano registradas em anos têm idade = 12 meses exatos.
- `qc_colunas_por_arquivo.csv`: linhas por arquivo, linhas < 2 anos e colunas ausentes (`CO_UNI_NOT` falta em todos os anos e sai da chave de deduplicação).

---

## 3. Tratamentos

Ordem fixa; cada etapa registra as linhas restantes em `qc_filtros.csv`.

| # | Tratamento | Regra | Removidas (19/10/2026) |
|---|---|---|---|
| 1 | Datas | aceita `AAAA-MM-DD[ hh:mm:ss]` e `DD/MM/AAAA` na mesma coluna | — |
| 2 | Data de sintomas | `DT_SIN_PRI` ∈ [30/12/2018, maior data de publicação] | 0 |
| 3 | Deduplicação entre arquivos | chave `NU_NOTIFIC + DT_NOTIFIC + SG_UF_NOT + DT_SIN_PRI`; mantém a publicação mais recente | 0 (cada arquivo contém só o próprio ano epidemiológico) |
| 4 | Hospitalização | `HOSPITAL = 1` (ou vazio, com `incluir_hospital_ausente = True`; coluna `hospital_ausente`) | 16.249: 5.270 com `2`, 762 com `9`, 10.217 vazios (~2%/ano) |
| 5 | UF | residência = `SG_UF`; se inválida, pelo município IBGE (`CO_MUN_RES`, 2 primeiros dígitos); em último caso, a UF de notificação (`uf_res_imputada_notif`). Notificação = `SG_UF_NOT`, ou pelo `CO_MUN_NOT`. **Cada critério exige só a própria UF**: sem UF de notificação, o caso sai apenas do painel por notificação | 0 (191 casos sem UF de notificação, ficam no painel por residência) |
| 6 | Marcadores laboratoriais | ver §5.1 | — |
| 7 | Disponibilidade | ver §3.1 | — |
| 8 | Semana completa | painel termina na última semana com sábado ≤ data de corte | 1 |

Perda total bruto → base limpa < 0,02% por ano, só por idade negativa (excluída na leitura). A recontagem independente em `validar_dados.py` confirma VSR idêntico ao bruto em todos os anos.

### 3.1 Datas de disponibilidade

- Registro: $a^{\text{reg}} =$ `DT_DIGITA` se estiver em $[t_{\text{sint}} - 7\text{ d},\ \text{publicação}]$; senão `DT_NOTIFIC` em $[t_{\text{sint}},\ \text{publicação}]$ (`disp_reg_imputada`). Em 2019, 34% não têm `DT_DIGITA`.
- Resultado de VSR: o menor entre `DT_PCR` (se `PCR_VSR = 1`) e `DT_RES_AN` (se `AN_VSR = 1`), dentro de $[t_{\text{sint}},\ \text{publicação}]$.
- Disponibilidade do VSR: $a^{\text{vsr}} = \max(a^{\text{reg}},\ \text{data do resultado})$.
- **Data de corte** $=\max a^{\text{reg}}$ (18/10/2026). `DT_DIGITA` não registra edições posteriores do registro, então a reconstrução em tempo real é otimista.

### 3.2 Calendário

Semana epidemiológica de domingo a sábado; o ano e o número da SE são os da quarta-feira da semana (padrão MMWR/MS). 2020 e 2025 têm SE 53. Faixas etárias: 00–05, 06–11 e 12–23 meses, mais o total `00-23m` (soma).

---

## 4. Cobertura temporal e períodos

A cobertura da semana $k$ do ano $Y$ é $\sum_\delta P(\text{arquivo } Y{+}\delta \mid k)\,\mathbb 1[\text{arquivo presente}]$, com $P$ estimada nos anos cujos arquivos vizinhos estão presentes. Uma semana com cobertura < 0,99 tem `coberta = False` e contagens NaN (hoje: nenhuma).

Períodos: `pre_pandemia` (< 01/03/2020), `quebra_npi` (até 31/12/2021), `pos_pandemia` e `imunizacao` (≥ 01/02/2026, nirsevimabe). `vacina_materna` fica inativa até `vacina_materna_ini` ser definida.

---

## 5. Painel semanal

Grão: `criterio_uf` (residência, principal; notificação, sensibilidade) × `nivel` × `unidade` × `faixa_etaria` × `semana`. As contagens são feitas por UF e faixa; região, Brasil e `00-23m` são somas. A hierarquia é coerente por construção, $\mathbf y = S\,\mathbf y_{\text{UF}}$ (matriz $S$ em `matriz_S.csv`).

### 5.1 Definições

- **Caso:** SRAG hospitalizada, idade < 24 meses, semana de primeiros sintomas $t$.
- **VSR confirmado (`vsr`, alvo principal):** `PCR_VSR = 1` ou `AN_VSR = 1`. **Alvo de sensibilidade:** `srag`, menos afetada pela expansão da testagem.
- **`lab_resultado`:** `PCR_RESUL` ou `RES_AN` ∈ {1, 2}, ou VSR positivo (83 positivos com código fora de {1, 2}). Fração da SRAG com resultado, de 2019 a 2026: 0,85 · 0,86 · 0,88 · 0,91 · 0,93 · 0,93 · 0,93 · 0,91.
- **`testado_painel`:** VSR positivo, ou `POS_PCROUT` ∈ {1, 2}, ou `POS_AN_OUT` ∈ {1, 2}. Achado: `POS_PCROUT`, `POS_PCRFLU`, `PCR_FLUASU`, `PCR_VSR` e `POS_AN_OUT` **só são preenchidos quando o teste detectou algum agente** (0% de preenchimento com `PCR_RESUL = 2`, 93–99% com `PCR_RESUL = 1`). Logo, `testado_painel` ≈ teste com agente detectado e exclui os negativos.

### 5.2 VSR corrigido pela testagem: limites

Para a UF $u$, a faixa $f$ e o denominador $n \in \{\texttt{lab\_resultado}, \texttt{testado\_painel}\}$, com janela *trailing* de $w = 3$ semanas e pseudo-contagem $\kappa = 5$:

$$\tilde p_{u,t} = \frac{\sum_{k=0}^{w-1} y_{u,t-k} + \kappa\,\bar p_{r(u),t}}{\sum_{k=0}^{w-1} n_{u,t-k} + \kappa},\qquad \hat y_{u,t} = \max\!\big(N_{u,t}\,\tilde p_{u,t},\ y_{u,t}\big),$$

com $y$ = VSR positivos, $N$ = SRAG, e $\bar p_{r(u),t}$ = positividade da região na mesma janela (fallback: Brasil).

- `vsr_est_inf` ($n$ = `lab_resultado`) é **limite inferior**: os negativos de testes só para SARS-CoV-2 deprimem $\tilde p$.
- `vsr_est_sup` ($n$ = `testado_painel`) é **limite superior**: exclui todos os negativos.
- Garantido $y \le$ inf $\le$ sup (verificado). Fração da SRAG em 2023–2026: `vsr` 0,28–0,37; inf 0,32–0,41; sup 0,52–0,56.
- Nunca usar os limites como alvo pontual.

---

## 6. Atraso de notificação e completude

**Triângulo.** $z_{u,f,t,d}$ = casos com sintomas na semana $t$ disponíveis $d$ semanas depois (VSR por $a^{\text{vsr}}$, SRAG por $a^{\text{reg}}$). Atrasos negativos viram 0 (47 casos de SRAG, contados em `metadata.qc_atraso`); $d \ge D = 26$ vai para o último *bucket*.

**Versão em tempo real** na origem $o$ (um domingo):
$$y^{(o)}_{u,t} = \sum_{d=0}^{\lfloor (o-t)/7 \rfloor} z_{u,t,d}$$
(código no §11 do notebook 01).

**Completude.**
- $F_r(d)$ = fração acumulada por região, estimada só com semanas maturadas ($t + D \le$ corte) dos 3 anos mais recentes.
- VSR no Brasil: $F(0) = 0{,}09$, $F(1) = 0{,}57$, $F(3) = 0{,}87$, $F(8) = 0{,}97$.
- `completude_esperada_*` $= F_r(\text{atraso\_ate\_corte})$; `semana_incompleta` ⇔ completude esperada do VSR < 0,95.
- **O nowcasting é obrigatório** nas ~5 semanas finais.

### 6.1 Nowcast probabilístico (`03_nowcasting.ipynb`, `vsr_nowcast.py`)

Rodado em 20/10/2026. Análogo conjugado de Bastos et al. (2019), sem INLA, por unidade $u$ (33, sem reconciliação):
$$y^{(o)}_t \mid \lambda_t \sim \text{Pois}(\lambda_t F(d)), \qquad N_t - y^{(o)}_t \mid \lambda_t, \varepsilon \sim \text{Pois}(\varepsilon \lambda_t (1 - F(d))), \qquad \varepsilon \sim \text{Gama}(\varphi, \varphi)$$
$$\lambda_t \sim \text{Gama}(k, k/m_t), \quad m_t = \hat\lambda_{t-1}\, g_t \;\Rightarrow\; \lambda_t \mid y_t \sim \text{Gama}(k + y_t,\ k/m_t + F(d))$$
- **Passeio aleatório no nível** (força $k$; $k = 0$: *a priori* plana) com tendência $g_t = \hat\lambda_{t-1}/\hat\lambda_{t-2}$ limitada a [2/3, 1,5]; $\hat\lambda$ = média da posteriori, filtrada de $o - D$ até $o$. Cada semana é amostrada da sua posteriori marginal (400 amostras), sem a correlação entre semanas.
- **$\varepsilon$:** superdispersão que absorve o erro de $F$ (o modelo só Poisson tem cobertura de 90% de ~0,7 nos agregados).
- **$F_r(d)$**, da região (UFs e regiões) ou do Brasil, por duas fontes: `maturado` (semanas com $t + D \le o$ dos 3 anos anteriores, igual a §11.1) e `janela` (chain-ladder com fatores de desenvolvimento nas últimas 52 semanas, incluindo as incompletas).
- **Desenho:** grade ajustada nas 53 origens de 2023 (fora da avaliação): $k \in \{0, 3, 10\}$, tendência sim/não, $\varphi \in \{\infty, 8, 4\}$. Escolha fixada antes da avaliação: `gamma_rw` = janela, $k = 10$, tendência, $\varphi = 4$ (configurações com $k \ge 3$ e $\varphi \in \{4, 8\}$ diferem pouco).
- **Avaliação:** 146 origens (31/12/2023 a 11/10/2026), $d = 0, \dots, 8$, contra o total final do triângulo; recorte principal só com verdade madura ($t + D \le$ última semana). Comparadores: `sem_correcao`, `chain_ladder` (pipeline atual, *a priori* plana, Poisson) e `cl_janela` (janela, *a priori* plana, $\varphi = 4$).

**Resultados ($d \le 3$, verdade madura):**

| | WIS log BR + regiões | cob. 90% | WIS log UF | cob. 90% | WIS rel. (IC 95%) |
|---|---|---|---|---|---|
| `sem_correcao` | 0,716 | 0,09 | 0,463 | 0,45 | — |
| `chain_ladder` | 0,148 | 0,65 | 0,285 | 0,82 | 1 |
| `cl_janela` | 0,131 | 0,94 | 0,251 | 0,92 | 0,85–0,92 |
| `gamma_rw` | **0,108** | 0,95 | **0,148** | 0,90 | BR 0,85 (0,79–0,90); regiões 0,72; UF A 0,66; B 0,42; C 0,39 |

**Leitura:**
1. O ganho cresce quanto menor a contagem: em $d = 0$, WIS log nas UFs de 0,73 (chain-ladder) para 0,28. O chain-ladder com *a priori* plana superestima as UFs pequenas (viés log +0,97 em $d = 0$: $(y + 1)/F$ com $F(0) \approx 0{,}09$); o passeio aleatório encolhe para o nível das semanas anteriores.
2. A fonte `janela` corrige o viés do $F$ maturado (atraso mudou desde 2023) e, com $\varphi$, dá a calibração: ~0,94 de cobertura de 90% nos agregados.
3. Viés residual do `gamma_rw`: −0,10 em $d = 0$ nos agregados e −0,19 nas UFs (atraso da tendência em subidas). O ganho sobre o chain-ladder é estável por ano (WIS rel. 0,52 em 2024, 0,55 em 2025, 0,65 em 2026).
4. **Integração** (feita, §11.1 e §12.1): `vsr_modelos.serie_origem` usa a mediana do `gamma_rw` nas semanas $o - 26, \dots, o - 1$ e as amostras como ponto de partida das trajetórias; os marcos de 2026 em `eda_marcos_temporada` usam a mediana do nowcast nas semanas incompletas (`nowcast_aplicado`).

---

## 7. Temporadas, climatologia e período 2020–2021

**Temporadas.**
- `temporada` = ano epidemiológico, ou deslocado por `semana_inicio_temporada[regiao]`.
- `semana_temporada` vai de 1 a 53, contada a partir do domingo da SE de início, **sem achatar a SE 53**.
- Nenhuma temporada recente cruza a virada do ano: de 2024 em diante, ≤ 4% do VSR regional cai nas SE 1–4 e 49–53. As exceções são 2022–2023, com atividade nas bordas de até 0,21 (Centro-Oeste), no pós-pandemia. A SE 1 é mantida como início da temporada.

**Temporadas de referência:** ≥ 90% das semanas cobertas, ≥ 52 semanas, fora de 2020–21 e da era de imunização ⇒ 2019, 2022, 2023, 2024 e 2025.

**Climatologia** por unidade × faixa × série × `semana_temporada`: média e desvio-padrão de $\log(1+y)$, e média da fração da temporada $y_k / \sum y$.
- `expanding`: para a temporada-alvo $S$, só temporadas de referência $< S$ (sem vazamento; é a única usada em modelos).
- `referencia`: todas as temporadas de referência (EDA).
- SE 53: usa as temporadas que a têm; se nenhuma tiver, usa a média das semanas 52 e 1 (`n_temporadas = 0`).

**2020–21 (`quebra_npi`).** Os valores observados nunca são sobrescritos. São criadas séries imputadas por $\exp(\overline{\log(1+y)}_k) - 1$:
- `*_imput_passado`: climatologia `expanding`, que na prática usa só 2019;
- `*_imput_ref`: climatologia `referencia`, só para EDA, nunca em avaliação fora da amostra.

---

## 8. Hierarquia, tiers e população

- **Hierarquia:** Brasil → 5 regiões → 27 UFs (`hierarquia_unidades.csv`, `matriz_S.csv`).
- **Tiers** (regra *a priori* sobre o VSR por residência, mínimo em 2023–2025):
  - A ≥ 300: modelável por UF (15 UFs);
  - B ≥ 100: só com *pooling*, início e pico avaliados na região (7 UFs);
  - C < 100: só no agregado regional (RR, AL, MT, PI, TO).
- **População < 2 anos** (`baixar_sinasc.py` → `data/external/populacao.csv`, 20/10/2026):
  - fonte: SINASC no OpenDataSUS (`SINASC/csv/SINASC_{ano}_csv.zip`, nascidos vivos por UF de residência da mãe, `CODMUNRES`);
  - $\text{pop\_menores2}(a) = NV(a-1) + NV(a-2)$, sem mortalidade infantil (~1,3% de superestimação) nem migração;
  - Brasil: 5,87 milhões (2019) → 4,85 milhões (2026); nascidos vivos 2,92 milhões (2017) → 2,39 milhões (2024) e 2,46 milhões (2025, preliminar); 0 registros sem UF;
  - o 01 anexa `pop_menores2` e `*_taxa100k` (faixa `00-23m`); rodado de novo com `VSR_OFFLINE=1` (sem rede, mesmo cache): painel idêntico ao anterior fora das colunas novas.
- **Taxas:** por 100 mil menores de 2 anos (`vsr_taxa100k` etc.).

---

## 9. Validações

**Internas** (notebook 01; qualquer falha interrompe a execução):
- chave do painel única;
- BR = Σ regiões = Σ UFs, e Σ faixas = total, nos dois critérios;
- triângulo soma ao painel (VSR e SRAG);
- semanas sem arquivo são NaN;
- limites ≥ `vsr`;
- positividades em [0, 1];
- (temporada, semana_temporada) único, em 1..53;
- painel termina na última semana completa;
- triângulo sem atraso negativo.

**Externas** (`validar_dados.py`, recontagem a partir do bruto):
- perda bruto → base limpa < 0,2%/ano;
- VSR por ano igual ao bruto (diferença ≤ 25);
- cada arquivo contém só o próprio ano;
- base limpa → painel idêntico;
- semanas contíguas;
- SE 53 sem colisão e presente na climatologia;
- $y \le$ inf $\le$ sup;
- última semana completa;
- atrasos negativos < 0,1%.

---

## 10. Análise exploratória (`02_eda.ipynb`)

### 10.1 Definições *a priori* de início, pico e fim (`vsr_comum.py`)

Para cada unidade × temporada × série, com $C_k = \sum_{j \le k} y_j / \sum_j y_j$:

- **início** $= \min\{k : C_k \ge 0{,}10\}$, **fim** $= \min\{k : C_k \ge 0{,}90\}$;
- **pico** $= \arg\max_k \bar y_k$, com $\bar y$ = média móvel centrada de 3 semanas;
- **janela mínima**: menor intervalo contíguo com ≥ 90% dos casos da temporada;
- **fração em fev–ago**: casos com semana de sintomas (mês da quarta-feira) de fevereiro a agosto.

As definições são relativas ao total da temporada e, portanto, invariantes à escala de averiguação, que cresceu 10–100× por UF entre 2019 e 2025. O MEM e o limiar de taxa entram como sensibilidade (§10.3). 2026 está em curso (`completa = False`): seus marcos são relativos ao observado até o corte. Saída: `eda_marcos_temporada.parquet`, que é o alvo de avaliação de início e pico dos modelos.

### 10.2 Achados (19/10/2026)

- **Averiguação não estacionária:** `vsr/srag` por UF passa de ≤ 0,09 (MT, AL, RO, PE até 2022) para 0,17–0,50 em 2025. As temporadas comparáveis para a maioria das UFs são 2023–2026.
- **Ordem regional** (média 2023–2025, SE):
  - início: Centro-Oeste 8,3 · Sudeste 9,7 · Sul 13,0 · Nordeste 14,0 · Norte 14,7;
  - pico: Centro-Oeste e Sudeste 15,3 · Sul 20,0 · Nordeste 20,3 · Norte 21,7.

  **O Norte e o Nordeste são tardios**, ao contrário da premissa da proposta (JOGH, 2026). Exceção: em 2026, o início no Norte foi na SE 6 (o mais cedo). É preciso confrontar com a literatura antes de reescrever a motivação.
- **Janela fev–ago:** 71% dos pares UF-temporada de 2022–2025 têm ≥ 90% do VSR dentro da janela. A janela mínima com 90% dura tipicamente 14–24 semanas, contra ~30 de fev–ago. **H4 vira argumento de custo** (janela regional mais curta com a mesma cobertura). 2025 é o contraexemplo: AM (0,56), SE (0,59), PB, RN e RO com temporada tardia.
- **Esparsidade:** 61–71% das semanas com zero VSR em MT, PI e TO (tier C); o desvio-padrão do pico entre temporadas (2022+) passa de 5 semanas em SP e MG (pela temporada atípica de 2022), RR, MT, AM e PA.
- **Concordância de alvos:** diferença de pico entre `vsr` e `srag` com mediana 0 e IQR [−1, 0] semanas por região.

### 10.3 Sensibilidade das definições de início e fim (20/10/2026)

Definições alternativas, série `vsr`, temporadas 2022–2026 (`vsr_comum.mem_periodo`, `mem_limiar`, `inicio_fim_limiar`; colunas `*_mem`, `*_taxa` e `limiar_mem` em `eda_marcos_temporada`):

- **MEM** (Vega et al., 2013), implementado em Python:
  - período epidêmico de cada temporada = janela de duração $r$ com a maior soma, com $r$ = menor duração em que alongar uma semana acrescenta < 2,8% do total;
  - limiar = limite superior do IC 95% unilateral (t de Student) da média geométrica, em $\log(1+y)$, dos $n = \text{round}(30/\#\text{temporadas})$ maiores valores pré-epidêmicos das **outras** temporadas completas de 2022–2025;
  - início/fim = 1ª/última semana com $y \ge$ limiar.
- **Limiar de taxa:** um único limiar de 17,6 casos por 100 mil menores de 2 anos por semana (mediana da taxa na semana de início *a priori* nas UF-temporadas 2023–2025; população do SINASC, §8). Início/fim = 1ª/última semana com taxa ≥ limiar.

**Concordância com a definição *a priori*** (mediana |diferença|, em semanas; fração a ±2 semanas):

| Nível | início MEM | fim MEM | início taxa | fim taxa |
|---|---|---|---|---|
| Brasil | 3 (0,40) | 4 (0,20) | 1 (0,60) | 2,5 (0,40) |
| Regiões | 4 (0,16) | 4 (0,28) | 2 (0,56) | 3 (0,40) |
| UFs | 4 (0,30) | 4 (0,29) | 1 (0,59) | 2 (0,40) |

- O **MEM antecipa o início** (Sudeste e Sul: SE 1 em 2022–2023), porque os limiares das temporadas de 2022–2023, de averiguação baixa, são baixos para 2024+. Com averiguação crescente, um limiar absoluto não é estacionário: esse é o motivo da definição relativa *a priori*.
- **Taxa:** mais próxima da *a priori*, mas sem início em 12–24% das unidade-temporadas (o limiar nunca é atingido; 2022 inteira no Brasil).
- Reavaliação dos marcos previstos contra essas verdades: §12.6.

---

## 11. Modelagem e avaliação (`04_modelos.ipynb`, `vsr_modelos.py`)

Implementada e rodada em 20/10/2026 sobre os dados com corte em 18/10/2026. Os números abaixo mudam a cada nova execução.

### 11.1 Dados em tempo real na origem

Em cada origem $o$ (domingo, de 31/12/2023 a 09/08/2026, 137 origens semanais):

1. **Versão:** $y^{(o)}_{u,t} = \sum_{d \le \lfloor (o-t)/7 \rfloor} z_{u,t,d}$, a partir do triângulo de VSR (residência, faixas somadas).
2. **Nowcast `gamma_rw`** (§6.1) nas semanas $o - 26, \dots, o$: 400 amostras por unidade (33 unidades, cada uma com o próprio nowcast; os agregados não são a soma das UFs). A série de treino usa a mediana; semanas com $t < o - 26$ usam a versão (todos os atrasos conhecidos, exceto o *bucket* ≥ 26 semanas, efeito desprezível). Substitui o chain-ladder multiplicativo com $F$ maturado (até 20/10/2026).
3. **Semana da origem fora da série** (`DESCARTE_RECENTE = 1`): decisão empírica. Com a mediana do nowcast de $d = 0$ ($F(0) \approx 0{,}09$) como dado, o $h = 0$ piorou (WIS log validação: Brasil 0,140 → 0,176; UF 0,262 → 0,280) e o $h = 1$–4 também (regiões 0,278 → 0,290). Os modelos preveem $h = 0, \dots, 4$ a partir de $o - 1$; NB e LightGBM partem de cada amostra do nowcast de $o - 1$ (propagação da incerteza); ETS, ARIMA e naive partem da mediana.
4. **`nowcast`:** as amostras do `gamma_rw` da semana $o$, como modelo em $h = 0$ e membro do ensemble só em $h = 0$.
5. **Janela de treino:** as 156 semanas anteriores, nunca antes de 02/01/2022 (fora de `quebra_npi`; 2019 tem averiguação baixa).
6. **Unidades:** 27 UFs, mais regiões e Brasil como somas ($\mathbf y = S\,\mathbf y_{\text{UF}}$).

**Alvos:** o `vsr` final da semana $o + 7h$, para $h = 0, \dots, 4$ (H1–H3 usam $h = 1$–4). Não se avaliam alvos com `semana_incompleta` no corte atual (último alvo: 06/09/2026).

**Períodos de avaliação:**
- *validação*: alvos nas temporadas 2024 e 2025. Não houve ajuste de hiperparâmetros nelas, então funcionam também como teste.
- *teste_2026*: fora de distribuição, primeira temporada com imunização.

### 11.2 Modelos

Todos geram 400 amostras por unidade e horizonte; os 23 quantis (0,01, 0,025, 0,05, …, 0,95, 0,975, 0,99) saem das amostras.

| Modelo | Unidades | Especificação |
|---|---|---|
| `naive_sazonal` (baseline) | todas | $y_{t+h} = y_{t+h-52}$; incerteza por bootstrap dos resíduos sazonais $\log(1+y_t) - \log(1+y_{t-52})$ |
| `climatologia` | todas | $\exp(\mathcal N(\mu_k, \sigma_k)) - 1$ com a climatologia `expanding` da temporada do alvo (só temporadas anteriores) |
| `ets` | regiões, BR | ETS(A,Ad,N) em $\log(1+y)$, simulação do espaço de estados |
| `arima_fourier` | regiões, BR | ARIMA(2,0,0) com constante e Fourier $K = 2$ (período 52,18) em $\log(1+y)$. Substitui o SARIMA com $s = 52$, instável em séries semanais com 2–3 anos (regressão harmônica dinâmica) |
| `nb_local` | todas | binomial negativa NB2: $\log \mu_t = \beta_0 + \text{Fourier}_{K=2}(t) + \gamma \log(1 + y_{t-1})$, ajuste por unidade; simulação recursiva das trajetórias. Fallback: Poisson (sem convergência ou com $\alpha \notin (0, 50)$); série toda nula ⇒ zeros |
| `lgbm_global` | UFs (+ somas) | LightGBM global com objetivo Poisson e UF categórica, um modelo direto por passo. Atributos: $\log(1+y)$ em $t, \dots, t-3$; média de 4 semanas; total da região em $t$; Fourier da semana-alvo. Dispersão NB $\alpha$ por passo, por momentos, nas últimas 26 semanas, com um modelo ajustado sem elas (fora da amostra) |
| `lgbm_taxa` | UFs (+ somas) | `lgbm_global` com atributos em taxa por 100 mil (população do SINASC, §8) e offset $\log(\text{pop}/10^5)$ no objetivo Poisson: $\log \mu = \log(\text{pop}/10^5) + f(x)$ |
| `nowcast` | todas | $h = 0$ apenas: amostras do `gamma_rw` da semana da origem (§6.1) |
| `ensemble` | todas | média dos quantis de `naive_sazonal`, `climatologia`, `nb_local`, `lgbm_global` e, onde existem, `ets`, `arima_fourier` e (em $h = 0$) `nowcast` |

### 11.3 Estratégias hierárquicas (H2)

Todas usam a NB harmônica como modelo base, exceto a global.

1. **`nb_local`:** cada uma das 33 unidades é ajustada sozinha (previsões incoerentes).
2. **`nb_bottom_up`:** soma amostra a amostra das UFs.
3. **`nb_mint`:** MinT-shrink sobre amostras, $\tilde{\mathbf y} = S G \hat{\mathbf y}$ com $G = (S^\top W^{-1} S)^{-1} S^\top W^{-1}$.
   - $W$: covariância dos resíduos de um passo no treino, com *shrinkage* para a diagonal (Schäfer e Strimmer, 2005, como em `hts`) e piso de 0,01 na diagonal.
   - Contagens negativas após a projeção são truncadas em 0.
4. **`lgbm_global`:** modelo global nas UFs, com bottom-up para os agregados.
5. **Amostras conjuntas** (`_conj`; `vm.schaake`): Schaake shuffle (Clark et al., 2004). As N amostras de cada unidade e passo são reordenadas pelos postos de N blocos de semanas consecutivas de resíduos de um passo do NB no treino (33 unidades × passos). Marginais intactas; a dependência entre unidades e entre horizontes passa a ser a do molde. Aplicado ao `nb_local` antes de `nb_bottom_up_conj` e `nb_mint_conj`, e às UFs do `lgbm_global` / `lgbm_taxa` antes do bottom-up (molde do NB: atalho). As versões independentes `nb_bottom_up` e `nb_mint` ficam para comparação.

A coerência das contagens não implica coerência de pico ou início (que serão derivados das trajetórias).

### 11.4 Métricas

- **WIS:** $\text{WIS} = \frac{2}{K}\sum_k \rho_{\tau_k}(y - q_k)$ com os 23 quantis (Bracher et al., 2021), em escala log, $\log(1+\cdot)$ em quantis e observado (principal; Bosse et al., 2023), e em escala natural.
- **WIS relativo:** média do modelo ÷ média do `naive_sazonal` no conjunto comum de alvos de cada recorte. Com sobreposição completa, é idêntico ao WIS relativo pareado de Cramer et al. (2022), porque as médias geométricas sobre os demais modelos se cancelam. IC 95% por bootstrap das origens (1.000 reamostragens, preserva a dependência entre unidades e horizontes da mesma origem).
- **Cobertura** empírica dos intervalos centrais de 50% e 90%.

### 11.5 Resultados (h = 1–4; WIS relativo em log ao naive sazonal, IC 95%)

Rodada de 20/10/2026 com o nowcast `gamma_rw` e as amostras conjuntas (137 origens, 31/12/2023 a 09/08/2026).

| Modelo | BR valid. | Região valid. | UF valid. | BR 2026 | Região 2026 | UF 2026 |
|---|---|---|---|---|---|---|
| ensemble | **0,55** [0,49; 0,62] | **0,55** [0,52; 0,58] | 0,70 [0,69; 0,72] | 0,49 | 0,45 | 0,60 |
| lgbm_global | 0,61 [0,51; 0,73] | 0,57 [0,52; 0,62] | **0,59** [0,56; 0,62] | 0,52 | 0,41 | 0,49 |
| lgbm_taxa | 0,61 [0,52; 0,71] | 0,57 [0,52; 0,63] | 0,60 [0,56; 0,63] | 0,53 | **0,39** | **0,48** |
| nb_local | 0,74 | 0,66 | 0,75 | 0,53 | 0,45 | 0,58 |
| nb_bottom_up | 0,71 | 0,78 | 0,75 | **0,42** | 0,58 | 0,58 |
| nb_bottom_up_conj | 0,67 | 0,77 | 0,75 | **0,42** | 0,57 | 0,58 |
| nb_mint | 0,81 | 0,75 | 0,73 | 0,51 | 0,62 | 0,64 |
| nb_mint_conj | 0,69 | 0,71 | 0,73 | 0,48 | 0,58 | 0,64 |
| arima_fourier | 0,77 | 0,67 | — | 0,57 | 0,47 | — |
| ets | 0,81 | 0,77 | — | 1,08 | 0,68 | — |
| climatologia | 1,55 | 1,36 | 1,15 | 1,48 | 1,18 | 1,02 |

Comparação com a rodada anterior (chain-ladder com $F$ maturado, amostras independentes): ensemble 0,56/0,55/0,69 → 0,55/0,55/0,70; `lgbm_global` 0,68/0,60/0,61 → 0,61/0,57/0,59.

- **H1 sustentada:** todos os modelos de previsão superam o naive sazonal, com IC abaixo de 1 (exceto ETS no Brasil e a climatologia).
- **Nowcast `gamma_rw` na série:** ganho concentrado no LightGBM (−0,07 no Brasil; −0,02 nas UFs), que parte de cada amostra do nowcast; ensemble praticamente inalterado.
- **H2:**
  - o *pooling* segue como a fonte do ganho: `lgbm_global` 0,79× o `nb_local` nas UFs na validação (0,84 em A, 0,76 em B, **0,71 em C**), 0,85 em 2026;
  - MinT continua sem ganho nas UFs (0,97–0,98) e piora nas regiões (1,07–1,13).
- **Amostras conjuntas (item 2):** a subdispersão nos agregados vinha da independência entre UFs. Cobertura de 90% em $h = 1$–4 (validação + 2026): `nb_mint` 0,40 → 0,73 (BR) e 0,55 → 0,69 (regiões); `nb_bottom_up` 0,58 → 0,72 e 0,61 → 0,69. O WIS melhora junto: `nb_mint` no Brasil 0,81 → 0,69, nas regiões 0,75 → 0,71. Nas UFs nada muda (marginais idênticas). Ainda abaixo de 0,9: o molde de 156 semanas não cobre a incerteza de parâmetros.
- **`lgbm_taxa` (item 7):** igual ao `lgbm_global` na validação; levemente melhor em 2026 (regiões 0,39 vs 0,41; UF 0,48 vs 0,49, dentro do IC). O offset populacional não traz ganho porque a população varia pouco na janela de 3 anos (−7% de 2023 a 2026) e o LightGBM já usa o nível recente. Fica como alternativa, não substitui o `lgbm_global` nem entra no ensemble.
- **Calibração do ensemble:** cobertura de 90% de 0,97 (BR), 0,94 (regiões) e 0,85 (UF) na validação; 1,00/0,96/0,89 em 2026.
- **$h = 0$:** `lgbm_global` é o melhor (WIS log validação 0,135/0,201/0,244; WIS relativo 0,37/0,40/0,47); o `nowcast` sozinho é pior (0,176/0,238/0,280; relativo 0,48/0,47/0,54), mas o mais bem calibrado em UFs. O ensemble com o `nowcast` como membro fica entre os dois (0,138/0,219/0,281; cobertura de 90% 0,90–0,99).
### 11.6 Pendências da modelagem

1. ~~Início e pico previstos~~ (§12). 2. ~~Amostras conjuntas~~ (Schaake shuffle, §11.3). 3. ~~Nowcasting com incerteza~~ (§6.1, §11.1). 4. ~~Sensibilidade `srag`~~ (§11.8). 5. ~~Decisão~~ (§14).
6. Molde de dependência próprio do LightGBM (resíduos fora da amostra) e incerteza de parâmetros nas amostras conjuntas (cobertura de 90% ainda ~0,7 nos agregados).
7. Combinar o `nowcast` de $o$ com as previsões de $h = 0$ como *prior* (o `gamma_rw` usa um passeio aleatório como *prior*; a previsão dos modelos a partir de $o - 1$ seria um *prior* melhor).

### 11.7 Regras

- Treinar só com dados da versão na origem; nunca usar `*_imput_ref` nem os limites `vsr_est_*` como alvo.
- Usar só a climatologia `expanding`. Ela usa o valor final das temporadas anteriores; em origens de janeiro, as últimas semanas da temporada anterior ainda não estavam completas, uma vantagem pequena para a climatologia, que mesmo assim é o pior modelo.
- Semente fixa (`SEMENTE = 20261019`, mais o índice da origem): o backtest é reprodutível.

### 11.8 Sensibilidade com `srag` como alvo (item 3)

Rodada de 20/10/2026: 03, 04 e 05 com `VSR_SERIE=srag` (triângulo, climatologia e verdade dos marcos da série `srag`; nowcast com os mesmos hiperparâmetros de `vsr`). Notebooks `0N_*_srag.ipynb`; saídas com sufixo `_srag`.

| Métrica (validação) | `vsr` | `srag` |
|---|---|---|
| WIS rel. ao naive, ensemble (BR/região/UF) | 0,55/0,55/0,70 | 0,80/0,73/0,77 |
| WIS rel. ao naive, lgbm_global | 0,61/0,57/0,59 | 0,76/0,73/0,73 |
| WIS rel. ao naive, climatologia | 1,55/1,36/1,15 | 2,82/2,27/1,62 |
| cob. 90% ensemble | 0,97/0,94/0,85 | 0,97/0,97/0,94 |
| nowcast d ≤ 3, WIS log agregados/UF: chain-ladder → `gamma_rw` | 0,148/0,285 → 0,108/0,148 | 0,077/0,175 → 0,062/0,140 |
| marcos, EA ensemble vs naive (início/pico/fim, sem) | 1,97/2,50/4,16 vs 2,09/2,52/4,34 | 1,58/2,21/1,32 vs 1,73/2,67/1,79 |
| sinal ensemble vs naive (início/pico/fim) | 32/63, 25/63, 25/41 | 37/63 (p = 0,21), 30/63, 8/12 |

- O ranking se mantém (ensemble e LightGBM à frente; climatologia pior que o naive; `gamma_rw` melhor que o chain-ladder). O ganho relativo ao naive é menor em `srag`: a série é mais suave (soma de vírus), e o naive já acerta mais.
- *Timing* em `srag`: erros menores (curva mais larga e estável), mas também sem vitória significativa sobre o naive. A conclusão de §12 não depende do alvo.
- O fim só tem 12 pares em `srag` (temporadas completas com fim definido).

---

## 12. Previsão de início, pico e fim (`05_marcos.ipynb`)

Rodada em 20/10/2026 (corte 18/10/2026).

### 12.1 Trajetórias e distribuição dos marcos

**Origens:** semanais, da SE 1 à SE 35 das temporadas 2024, 2025 e 2026 (102 origens).

**Trajetória:** em cada origem $o$, a temporada inteira $(y_1, \dots, y_K)$, com $K = 52$ ou 53, é composta por três partes:
- semanas com $t < o - 26$: a versão na origem (completa);
- semanas $o - 26, \dots, o - 1$: a amostra $i$ do nowcast `gamma_rw` (§6.1), não mais um valor pontual;
- semanas restantes, a partir de $o$: a amostra $i$ do modelo até o fim da temporada; o NB parte da amostra $i$ do nowcast de $o - 1$.

**Marcos:** para cada amostra, início, pico e fim pela mesma função da verdade (`vsr_comum.marcos_amostras`, definição *a priori* de §10.1). Assim, a distribuição prevista e a verdade são calculadas exatamente da mesma forma; o autoteste confere a versão vetorizada contra a referência em pandas em 300 trajetórias com empates.

**Modelos:**
- `naive_sazonal`;
- `climatologia`;
- `nb_local` (simulação recursiva);
- `arima_fourier` (região e Brasil);
- `forma_curva` (§12.5);
- `ensemble`: **mistura** das amostras com pesos iguais (para eventos, mistura de trajetórias, não média de quantis), incluindo `forma_curva`;
- `ensemble_pond` (§12.5): mistura com pesos ∝ 1/RPS das temporadas anteriores.

LightGBM (direto, $h \le 5$) e ETS (sem sazonalidade) não geram uma temporada.

**Verdade:** `eda_marcos_temporada.parquet` (série `vsr`). Em 2026, temporada em curso, o início e o pico são provisórios (semanas incompletas pela mediana do nowcast, `nowcast_aplicado`) e o fim não é avaliado.

### 12.2 Métricas

Sobre a distribuição $p_w$ em semanas $w = 1, \dots, 53$ (massa sem semana, de trajetórias nulas, é penalizada):

- **RPS** (CRPS discreto): $\sum_w (F_w - \mathbb 1[w \ge y])^2$, em semanas;
- **erro absoluto da mediana**;
- $P(|w - y| \le 1)$ (acerto em ±1 semana, como nos desafios do CDC);
- **cobertura** do intervalo de 80%.

**Antecedência** = semana real − semana da origem (> 0: evento futuro). O recorte principal é BR, regiões e UFs do tier A; nos tiers B e C, os marcos são avaliados na região.

**Teste de sinal pareado:** por par unidade-temporada, RPS médio nas origens com antecedência de 1 a 8 semanas; teste binomial bilateral. Há 63 pares para início e pico (21 unidades × 3 temporadas) e 41 para fim.

### 12.3 Resultados (antecedência 1–8 semanas)

Rodada de 20/10/2026 com o nowcast nas trajetórias, `forma_curva` e `ensemble_pond`.

| Marco | Modelo | BR + regiões: EA (sem) | P(±1) | cob. 80% | UF A: EA (sem) | P(±1) | cob. 80% |
|---|---|---|---|---|---|---|---|
| início | ensemble | 1,72 | 0,40 | 0,72 | 2,07 | 0,36 | 0,67 |
| início | naive_sazonal | 1,77 | 0,52 | 0,61 | 2,22 | 0,41 | 0,52 |
| início | arima_fourier | 1,60 | 0,45 | 0,70 | — | — | — |
| início | forma_curva | 2,25 | 0,32 | 0,42 | 2,19 | 0,34 | 0,53 |
| início | climatologia | 2,02 | 0,34 | 0,55 | 2,35 | 0,30 | 0,49 |
| pico | ensemble | 1,91 | 0,30 | 0,85 | 2,74 | 0,26 | 0,78 |
| pico | naive_sazonal | 1,90 | 0,34 | 0,94 | 2,77 | 0,30 | 0,78 |
| pico | arima_fourier | 2,06 | 0,33 | 0,79 | — | — | — |
| pico | forma_curva | 2,99 | 0,27 | 0,48 | 2,98 | 0,26 | 0,51 |
| pico | climatologia | 3,35 | 0,20 | 0,91 | 3,59 | 0,20 | 0,75 |
| fim | ensemble | 5,12 | 0,20 | 0,62 | 3,78 | 0,24 | 0,70 |
| fim | naive_sazonal | 4,00 | 0,27 | 0,42 | 4,47 | 0,20 | 0,30 |

**Teste de sinal (vitórias / pares):**
- *Contra a climatologia:* ensemble vence em início 45/63 (p = 0,001), pico 56/63 e fim 34/41 (p < 0,001).
- *Contra o naive sazonal:* **nenhum modelo é melhor** (ensemble: início 32/63, pico 25/63, p = 0,13; fim 25/41, p = 0,21; `ensemble_pond`: idem). O ensemble tem RPS médio menor que o naive nos três marcos (início 1,50 vs 1,81; pico 1,86 vs 1,89; fim 3,17 vs 3,51), mas sem consistência entre pares. Na rodada anterior o naive vencia no pico (ensemble 23/63, p = 0,04); com o nowcast nas trajetórias e o `forma_curva` na mistura, a diferença deixou de ser significativa.
- `forma_curva` perde para o naive no pico (21/63, p = 0,01).

**Leitura:**
1. Erro típico com 1–8 semanas de antecedência: ~2 semanas no início e no pico em BR e regiões, 2–3 nas UFs do tier A; o fim erra 4–5 semanas.
2. A habilidade nas contagens semanais (§11.5) **não se traduz em habilidade de *timing*** acima do naive sazonal.
3. Calibração: o início segue estreito (cobertura de 80% de 0,52–0,72); o pico é adequado no ensemble (0,78–0,85).

### 12.4 Implicações

- Para H4 e `06_decisao`, o baseline de *timing* é o naive sazonal.
- O *timing* da temporada corrente é dominado pelo formato da anterior; ganhos exigem informação externa (início em regiões vizinhas, outros vírus, sinais de busca) ou mais temporadas comparáveis.

### 12.5 Forma da curva e mistura ponderada (item 4)

**`forma_curva`** (`vm.forma_curva`): $\mu_w = A\, c\big(w_p + (w - w_p - \tau)/s\big)$.
- $c$ = média das curvas normalizadas (total 1) das temporadas anteriores inteiras na janela de treino, suavizada por média móvel de 3 semanas; $w_p$ = pico do molde. UFs com < 300 casos nas temporadas anteriores usam o molde da região (amplitude própria).
- Grade $\tau \in \{-10, \dots, 10\}$ semanas, $s \in \{0{,}8; 0{,}9; 1; 1{,}12; 1{,}25\}$.
- $A \sim \text{Gama}(a_0, b_0)$ com média e CV (≥ 0,3) dos totais das temporadas anteriores ⇒ verossimilhança marginal Poisson-Gama fechada para cada $(\tau, s)$ nas semanas observadas, temperada pela dispersão de Pearson $\varphi$ do melhor ajuste (quase-verossimilhança).
- *Prior* $\tau \sim \mathcal N(0, \sigma_\tau^2)$, $\sigma_\tau$ = dp dos picos anteriores (≥ 2); $s$ uniforme.
- Trajetórias: $(\tau, s)$ da posteriori na grade, $A \mid \tau, s$ da Gama posteriori e contagens NB1 com variância $\varphi\mu$.
- Autoteste: temporada sintética deslocada em $\tau = 3$ tem o pico recuperado (±1 semana) depois do pico.

**Resultado:** pior que o naive sazonal (pico 21/63, p = 0,01; início 24/63, p = 0,08) e subdisperso (cobertura de 80% de 0,42–0,53). Na subida da curva, deslocamento e amplitude se confundem; com 2–3 temporadas de molde, a *prior* em $\tau$ é estreita demais para temporadas como 2025 (tardia). Não ganha habilidade de *timing*.

**`ensemble_pond`** (`vm.ensemble_ponderado`): mistura das distribuições de `MODELOS_MARCOS` com pesos ∝ 1/RPS médio do modelo nas temporadas anteriores (antecedência 1–8, todas as unidades, por marco); 2024 sem histórico ⇒ pesos iguais. Resultado igual ao `ensemble` (início 32/63, pico 25/63, fim 25/41 contra o naive): os pesos aprendidos em 1–2 temporadas pouco diferem dos iguais.

### 12.6 Sensibilidade à definição da verdade (item 6)

As distribuições previstas (definição *a priori*) pontuadas contra as verdades MEM e de taxa (§10.3), sem nova previsão:
- EA da mediana cresce (início: 2,0 → 2,3 semanas no ensemble; fim: 4,2 → 5,3–5,4), como esperado pela diferença entre as definições.
- Contra o naive sazonal, as conclusões se mantêm: nenhum modelo é consistentemente melhor (ensemble no início: 38/60 com MEM, p = 0,05; 34/63 com taxa, p = 0,62).
- Limitação: os modelos não foram reajustados para prever o início pela definição alternativa; a comparação mede a robustez das conclusões, não o desempenho ótimo sob outra definição.

---

## 13. Limitações conhecidas

- **Nowcast:** amostras independentes entre semanas e entre unidades (sem coerência hierárquica); $F$ igual para todas as UFs de uma região; reconstrução otimista (abaixo) favorece todos os métodos igualmente.
- **Positividade:** a fração verdadeira está entre os dois limites. Falta confirmar no dicionário do SIVEP se `PCR_RESUL = 2` inclui testes só para SARS-CoV-2.
- **Reconstrução otimista:** `DT_DIGITA` não registra edições do registro, então a reconstrução em tempo real é otimista. Snapshots semanais reais ainda não estão sendo acumulados.
- **2019:** sem `DT_DIGITA` em 34% dos registros, e a averiguação é baixa.
- **Imunização:** só identificável por data; não há campo de nirsevimabe na ficha aberta.
- **Hospitalização:** a regra `HOSPITAL = 1` exclui ~2% dos casos com campo vazio (sensibilidade disponível).
- **UF de residência:** a recuperação é feita pelo município; em 0,03% dos casos é imputada pela UF de notificação.

---

## 14. Decisão: janela de imunização (`06_decisao.ipynb`)

Rodado em 20/10/2026. Unidades: Brasil, regiões e UFs do tier A (21). H4 reformulada (§10.2): uma janela regional cobre a mesma fração dos casos com menos semanas de campanha?

### 14.1 Desenho

- **Janela climatológica, validação deixando uma temporada de fora (LOSO, 2023–2025):** curva de treino = média das curvas normalizadas (total 1) das outras temporadas de 2023–2025. Para cada duração $L$, a janela de $L$ semanas com maior cobertura no treino é aplicada à temporada retirada (`decisao_curva`). 2026 usa as três temporadas como treino (cobertura provisória: temporada até a SE 41).
  - `regional_90`: menor $L$ com cobertura de treino ≥ 90%;
  - `regional_mesma_dur`: $L$ = duração de fev–ago (comparação a custo igual).
- **Fev–ago:** semanas com a quarta-feira de fevereiro a agosto (30–31 semanas).
- **Gatilho adaptativo:** na origem com semana $s$ (SE 1–35, `previsoes_marcos`), dispara se $P(\text{início} \le s + 3) > p$; a campanha dura o $L$ de `regional_90` (LOSO) a partir da semana do disparo. Métricas: cobertura, antecedência (início real − disparo) e falso alarme (sem disparo ou antecedência > 8 semanas). Modelos: `naive_sazonal` (baseline), `ensemble`, `ensemble_pond`, `forma_curva`; $p \in \{0{,}3, \dots, 0{,}9\}$.

### 14.2 Resultados (2023–2025 para janelas; 2024–2025 para o gatilho)

| Estratégia | BR + regiões: cobertura | duração (sem) | cob./semana | UF A: cobertura | duração | cob./semana |
|---|---|---|---|---|---|---|
| fev–ago | 0,92 | 30,3 | 0,030 | 0,93 | 30,3 | 0,031 |
| regional, mesma duração | 0,92 | 30,3 | 0,030 | 0,93 | 30,3 | 0,031 |
| regional 90% (LOSO) | 0,88 | 25,9 | 0,035 | 0,84 | 23,0 | 0,038 |
| gatilho, ensemble, $p = 0{,}7$ | 0,91 | 27,0 | 0,034 | 0,89 | 23,7 | 0,038 |
| gatilho, naive sazonal, $p = 0{,}7$ | 0,91 | 27,0 | 0,034 | 0,87 | 23,7 | 0,037 |

- **Janela fixa vs regional a custo igual:** empate (0,92–0,93). Com 30 semanas, fev–ago já cobre quase toda a temporada em todas as regiões; deslocar a janela não ganha.
- **Janela regional mais curta:** a de 90% (LOSO) perde 4–9 pontos de cobertura por 4–7 semanas a menos; a cobertura na temporada retirada fica abaixo do nominal (0,84–0,88 vs 0,90 no treino): a variação entre temporadas (2025 tardia no Nordeste: 0,73) não é capturada por 2 temporadas de treino.
- **Gatilho adaptativo:** recupera a cobertura da janela curta (0,91 em BR + regiões, 0,89 nas UFs A) com 3–7 semanas a menos que fev–ago, ou seja **+12% a +22% de cobertura por semana de campanha**. Antecedência média de 3–5 semanas antes do início; nenhum falso alarme nos agregados, 3–13% nas UFs A com $p \le 0{,}7$.
- **Modelo do gatilho:** ensemble e naive sazonal empatam nos agregados (0,91); nas UFs A o ensemble cobre +0,02 com o mesmo $p$. Consistente com §12: a vantagem de *timing* dos modelos sobre o naive é pequena.
- **Leitura para H4:** o argumento é de custo, não de cobertura. A janela fixa de 30 semanas cobre ~92%; uma campanha disparada pela previsão do início cobre ~90% com ~10–20% menos semanas. Com 2 temporadas de validação, a evidência é fraca (sem IC); precisa ser revista com 2026 completa.
