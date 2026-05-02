# Feedback Loop — Dataset Expansion

## Como funciona

Após cada run do pipeline, o arquivo `feedback/candidates.jsonl` é atualizado
automaticamente com artigos na faixa de score "near-miss" (próximos ao threshold
de descarte). Esses são os candidatos mais valiosos para rotular, pois ficaram
na fronteira da decisão.

## Workflow de revisão

### 1. Ver candidatos
Abrir `feedback/candidates.jsonl`. Cada linha é um JSON com:
```json
{
  "date": "01/05/2026",
  "title": "Embraer anuncia contrato com Delta Air Lines",
  "url": "https://...",
  "source": "Valor Econômico",
  "relevance_score": 0.521,
  "predicted_tickers": ["EMBR3"],
  "relevant_yes_no": null,
  "_reviewed": false
}
```

### 2. Rotular cada artigo
Para cada artigo que você quer revisar:
- `relevant_yes_no: true` → artigo É relevante (o screener errou ao descartar)
- `relevant_yes_no: false` → artigo NÃO é relevante (confirma o descarte)
- Mudar `_reviewed: true` para marcar como revisado

Artigos com `_reviewed: false` são ignorados no ingest — só revise o que você tem certeza.

### 3. Mover para labeled/
Copiar o arquivo revisado para `feedback/labeled/`:
```
feedback/labeled/reviewed_YYYYMMDD.jsonl
```
Você pode manter o `candidates.jsonl` original intacto (o sistema não re-ingere o que já está no Qdrant).

### 4. Re-ingerir e recalibrar
```bash
python -m data.ingest_history        # re-ingere positivos + negativos
python -m data.calibrate_threshold   # recalibra threshold com os novos dados
```

## Frequência recomendada

- **Semanalmente**: revisar o `candidates.jsonl` acumulado da semana (~50-100 artigos)
- **Após 50+ negativos**: rodar ingest + calibrate para melhorar o screener

## Estrutura de arquivos

```
feedback/
  candidates.jsonl        # near-misses acumulados (gerado automaticamente)
  labeled/
    reviewed_20260501.jsonl   # arquivo revisado pelo analista
    reviewed_20260508.jsonl
    ...
```

## Impacto esperado

| Negativos no dataset | Efeito |
|---|---|
| 0 (atual) | Score threshold conservador, alguns near-misses entram por precaução |
| 50-100 | Threshold mais preciso, menos false positives |
| 200+ | Modelo distingue bem near-misses de relevantes |
