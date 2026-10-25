# Terceira rodada — recalibração conformal online

## Método

Partimos do `ensemble_prequential` já avaliado na segunda rodada. Para cada origem, nível e horizonte, os intervalos são corrigidos em escala `log1p` usando escores das últimas 52 origens cujos alvos já estavam maduros há pelo menos 26 semanas. Para cada intervalo central de nível $1-2\tau$, a expansão é o quantil split-conformal finito do escore $\max(q_\tau-y, y-q_{1-\tau},0)$. A mediana não é alterada. Esta é uma transformação online, sem usar dados futuros na origem.

## WIS relativo ao naive sazonal

```
               fase  nivel                         modelo  wis_rel     n
         teste_2026 BRASIL           ensemble_prequential    0.409   138
         teste_2026 BRASIL ensemble_prequential_conformal    0.467   138
         teste_2026 REGIAO           ensemble_prequential    0.358   690
         teste_2026 REGIAO ensemble_prequential_conformal    0.365   690
         teste_2026     UF           ensemble_prequential    0.472  3726
         teste_2026     UF ensemble_prequential_conformal    0.476  3726
validacao_2024_2025 BRASIL           ensemble_prequential    0.550   410
validacao_2024_2025 BRASIL ensemble_prequential_conformal    0.604   410
validacao_2024_2025 REGIAO           ensemble_prequential    0.533  2050
validacao_2024_2025 REGIAO ensemble_prequential_conformal    0.541  2050
validacao_2024_2025     UF           ensemble_prequential    0.600 11070
validacao_2024_2025     UF ensemble_prequential_conformal    0.600 11070
```

## Cobertura e WIS em log

```
                                                           cob50  cob90  wis_log
fase                nivel  modelo                                               
teste_2026          BRASIL ensemble_prequential            0.587  0.957    0.109
                           ensemble_prequential_conformal  0.688  0.957    0.124
                           naive_sazonal                   0.587  0.971    0.266
                    REGIAO ensemble_prequential            0.528  0.923    0.192
                           ensemble_prequential_conformal  0.591  0.938    0.195
                           naive_sazonal                   0.457  0.822    0.535
                    UF     ensemble_prequential            0.513  0.878    0.300
                           ensemble_prequential_conformal  0.560  0.922    0.302
                           naive_sazonal                   0.392  0.832    0.636
validacao_2024_2025 BRASIL ensemble_prequential            0.422  0.868    0.197
                           ensemble_prequential_conformal  0.620  0.920    0.216
                           naive_sazonal                   0.546  0.846    0.358
                    REGIAO ensemble_prequential            0.465  0.895    0.271
                           ensemble_prequential_conformal  0.521  0.914    0.275
                           naive_sazonal                   0.454  0.863    0.508
                    UF     ensemble_prequential            0.510  0.873    0.320
                           ensemble_prequential_conformal  0.536  0.893    0.320
                           naive_sazonal                   0.479  0.833    0.533
```

## Diferença pareada: conformal menos adaptativo original

`delta_wis_log < 0` favorece a recalibração; cobertura de 90% próxima de 0,90 é desejada.

```
               fase  nivel                      candidato           referencia  delta_wis_log  ic_inf  ic_sup     n
         teste_2026 BRASIL ensemble_prequential_conformal ensemble_prequential         0.0152  0.0108  0.0248   138
         teste_2026 REGIAO ensemble_prequential_conformal ensemble_prequential         0.0037  0.0011  0.0055   690
         teste_2026     UF ensemble_prequential_conformal ensemble_prequential         0.0021  0.0005  0.0031  3726
validacao_2024_2025 BRASIL ensemble_prequential_conformal ensemble_prequential         0.0194  0.0102  0.0285   410
validacao_2024_2025 REGIAO ensemble_prequential_conformal ensemble_prequential         0.0040  0.0021  0.0059  2050
validacao_2024_2025     UF ensemble_prequential_conformal ensemble_prequential         0.0002 -0.0003  0.0004 11070
```

## Comparação direta: conformal menos ensemble fixo

```
               fase  nivel                      candidato referencia  delta_wis_log  ic_inf  ic_sup     n
         teste_2026 BRASIL ensemble_prequential_conformal   ensemble        -0.0071 -0.0228  0.0128   138
         teste_2026 REGIAO ensemble_prequential_conformal   ensemble        -0.0458 -0.0561 -0.0393   690
         teste_2026     UF ensemble_prequential_conformal   ensemble        -0.0810 -0.0903 -0.0732  3726
validacao_2024_2025 BRASIL ensemble_prequential_conformal   ensemble         0.0192  0.0049  0.0327   410
validacao_2024_2025 REGIAO ensemble_prequential_conformal   ensemble        -0.0045 -0.0153  0.0024  2050
validacao_2024_2025     UF ensemble_prequential_conformal   ensemble        -0.0536 -0.0641 -0.0457 11070
```

## Decisão

Para regiões, a versão conformal eleva a cobertura de 0,895 para 0,914, mas seu WIS é 0,541 versus 0,550 do ensemble fixo e a diferença pareada ainda inclui zero. Logo, ela é um candidato calibrado, não uma substituição confirmada. Brasil permanece no ensemble fixo: a recalibração piora WIS na validação. Nas UFs a decisão continua sendo o `lgbm_global` da segunda rodada. Toda adoção operacional depende de reavaliação com snapshots reais do SIVEP-Gripe; 2026 é apenas confirmação fora de distribuição.
