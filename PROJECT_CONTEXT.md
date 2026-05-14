# Clipping — Contexto Completo do Projeto

> Documento gerado para uso em sessões de brainstorming. Auto-contido — não requer acesso ao repositório.

---

## 1. O que é o Clipping

O Clipping é um pipeline de inteligência de mercado automatizado, construído para uso pessoal de um
analista de equity research do banco Safra. A cobertura é focada em **Capital Goods & Industrials
Brasil** — um setor que inclui aeroespacial, automotivo, energia industrial e equipment rental.

**O problema que resolve:** monitorar dezenas de fontes de notícias diariamente, filtrar ruído,
identificar artigos relevantes para as empresas cobertas, e entregar um briefing estruturado já com
análise de sentimento e resumo — tudo antes das 07:30.

**O que entrega a cada execução (dias úteis, 07:00):**
- Um arquivo Word (`.docx`) com artigos agrupados por setor, sentimento e resumo
- Email 1: o mesmo briefing em HTML formatado, enviado para o analista no banco e no Gmail pessoal
- Email 2: relatório de operações (métricas de performance do pipeline) para diagnóstico
- Log de auditoria JSONL com todos os artigos avaliados (incluindo os descartados, com score)

**Escala estimada:** ~200–500 artigos coletados por dia, ~30–100 aprovados para análise LLM,
~10–50 no briefing final.

---

## 2. Universo de Cobertura (10 empresas)

| Ticker | Empresa | Setor no Briefing |
|--------|---------|-------------------|
| EMBR3 | Embraer | Aeroespacial |
| POMO4 | Marcopolo | Automotivo |
| RAPT4 | Randoncorp | Automotivo |
| FRAS3 | Frasle Mobility | Automotivo |
| MYPK3 | Iochpe-Maxion | Automotivo |
| WEGE3 | WEG | Energia e Tecnologia |
| INTB3 | Intelbras | Energia e Tecnologia |
| GGPS3 | GPS Participações | Outros |
| ARML3 | Armac | Outros |
| MILS3 | Mills | Outros |

Artigos sem ticker identificado vão para a seção **Macro / Setorial** (política industrial,
câmbio, commodities com impacto setorial).

---

## 3. Fontes de Dados (23 total)

### RSS Feeds (9)

| Fonte | Veículo | Idioma | Lookback |
|-------|---------|--------|----------|
| Pipeline Valor | Valor Econômico (curated deals flow) | PT | padrão (24h) |
| Valor Econômico | Valor Econômico (geral) | PT | padrão |
| Estadão Política | O Estado de S.Paulo | PT | padrão |
| Estadão Internacional | O Estado de S.Paulo | PT | padrão |
| Estadão Economia | O Estado de S.Paulo | PT | padrão |
| Financial Times Brazil | FT.com | EN | 336h (14 dias — baixa frequência, curado) |
| CNN Brasil | CNN Brasil | PT | padrão |
| Folha Mercados | Folha de S.Paulo | PT | padrão |
| Folha Mundo | Folha de S.Paulo | PT | padrão |

### Scrapers Playwright (14)

| Fonte | Site | Categoria |
|-------|------|-----------|
| Aeroin | aeroin.net | Aviação / Aeroespacial BR |
| Airway | airway.com.br | Aviação |
| Autodata | autodata.com.br | Dados setor automotivo |
| Automotive Business | automotivebusiness.com.br | Notícias automotivo |
| Brazil Journal | braziljournal.com | Business (EN) |
| Canal Solar | canalsolar.com.br | Energia solar |
| InfoMoney | infomoney.com.br | Financeiro geral |
| O Globo | oglobo.globo.com | Notícias gerais |
| Portal Solar | portalsolar.com.br | Energia solar / renovável |
| Reuters Aerospace & Defense | reuters.com | Stream por indústria |
| Reuters Autos & Transportation | reuters.com | Stream por indústria |
| Reuters Energy | reuters.com | Stream por indústria |
| Bloomberg Transportation | bloomberg.com | Digest curado (14 dias) |
| Bloomberg Energy | bloomberg.com | Digest curado (14 dias) |

Os scrapers usam Playwright (Chromium headless) com seletores CSS específicos por site.
Há um utilitário `playwright_utils.py` com extração de datas em cascata (structured data →
meta tags → byline → URL → fallback para now).

---

## 4. Arquitetura do Pipeline

O fluxo é **estritamente unidirecional** — nenhuma camada lê de volta para uma anterior.

```
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 1: Coleta                                               │
│  fetcher/rss_fetcher.py + scrapers/registry.py                 │
│                                                                 │
│  feedparser → Article                                          │
│  Playwright → Article                                          │
│  trafilatura → Article.full_text (ThreadPool, 10 workers)      │
│                                                                 │
│  Output: list[Article]  (~200–500 por dia)                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  CAMADA 2: Triagem (Screener v2)                                │
│  screener/screener_v2.py + embedding_store.py + relevance_scorer│
│                                                                 │
│  Texto: title + full_text[:4000]                               │
│  → SentenceTransformer BAAI/bge-m3 (GPU, 1024-dim, normalized) │
│  → Qdrant kNN (k=5, filter label="positive")                   │
│  → score = mean cosine similarity aos top-5 vizinhos           │
│  → tier: HIGH / MEDIUM / LOW_REVIEW / DISCARD                  │
│  → ticker: weighted vote dos payloads dos vizinhos             │
│                                                                 │
│  Output: list[ScoredArticle]  (~50–150 não-discard)            │
│  Audit log: output/screening_audit_YYYYMMDD.jsonl (todos)      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  CAMADA 3: Análise LLM                                         │
│  analyzer/tiered_dispatcher.py + analyzer/api_analyzer.py      │
│                                                                 │
│  HIGH + MEDIUM → LLM (LOW_REVIEW é logado, não analisado)      │
│  Backend configurável: Ollama (Qwen3.5:9b) ou OpenAI (GPT-4o)  │
│  Extrai: sentiment, action_required, summary                   │
│  Fallback JSON parsing se LLM retorna malformed                │
│                                                                 │
│  Output: list[AnalysisResult]  (~10–50 analisados)             │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  CAMADA 4: Documento + Emails                                  │
│  generator/doc_generator.py + notifier/email_sender.py         │
│                                                                 │
│  Word: artigos agrupados por setor (SECTOR_GROUPS)             │
│  Email 1 → HTML briefing → briefing_to[]                       │
│  Email 2 → HTML ops report → ops_to[]                          │
│                                                                 │
│  Output: output/briefing_YYYYMMDD_HHMM.docx                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  PÓS-RUN: Feedback Loop                                        │
│  data/export_feedback_candidates.py                            │
│                                                                 │
│  Near-misses (discard com score entre threshold±offsets)       │
│  + artigos aprovados → feedback/candidates.jsonl               │
│                                                                 │
│  Revisão manual: streamlit run review_feedback.py              │
│  Labels salvos → feedback/labeled/reviewed.jsonl               │
│  Retreino: python -m data.ingest_history                       │
└─────────────────────────────────────────────────────────────────┘
```

### Cadeia de Dataclasses

```
Article
  ├── source: str
  ├── title: str
  ├── url: str
  ├── published: datetime
  └── full_text: str | None

ScoredArticle
  ├── article: Article
  ├── relevance_score: float  (0.0 – 1.0)
  ├── tier: str               (high / medium / low_review / discard)
  ├── predicted_tickers: list[str]
  └── rationale: str

AnalysisResult
  ├── (campos de ScoredArticle achatados)
  ├── sentiment: str          (Positivo / Negativo / Neutro)
  ├── action_required: bool
  ├── summary: str
  └── source: str             (nome da fonte)
```

---

## 5. Screener v2 — A Parte Mais Sofisticada

O screener usa **novelty detection one-class** via embeddings, não classificação supervisionada.
Isso significa que o modelo não aprende "isso é irrelevante" — ele aprende "isso se parece com
o que eu já vi de relevante".

### Como funciona

1. **Dataset de positivos**: ~4.700 artigos históricos do briefing (artigos que o analista
   considerou relevantes o suficiente para incluir) ficam indexados no Qdrant com label `"positive"`.

2. **Embedding**: cada novo artigo é convertido em vetor 1024-dim via BAAI/bge-m3
   (modelo multilingual, roda na GPU, normalizado para cosine similarity).

3. **kNN**: busca os 5 vizinhos mais próximos no Qdrant entre os positivos.

4. **Score**: média das similaridades coseno com os 5 vizinhos.
   - Score próximo de 1.0 → artigo muito parecido com positivos históricos
   - Score próximo de 0.0 → artigo sem similaridade com nada relevante

5. **Threshold calibration** (LOO-CV):
   - Remove um positivo do Qdrant, embede ele, calcula o score com os demais
   - Repete para todos os positivos
   - Distribui os scores e pega o **P5** (5º percentil) como threshold
   - Isso garante que 95% dos positivos históricos passariam pelo filtro
   - Threshold atual: ~0.518 (em `config/calibration.json`)

6. **Tiers**:
   ```
   score ≥ 0.85  →  HIGH       (vai para LLM + briefing)
   score ≥ 0.55  →  MEDIUM     (vai para LLM + briefing)
   score ≥ thr   →  LOW_REVIEW (logado no audit, não enviado ao LLM)
   score < thr   →  DISCARD    (descartado, aparece no near-miss review)
   ```

7. **Ticker classification**: vote ponderado pelos scores dos vizinhos.
   Cada vizinho vota pelos seus tickers com peso = sua similarity score.
   Fallback: busca por keywords dos tickers no título.

### Near-miss review range
Artigos descartados com score em `[threshold - 0.05, threshold + 0.35]` são exportados
para revisão manual. Isso permite ao analista corrigir falsos negativos e usar os labels
para retreinar o sistema.

---

## 6. Stack Tecnológico

| Componente | Tecnologia | Papel |
|-----------|-----------|-------|
| Linguagem | Python 3.13 | Todo o pipeline |
| RSS Parsing | feedparser | Leitura de feeds RSS |
| Web Scraping | Playwright (Chromium) | Sites sem RSS |
| Full-text | trafilatura | Extração de texto limpo de URLs |
| Embeddings | BAAI/bge-m3 via sentence-transformers | Vetorização de artigos (GPU) |
| Vector DB | Qdrant (Docker) | Indexação + busca kNN dos positivos |
| LLM (local) | Ollama + Qwen3.5:9b | Análise de sentimento + resumo |
| LLM (cloud) | OpenAI GPT-4o | Alternativa configurável via .env |
| Doc geração | python-docx | Briefing Word (.docx) |
| Email | smtplib (stdlib) + Gmail SMTP | Envio automático pós-run |
| UI de revisão | Streamlit | App de labeling + dashboard |
| Config | Pydantic BaseSettings + .env | Todos os parâmetros |
| Scheduler | schedule (lib) | Execução às 07:00 em dias úteis |
| Logging | Python logging | DEBUG → arquivo + INFO → console |
| Dados | JSONL (newline-delimited JSON) | Audit logs, dataset, feedback |

**Infraestrutura:** Tudo roda localmente (Windows). Qdrant em Docker. GPU via CUDA para BGE-M3.
Não há serviços cloud obrigatórios — OpenAI é opcional.

---

## 7. Outputs Gerados

| Output | Caminho | Formato | Retenção |
|--------|---------|---------|---------|
| Briefing | `output/briefing_YYYYMMDD_HHMM.docx` | Word | Manual |
| Audit log | `output/screening_audit_YYYYMMDD.jsonl` | JSONL | Manual |
| Pipeline log | `output/pipeline_YYYYMMDD.log` | Texto | Manual |
| Email briefing | inbox analista | HTML | Gmail |
| Email ops | inbox analista | HTML | Gmail |
| Candidates | `feedback/candidates.jsonl` | JSONL | Gitignored |
| Labels | `feedback/labeled/reviewed.jsonl` | JSONL | Gitignored |
| Dataset | `news_dataset/clipping_enriched.jsonl` | JSONL | Gitignored |

---

## 8. Ferramentas de Suporte

### Source Health Checker (`tools/check_source.py`)

CLI para testar fontes individualmente sem rodar o pipeline completo.

```bash
python -m tools.check_source aeroin          # testa um scraper pelo nome
python -m tools.check_source "Valor"         # fuzzy match por nome
python -m tools.check_source --rss           # todos os RSS feeds
python -m tools.check_source --scrapers      # todos os scrapers (Playwright — lento)
python -m tools.check_source --all           # todos os 23
python -m tools.check_source aeroin --lookback 48  # janela maior
python -m tools.check_source aeroin -v       # verbose (URLs + tracebacks)
```

Retorna: ✅ OK / ⚠️ zero artigos / ❌ erro. Exit code 1 se qualquer fonte falha (usável em CI).

### Streamlit App (`review_feedback.py`)

```bash
streamlit run review_feedback.py
```

**Tab 1 — 📊 Base de Dados**: tamanho do dataset, contagem no Qdrant, stats de labels.

**Tab 2 — 🏷️ Revisão**: navega pelos candidates.jsonl, classifica como Relevante / Não Relevante /
Ignorar, salva labels. Filtros por tipo (near_miss / approved) e status (não revisado / revisado).

**Tab 3 — 📈 Dashboard**: dashboard operacional ao vivo com:
- Alertas de saúde (fontes que pararam de coletar ou caíram em volume)
- Gráfico de volume diário por fonte
- Tabela de stats por fonte (coletados, aprovados, scores)
- Heatmap fonte × dia
- Distribuição de tiers
- Frequência de tickers

---

## 9. Estado Atual e Limitações Conhecidas

### O que funciona bem
- Pipeline end-to-end estável (coleta → triagem → análise → Word → emails)
- Screener v2 com calibração LOO-CV — bom recall, configurable precision via threshold
- Emails automáticos (briefing HTML + ops report) com falha silenciosa
- Streamlit app com dashboard de saúde das fontes
- Health checker CLI para diagnóstico rápido de fontes
- Audit log completo para cada artigo (rastreabilidade total)

### Limitações e Gaps

**Feedback loop ainda manual:**
- O analista precisa abrir o Streamlit, revisar near-misses, salvar labels
- Depois rodar `python -m data.ingest_history` e `python -m data.calibrate_threshold` para retreinar
- Não há retreino automático nem ciclo de feedback contínuo

**Custo de LLM:**
- Ollama local (Qwen3.5:9b) é grátis mas requer GPU — sem GPU, é lento
- OpenAI GPT-4o resolve o problema de GPU mas adiciona custo por token
- Sem mecanismo de cache de análises: artigos muito similares são re-analisados

**Cobertura de fontes:**
- Algumas fontes relevantes não têm RSS nem scraper ainda (ex: Exame, InfoMoney agenda)
- Bloomberg e Reuters têm paywall — os scrapers funcionam apenas para conteúdo público ou seções específicas

**Ticker classification:**
- Baseado apenas em kNN dos vizinhos + keyword fallback
- Artigos sobre macro/setorial sem ticker claro vão para "Macro/Setorial" — às vezes relevante para múltiplos tickers sem ser classificado em nenhum
- Sem modelo de NER treinado para extração de entidades

**Escalabilidade:**
- Monousuário por design (pipeline roda localmente, não há API REST)
- Qdrant local — não compartilhado com outros analistas

**LOW_REVIEW tier:**
- Artigos nesse tier são logados no audit mas não enviados ao LLM nem ao briefing
- O analista pode não ver artigos importantes que ficaram nessa faixa — mitigado pelo Streamlit

---

## 10. Próximos Passos Possíveis (para brainstorming)

Esta lista é intencional e aberta — não é um roadmap comprometido.

### Qualidade do briefing
- **Deduplicação semântica**: artigos sobre o mesmo evento de fontes diferentes colapsar em um único item
- **Ranking intra-setor**: ordenar artigos dentro de cada seção por relevância (score) ao invés de data
- **Resumo executivo automático**: gerado pelo LLM a partir dos artigos do dia, não apenas contagem de sentimentos
- **Alertas de materialidade**: identificar automaticamente eventos que requerem nota imediata (guidance revision, acidente, M&A)

### Cobertura e fontes
- **Mais tickers**: expandir para Utilities, Infra, ou outros setores do banco
- **Fontes internacionais**: Aviation Week, Ward's Auto, S&P Global Mobility
- **RSS de reguladores**: ANAC, ANTT, MME para capturar regulações setoriais
- **CVM/SEC filings**: 8-K, 20-F, comunicados relevantes das próprias empresas cobertas

### Retreino e aprendizado
- **Feedback loop automático**: após labeling no Streamlit, rodar ingest + calibrate automaticamente
- **Active learning**: priorizar para revisão os artigos onde o modelo é menos confiante
- **Score drift detection**: alertar quando a distribuição de scores muda significativamente (indica mudança no fluxo de notícias ou degradação do modelo)
- **Backtesting**: comparar o que o screener aprovaria nos últimos N dias com o que foi efetivamente relevante

### Operação e monitoramento
- **Alertas de falha de fonte**: email imediato quando uma fonte para de coletar (hoje só visível no Streamlit)
- **Dashboard web**: versão web do ops dashboard (Grafana ou similar) sem precisar abrir Streamlit
- **Execução na nuvem**: mover pipeline para um servidor (EC2, VPS) com GPU para independência do laptop

### Briefing e UX
- **Web UI do briefing**: ao invés de Word, um dashboard web interativo onde o analista pode filtrar, buscar, marcar artigos
- **Histórico de tickers**: ver todos os artigos de EMBR3 nos últimos 30 dias em um click
- **Integração com Bloomberg Terminal**: exportar artigos curados para uma função personalizada do Bloomberg
- **Scoring histórico**: gráfico de sentimento por ticker ao longo do tempo para identificar tendências

### LLM e análise
- **Fine-tuning do modelo de análise**: treinar um modelo menor e específico para análise de equity research BR
- **Extração estruturada**: além de sentimento, extrair guidances, preços, volumes, variações de taxa
- **Comparação com consenso**: integrar com dados de consenso de analistas para identificar surpresas
- **Análise intraday**: versão mais leve que roda a cada 2h durante o pregão para alertas urgentes

---

## Referências Rápidas

```bash
# Rodar o pipeline
python main.py

# Rodar sem enriquecimento (mais rápido, só headlines)
python main.py --no-enrich

# Executar em modo agendado (07:00 dias úteis)
python main.py --schedule

# Testar fonte específica
python -m tools.check_source aeroin -v

# Abrir app de revisão
streamlit run review_feedback.py

# Retreinar após labeling
python -m data.ingest_history
python -m data.calibrate_threshold

# Rodar testes unitários
python -m pytest tests/ -v
```

**Arquivos-chave:**
- `pipeline.py` — orquestração geral
- `screener/screener_v2.py` — triagem por embeddings
- `config/calibration.json` — threshold (`threshold` field, único campo ativo)
- `config/settings.py` — todos os parâmetros (RSS feeds, tickers, email, LLM backend)
- `notifier/email_sender.py` — PipelineStats + templates HTML dos emails
- `review_feedback.py` — Streamlit app
