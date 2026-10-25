# Resultados locais - VSR no Brasil

Execução independente em 20/10/2026, com dados públicos SIVEP-Gripe baixados do OpenDataSUS.

## Dados e validação

- Arquivos anuais: 2019--2026; versões de 2025 e 2026 publicadas em 19/10/2026.
- Corte de disponibilidade: 18/10/2026; última semana completa: 11/10/2026.
- Alvo: hospitalizações por SRAG com VSR confirmado (PCR ou antígeno), em menores de 24 meses, por semana de início dos sintomas e UF de residência.
- O painel contém 407 semanas (30/12/2018--11/10/2026), com 33 unidades na hierarquia (Brasil, 5 regiões, 27 UFs).
- Todas as 17 validações internas passaram: coerência UF-região-Brasil, soma das faixas etárias, soma do triângulo de atraso, chaves únicas e calendário válido.

## Desenho de avaliação

- Origens rolantes semanais: 31/12/2023--09/08/2026 (137 origens).
- Horizontes: 1--4 semanas; a semana corrente é avaliada separadamente.
- Treino: janela de 156 semanas, a partir de 2022; 2020--2021 fora do treino.
- Nowcast: Gamma-Poisson com passeio aleatório e atraso máximo de 26 semanas.
- Métrica principal: WIS em `log(1 + casos)`, relativo ao naive sazonal. Menor que 1 é melhor.

## Configurações comparadas

1. naive sazonal;
2. climatologia;
3. ETS;
4. ARIMA com termos de Fourier;
5. binomial negativa local;
6. binomial negativa reconciliada por bottom-up e MinT, com e sem Schaake shuffle;
7. LightGBM global com defasagens, sazonalidade e UF categórica;
8. ensemble probabilístico.

## Resultado principal: WIS relativo ao naive sazonal (h = 1--4)

| Modelo | Brasil, validação | Regiões, validação | UFs, validação | Brasil, teste 2026 | Regiões, teste 2026 | UFs, teste 2026 |
|---|---:|---:|---:|---:|---:|---:|
| Ensemble | **0,550** | **0,550** | 0,701 | 0,493 | 0,450 | 0,603 |
| LightGBM global | 0,614 | 0,570 | **0,592** | 0,522 | **0,409** | **0,492** |
| NB local | 0,737 | 0,657 | 0,746 | 0,539 | 0,454 | 0,583 |
| NB bottom-up | 0,709 | 0,776 | 0,746 | **0,419** | 0,583 | 0,583 |
| NB MinT | 0,810 | 0,745 | 0,726 | 0,515 | 0,620 | 0,645 |
| Naive sazonal | 1,000 | 1,000 | 1,000 | 1,000 | 1,000 | 1,000 |
| Climatologia | 1,545 | 1,365 | 1,152 | 1,483 | 1,186 | 1,018 |

## Calibração dos intervalos de 90% (h = 1--4)

| Modelo | Brasil, validação | Regiões, validação | UFs, validação |
|---|---:|---:|---:|
| Ensemble | 0,966 | 0,939 | 0,855 |
| LightGBM global | 0,780 | 0,818 | 0,880 |
| NB local | 0,717 | 0,720 | 0,781 |
| NB MinT | 0,317 | 0,529 | 0,756 |

## Leitura

- A previsão de contagens semanais melhora claramente sobre o naive sazonal em Brasil, regiões e UFs.
- O ensemble é a melhor configuração na validação para Brasil e regiões e é a mais bem calibrada nos agregados.
- O LightGBM global é a melhor configuração nas UFs. O ganho vem de compartilhar informação entre estados, sobretudo os de baixa contagem.
- MinT não é a fonte principal do ganho: frente ao NB local, melhora só marginalmente nas UFs (0,726 contra 0,746) e piora no Brasil e regiões. Seus intervalos também ficam muito estreitos nos agregados.
- A conclusão é robusta no período de teste de 2026: LightGBM continua melhor em regiões/UFs, enquanto bottom-up é o melhor no Brasil.
- Esta rodada confirma a conclusão técnica do projeto: há ganho real para prever volume semanal, mas estes resultados não demonstram, por si só, ganho para prever a semana exata de início ou pico da temporada.

## Arquivos reproduzíveis

- `data/processed/metadata.json`: fontes, hashes, configuração e data de corte.
- `data/processed/qc_validacao.csv`: checagens internas aprovadas.
- `data/processed/previsoes_backtest.parquet`: distribuições preditivas.
- `data/processed/avaliacao_backtest.parquet`: WIS, erro e cobertura por origem, unidade e horizonte.

Limitação relevante: a reconstrução de informação em tempo real usa as datas de digitação e resultado presentes na versão final dos arquivos; ela não substitui snapshots semanais reais do SIVEP-Gripe.

## Segunda rodada exploratória

Modelos adicionais, avaliação temporal, intervalos bootstrap e recomendação operacional estão em `RESULTADOS_SEGUNDA_RODADA.md`. A rodada mantém o ensemble fixo para Brasil e o LightGBM global para UFs; o ensemble online regional é uma hipótese para recalibração, não uma substituição já aprovada.

## Terceira rodada: calibração dos intervalos

A recalibração conformal online está documentada em `RESULTADOS_TERCEIRA_RODADA_CONFORMAL.md`. Ela melhora a cobertura de 90% nas regiões (0,895 para 0,914), mas não estabelece superioridade de WIS contra o ensemble fixo; a referência operacional permanece inalterada.
