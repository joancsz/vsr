# Operação semanal — snapshots e decisão de previsão

## Objetivo

Preservar a versão do SIVEP-Gripe realmente disponível a cada semana. Não é necessário reexecutar os modelos semanalmente para começar: a série de snapshots é a condição para transformar o atual backtest pseudo-tempo-real em avaliação histórica real.

## Rotina semanal (quarta-feira, após a atualização habitual do OpenDataSUS)

No diretório do projeto, com o ambiente `vsr`:

```powershell
python capturar_snapshot_srag.py --anos 2025 2026
```

Antes de uma primeira execução ou após falha de rede, a verificação sem escrita é:

```powershell
python capturar_snapshot_srag.py --anos 2025 2026 --dry-run
```

O coletor grava arquivos em `data/raw/snapshots/SRAG/AAAA-MM-DD/<ano>/` e acrescenta uma linha ao manifesto `data/raw/snapshots/SRAG/manifesto_snapshots.jsonl`. O manifesto é append-only; não editar, renomear ou substituir arquivos capturados.

## Marco inicial

A primeira captura foi concluída em 20/10/2026 para 2025 e 2026. Ela preservou as publicações de 19/10/2026, respectivamente com hashes `dd7eeff22754…` e `66b9e61b8b7e…`. Os caminhos e hashes completos estão no manifesto; este marco é a origem da série de avaliação realmente prospectiva.

## Critérios de aceitação da coleta

1. O comando termina com código zero e uma linha por ano.
2. Cada novo arquivo é validado como Parquet e recebe SHA-256.
3. Uma republicação idêntica é registrada com `acao = referencia_hash_existente`, sem duplicar dezenas de megabytes.
4. Se algum ano solicitado não tiver URL, o comando falha antes de registrar uma captura parcial.
5. Uma segunda captura na mesma data falha por padrão. `--forcar` só serve para investigar uma republicação intradiária e cria outra linha, nunca sobrescreve a anterior.

## Quando reexecutar o pipeline

Não misturar snapshot com previsão operacional sem marcar a versão usada. Para cada rodada de previsão, registrar no relatório: data/hora, hash de cada snapshot, data final observável e versão do código.

Reexecutar `01` a `06` somente quando houver uma atualização de análise planejada (por exemplo, mensalmente ou após encerrar uma temporada). A cada snapshot basta verificar a coleta. Depois de acumular pelo menos uma temporada de snapshots, reconstruir o triângulo de atraso e repetir os backtests exclusivamente com os arquivos que existiam em cada origem.

## Regra atual de decisão

- Brasil: ensemble fixo.
- Regiões: ensemble fixo; versão conformal permanece análise de sensibilidade.
- UFs: `lgbm_global`.
- Não adotar gatilho de imunização como recomendação final antes de fechar 2026 e repetir a avaliação com snapshots reais.

## Alertas que exigem investigação

- falha de descoberta ou download;
- mudança súbita de esquema/colunas do Parquet;
- ausência de republicação por mais de duas semanas no ano corrente;
- mudança importante no hash com mesma data de publicação;
- cobertura de 90% abaixo de 0,85 ou acima de 0,98 em duas rodadas consecutivas de avaliação.
