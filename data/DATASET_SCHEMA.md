# Dataset Schema — clipping_enriched_dummy.jsonl

**Localização:** `news_dataset/clipping_enriched_dummy.jsonl`

## Estatísticas (inspecionadas em 2026-05-01)

| Métrica | Valor |
|---|---|
| Total de registros | 400 |
| `relevant_yes_no = true` | 400 (100%) |
| `relevant_yes_no = false` | 0 |
| Com `ticker` atribuído | 85 (21%) |
| Sem `ticker` (macro/setorial) | 315 (79%) |
| Com `body` | 336 (84%) |
| Sem `body` (só título) | 64 (16%) |

## Campos

| Campo             | Tipo        | Nullable | Notas                                              |
|-------------------|-------------|----------|----------------------------------------------------|
| `date`            | `str`       | Não      | Formato `DD/MM/YYYY`, ex: `"15/01/2025"`           |
| `clipping_date_msg` | `str`     | Não      | Formato `M/D/YY HH:MM`, ex: `"1/15/25 08:20"`     |
| `sender`          | `str`       | Não      | Nome do analista, ex: `"Lucas Melotti"`            |
| `section`         | `str`       | Não      | Seção do clipping, ex: `"General"`                 |
| `title`           | `str`       | Não      | Sempre presente; usado como fallback de embedding  |
| `url`             | `str`       | Não      | URL da notícia original                            |
| `ticker`          | `str\|null` | Sim (79%)| Ticker B3 (ex: `"FRAS3"`) ou `null` para macro    |
| `all_tickers`     | `list`      | Não      | Lista com `[ticker]` ou `[null]`                   |
| `relevant_yes_no` | `bool`      | Não      | `true` em todos os registros atuais                |
| `body`            | `str\|null` | Sim (16%)| Texto completo do artigo; `null` em 64 registros   |
| `title_original`  | `str\|null` | Sim      | Título em PT-BR quando artigo é em inglês          |
| `subtitle_original` | `str\|null` | Sim    | Subtítulo em PT-BR ou `null`                       |

## Regras de ingestão no Qdrant

- **Todos** os registros com `relevant_yes_no=True` entram no índice (400 total)
- `ticker=null` → `tickers: []` no payload — notícias macro/setoriais são incluídas
- Texto para embedding:
  - Se `body` vazio/null: usar só `title`
  - Caso contrário: `title + "\n\n" + body[:4000]`
- ID de cada vetor: SHA-256 de `url + title` (hex, 32 chars) → convertido para int64

## Modelos em uso

| Modelo              | Variante | VRAM estimada | Sprint |
|---------------------|----------|---------------|--------|
| `BAAI/bge-m3`       | full     | ~2 GB         | 1      |
| Qwen (análise)      | TBD      | TBD           | 3      |
| `bge-reranker-v2-m3`| full     | ~2 GB         | 4      |

> Atualizar coluna "VRAM estimada" após medição real no Sprint 3.
