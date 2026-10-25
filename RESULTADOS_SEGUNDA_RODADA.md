# Segunda rodada de modelos — resultados locais

## Desenho confirmado antes da execução

Esta rodada não reotimiza modelos com os resultados de 2024--2026. Foram comparados:

1. `lgbm_sazonal`: LightGBM global direto por horizonte com lags de 13, 26 e 52 semanas e regularização mais forte;
2. `poisson_painel`: regressão Poisson global direta com efeitos fixos de UF, interações região × Fourier e incerteza NB estimada fora da amostra por UF;
3. `ensemble_prequential`: média de quantis de `ensemble`, `lgbm_global`, `lgbm_sazonal` e `poisson_painel`, com pesos inversos ao WIS das últimas 52 origens cujos alvos já estariam maduros (alvo até 26 semanas antes da origem).

Todos os candidatos usam a mesma série reconstruída na origem, as mesmas 137 origens, h=1--4, 400 amostras e a mesma reconciliação bottom-up/Schaake do LightGBM de referência. Validação = alvos 2024--2025; 2026 fica separado como teste fora de distribuição. WIS relativo menor que 1 favorece o modelo sobre o naive sazonal.

**Controle de versão:** arquivos experimentais sem o sufixo `_v2` ficam somente como trilha de auditoria; tinham um desalinhamento de horizonte (a semana da origem foi etiquetada como h=1) e não entram em nenhum resultado deste relatório. Use exclusivamente as saídas `_v2`.

## WIS relativo ao naive sazonal

```
modelo                      ensemble  ensemble_prequential  lgbm_global  lgbm_sazonal  naive_sazonal  poisson_painel
fase                nivel                                                                                           
teste_2026          BRASIL     0.493                 0.409        0.522         0.538            1.0           0.490
                    REGIAO     0.450                 0.358        0.409         0.405            1.0           0.460
                    UF         0.603                 0.472        0.492         0.516            1.0           0.575
validacao_2024_2025 BRASIL     0.550                 0.550        0.614         0.554            1.0           0.750
                    REGIAO     0.550                 0.533        0.570         0.586            1.0           0.671
                    UF         0.701                 0.600        0.592         0.640            1.0           0.705
```

## Diferença pareada contra o LightGBM global

`delta_wis_log < 0` favorece o candidato. Um IC de 95% que cruza zero não sustenta ganho estável.

```
               fase  nivel            candidato  referencia  delta_wis_log  ic_inf  ic_sup     n
         teste_2026 BRASIL         lgbm_sazonal lgbm_global         0.0044 -0.0366  0.0455   138
         teste_2026 REGIAO         lgbm_sazonal lgbm_global        -0.0022 -0.0271  0.0264   690
         teste_2026     UF         lgbm_sazonal lgbm_global         0.0152  0.0035  0.0319  3726
validacao_2024_2025 BRASIL         lgbm_sazonal lgbm_global        -0.0212 -0.0389 -0.0042   410
validacao_2024_2025 REGIAO         lgbm_sazonal lgbm_global         0.0079 -0.0038  0.0186  2050
validacao_2024_2025     UF         lgbm_sazonal lgbm_global         0.0255  0.0173  0.0326 11070
         teste_2026 BRASIL       poisson_painel lgbm_global        -0.0085 -0.0443  0.0330   138
         teste_2026 REGIAO       poisson_painel lgbm_global         0.0276  0.0093  0.0686   690
         teste_2026     UF       poisson_painel lgbm_global         0.0527  0.0310  0.0858  3726
validacao_2024_2025 BRASIL       poisson_painel lgbm_global         0.0489 -0.0019  0.1014   410
validacao_2024_2025 REGIAO       poisson_painel lgbm_global         0.0515  0.0184  0.0883  2050
validacao_2024_2025     UF       poisson_painel lgbm_global         0.0602  0.0462  0.0779 11070
         teste_2026 BRASIL ensemble_prequential    ensemble        -0.0223 -0.0385 -0.0044   138
         teste_2026 REGIAO ensemble_prequential    ensemble        -0.0495 -0.0586 -0.0427   690
         teste_2026     UF ensemble_prequential    ensemble        -0.0831 -0.0915 -0.0758  3726
validacao_2024_2025 BRASIL ensemble_prequential    ensemble        -0.0001 -0.0133  0.0138   410
validacao_2024_2025 REGIAO ensemble_prequential    ensemble        -0.0085 -0.0189 -0.0020  2050
validacao_2024_2025     UF ensemble_prequential    ensemble        -0.0538 -0.0640 -0.0458 11070
         teste_2026 BRASIL ensemble_prequential lgbm_global        -0.0300 -0.0565 -0.0022   138
         teste_2026 REGIAO ensemble_prequential lgbm_global        -0.0273 -0.0411 -0.0047   690
         teste_2026     UF ensemble_prequential lgbm_global        -0.0126 -0.0240  0.0049  3726
validacao_2024_2025 BRASIL ensemble_prequential lgbm_global        -0.0228 -0.0446 -0.0011   410
validacao_2024_2025 REGIAO ensemble_prequential lgbm_global        -0.0190 -0.0323 -0.0051  2050
validacao_2024_2025     UF ensemble_prequential lgbm_global         0.0043 -0.0054  0.0150 11070
```

## Calibração: cobertura empírica do intervalo de 90%

O alvo é aproximadamente 0,90. Cobertura excessiva também é custo: em geral indica intervalos largos demais.

```
modelo                      ensemble  ensemble_prequential  lgbm_global  lgbm_sazonal  naive_sazonal  poisson_painel
fase                nivel                                                                                           
teste_2026          BRASIL     1.000                 0.957        0.819         0.826          0.971           0.870
                    REGIAO     0.959                 0.923        0.810         0.806          0.822           0.846
                    UF         0.890                 0.878        0.854         0.865          0.832           0.883
validacao_2024_2025 BRASIL     0.966                 0.868        0.780         0.846          0.846           0.820
                    REGIAO     0.939                 0.895        0.818         0.828          0.863           0.815
                    UF         0.855                 0.873        0.880         0.879          0.833           0.868
```

## Auditoria do ensemble online

O número de observações maduras usado nos pesos varia por origem, nível e horizonte; resumo do máximo disponível:

```
count     12.000000
mean     269.500000
std      293.097409
min       23.000000
25%       25.750000
50%      122.500000
75%      627.750000
max      702.000000
```

## Interpretação

1. **Não substituir a referência das UFs.** `lgbm_global` continua melhor na validação (0,592); o ensemble online fica em 0,600 e seu IC de diferença versus LightGBM inclui zero. `lgbm_sazonal` e `poisson_painel` são piores nas UFs.
2. **Brasil: manter o ensemble fixo.** O ensemble online empata em WIS na validação (0,550), mas reduz a cobertura de 90% de 0,966 para 0,868. O ganho do `lgbm_sazonal` sobre o LightGBM não supera o ensemble e também piora a calibração.
3. **Regiões: o ensemble online é promissor, mas ainda provisório.** Melhora WIS na validação (0,533 vs 0,550 do ensemble fixo; diferença pareada negativa) e alcança cobertura de 0,895. Antes de adotá-lo, aplicar recalibração conformal em uma janela passada e reavaliar com snapshots reais.
4. **Rejeitar o Poisson de painel como substituto.** Ele perde para o LightGBM em regiões e UFs, com ICs de diferença inteiramente positivos na validação.

Use o WIS relativo e os ICs, não apenas o menor número pontual, para decidir se algum candidato substitui o modelo de referência. Esta é uma comparação interna ao mesmo corte de dados: ela mede ganho de especificação, mas não resolve a limitação de pseudo-tempo-real causada pela ausência de snapshots históricos.
