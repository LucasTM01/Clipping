# Capital Goods News Dataset — Claude Code Workspace

## Contexto do projeto

Pipeline para construir um dataset de notícias labelado do clipping diário de Capital Goods
do Banco Safra, originado de conversas do WhatsApp exportadas.

O objetivo final é um JSONL limpo com campos `title`, `body`, `ticker`, `relevant_yes_no`, `date`
que sirva de base para um classificador de relevância de notícias por ticker.

---

## Estado atual do dataset

- Arquivo base: `clipping_dataset.jsonl` (~4.145 registros)
- Fonte: exportação do WhatsApp da conversa Lucas Melotti ↔ Luiza Mussi
- Formato de cada registro:
  ```json
  {
    "date": "15/01/2025",
    "clipping_date_msg": "1/15/25 08:20",
    "sender": "Luiza Mussi",
    "section": "Aviation",
    "title": "Azul will reinstate route connecting Porto Alegre and Recife with the Embraer E2.",
    "url": "https://...",
    "ticker": "EMBR3",
    "all_tickers": ["EMBR3"],
    "relevant_yes_no": null,
    "body": null
  }
  ```
- Problema: ~338 duplicatas (mesmo clipping enviado por Luiza e Lucas no mesmo dia)
- `relevant_yes_no` ainda null em todos os registros
- `body` ainda null — artigo não foi fetchado

---

## Cobertura de tickers

Sempre usar este mapeamento canônico. Detecção por keyword no título/body:

| Keyword(s)                        | Ticker |
|-----------------------------------|--------|
| WEG, WEGE3                        | WEGE3  |
| MARCOPOLO, POMO4                  | POMO4  |
| EMBRAER, EMBR3, E2, E175, E190    | EMBR3  |
| RANDON, RAPT4                     | RAPT4  |
| FRAS-LE, FRASLE, FRAS3            | FRAS3  |
| IOCHPE, MYPK3                     | MYPK3  |
| INTELBRAS, INTB3                  | INTB3  |
| GPS, GGPS3                        | GGPS3  |
| ARMAC, ARML3                      | ARML3  |
| MILLS, MILS3                      | MILS3  |

---

## Stack técnico

- **Python 3.11+** com `uv` para gerenciamento de dependências
- **httpx** para HTTP async (não requests)
- **polars** para manipulação de dados (não pandas)
- **BeautifulSoup4** para parsing HTML
- **pydantic-settings** para configuração via `.env`
- **SQLAlchemy async** se precisar de persistência relacional
- Rodar em **WSL2** (Ubuntu) no Windows

Instalar dependências sempre com:
```bash
uv add httpx polars beautifulsoup4 pydantic-settings
```

---

## Sprints planejados

### Sprint 1 — Limpeza e labeling (PRÓXIMO)

**1a. Deduplicação**
- Deduplicar por `(title, url)`, manter o registro com `sender == "Lucas Melotti"` como prioritário
- Salvar em `clipping_dedup.jsonl`

**1b. Labeling automático inicial**
- `relevant_yes_no = true` → registros onde `sender == "Lucas Melotti"` (você escreveu/editou o clipping)
- `relevant_yes_no = false` → amostra de notícias dos mesmos portais e período que NÃO entraram no clipping (negativo proxy)
- Manter campo `label_source` ("auto_positive", "auto_negative", "manual") para rastreabilidade

**1c. Script de review interativo**
- CLI simples: mostra `title` + `section` + `date`, usuário digita `y/n/s` (yes/no/skip)
- Salva incrementalmente para não perder progresso

### Sprint 2 — Enriquecimento

**2a. Fetch de artigos**
- Para cada registro com URL, fetchar o corpo do artigo
- Respeitar rate limit: 1 req/s por domínio, timeout 10s
- Tratar paywall (Valor Globo, Estadão): guardar o que vier, não forçar
- Salvar `body` no registro

**2b. Melhoria de ticker detection**
- Rodar LLM (claude-haiku-4-5) sobre `title + body[:500]` para extrair tickers
- Só para registros onde `ticker == null`
- Prompt: "You are a Brazilian equity research assistant. Coverage: WEGE3, POMO4, EMBR3, RAPT4, FRAS3, MYPK3, INTB3, GGPS3, ARML3, MILS3. Given the news title and excerpt below, return a JSON array with all tickers mentioned or clearly implied. Return [] if none. Only return the JSON array, no explanation."

### Sprint 3 — Modelo

- Fine-tune ou few-shot classifier com dataset final
- Input: `title + body[:1000]`
- Output: `{ticker: str, relevant: bool, confidence: float}`
- Avaliar com split temporal (treino até mês X, teste mês X+1)

---

## Convenções de código

- Funções async por padrão para I/O
- Salvar checkpoints a cada 100 registros no fetch
- Logs com `print(f"[{datetime.now():%H:%M:%S}] ...")` simples, sem biblioteca de logging
- JSONL como formato padrão (não CSV, não parquet por enquanto)
- Não usar `pandas` — usar `polars` ou dicts nativos

---

## Arquivos do projeto

```
news-dataset/
├── CLAUDE.md                  ← este arquivo
├── data/
│   ├── clipping_dataset.jsonl     ← raw extraído do WhatsApp
│   ├── clipping_dedup.jsonl       ← após deduplicação (Sprint 1a)
│   ├── clipping_labeled.jsonl     ← após labeling (Sprint 1b/1c)
│   └── clipping_enriched.jsonl    ← após fetch de artigos (Sprint 2a)
├── parse_whatsapp.py              ← parser do .txt exportado
├── deduplicate.py                 ← Sprint 1a
├── label_auto.py                  ← Sprint 1b
├── review_cli.py                  ← Sprint 1c
├── fetch_articles.py              ← Sprint 2a
└── detect_tickers_llm.py          ← Sprint 2b
```

---

## Como retomar o trabalho

Ao iniciar uma sessão nova no Claude Code, dizer:

> "Estou no projeto news-dataset. Leia o CLAUDE.md e me diga o estado atual e qual é o próximo passo do Sprint X."

O Claude Code deve:
1. Ler este arquivo
2. Checar quais arquivos em `data/` já existem
3. Propor continuar do ponto parado

---

## Notas importantes

- O chat do WhatsApp tem duplicatas porque Luiza e Lucas enviavam o mesmo clipping; isso é esperado
- Notícias sem ticker não são irrelevantes — são contexto macro útil para features futuras
- Urls do domínio `apiext-bi.safra.com.br` são PDFs internos do Safra — pular no fetch, não são públicas
- URLs com `safelinks.protection.outlook.com` precisam ser decodificadas antes do fetch (extrair o parâmetro `url=`)
