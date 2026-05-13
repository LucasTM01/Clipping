# Guia Operacional — Pipeline de Clipping

Manual de operação do sistema de clipping automatizado para Equity Research —
Capital Goods & Industrials.

---

## O que o sistema faz

Todo dia útil, o pipeline:
1. Coleta notícias de feeds RSS (Valor Econômico, InfoMoney, Reuters, Bloomberg BR, etc.)
2. Filtra as relevantes para a cobertura usando inteligência artificial (embeddings + similaridade semântica)
3. Analisa cada notícia com um LLM local (Qwen3.5) e gera uma síntese em linguagem de sell-side
4. Gera um documento Word organizado por setor com as notícias do dia

**Output:** `output/briefing_YYYYMMDD_HHMM.docx`

---

## Cobertura

| Setor | Tickers |
|---|---|
| Aeroespacial | EMBR3 (Embraer) |
| Automotivo | POMO4 (Marcopolo), RAPT4 (Randoncorp), FRAS3 (Frasle), MYPK3 (Iochpe-Maxion) |
| Energia e Tecnologia | WEGE3 (WEG), INTB3 (Intelbras) |
| Outros | GGPS3 (GPS Participações), ARML3 (Armac), MILS3 (Mills) |

---

## Pré-requisitos

Antes de rodar pela primeira vez, verifique:

- [ ] **Docker Desktop** aberto e rodando (necessário para o Qdrant)
- [ ] **Ollama** instalado e rodando com o modelo `qwen3.5:9b`
- [ ] Arquivo **`.env`** configurado na pasta do projeto
- [ ] **Qdrant** iniciado via Docker

```powershell
# Iniciar Qdrant (só precisa rodar uma vez, fica em background)
docker compose up -d

# Verificar se Ollama está rodando
ollama list   # deve mostrar qwen3.5:9b na lista
```

---

## Operação Diária

### Rodar o pipeline completo

```powershell
# 1. Ativar o ambiente Python
.venv\Scripts\activate

# 2. Rodar
python main.py
```
**1. Rodar o app de revisão**

```powershell
.venv\Scripts\activate
streamlit run review_feedback.py
```

O pipeline leva entre 15–30 minutos dependendo do volume de notícias do dia.
O briefing é salvo automaticamente em `output/`.

### Rodar sem baixar textos completos (mais rápido, menos preciso)

```powershell
python main.py --no-enrich
```

Usa apenas os títulos das notícias para triagem. Mais rápido (~8 min), mas
artigos com títulos genéricos podem ser perdidos ou mal classificados.

### Modo agendado (roda sozinho todo dia às 7h)

```powershell
python main.py --schedule

# Ou em horário diferente:
python main.py --schedule --schedule-hour 8
```

O processo fica rodando em background e executa automaticamente nos dias úteis.

### Ver log detalhado enquanto roda

```powershell
python main.py --verbose
```

---

## Entendendo o Briefing

O documento Word gerado tem a seguinte estrutura:

```
Resumo Executivo
  → Contagem de notícias por sentimento ([+] Positivo, [-] Negativo, [=] Neutro)
  → Tabela de ações requeridas (quando há flash notes ou updates de modelo necessários)

Aeroespacial
  EMBR3 — Embraer
    [notícias analisadas]

Automotivo
  POMO4 — Marcopolo
  RAPT4 — Randoncorp
  ...

Energia e Tecnologia
  WEGE3 — WEG
  INTB3 — Intelbras

Outros
  GGPS3 / ARML3 / MILS3

Macro / Setorial
  → Notícias relevantes para os setores sem ticker específico
```

Cada notícia mostra: sentimento, título, fonte, data, síntese analítica,
e flag de ação requerida quando aplicável.

---

## Feedback Loop (Semanal)

O sistema aprende com o tempo. A cada run, ele salva automaticamente os
artigos que ficaram "na fronteira" da decisão em `feedback/candidates.jsonl`.
Classificar esses artigos melhora a precisão do screener progressivamente.

### Passo a passo

**1. Rodar o app de revisão**

```powershell
.venv\Scripts\activate
streamlit run review_feedback.py
```

Abre no browser em `http://localhost:8501`.

**2. Classificar os artigos**

Para cada artigo exibido, clicar:
- **✅ Relevante** — o artigo É relevante para a cobertura (o sistema errou ao descartar)
- **❌ Não Relevante** — o artigo NÃO é relevante (confirma o descarte)
- **⏭ Pular** — não tenho certeza, revisar depois

**3. Exportar e re-ingerir**

Quando terminar a revisão, clicar em **"Salvar para labeled/"** no app.
Em seguida, no terminal:

```powershell
python -m data.ingest_history       # re-ingere com os novos rótulos
python -m data.calibrate_threshold  # recalibra o threshold de decisão
```

> **Frequência recomendada:** uma vez por semana, revisando o acumulado.
> Com 50+ negativos rotulados, a precisão do screener melhora visivelmente.

---

## Expandir a Base de Notícias Históricas

Quanto mais exemplos relevantes o sistema conhece, melhor ele triagem.

### Adicionar artigos via lista de URLs

Se você tiver uma lista de URLs de artigos relevantes:

```powershell
# Criar um arquivo urls.txt com uma URL por linha
# Então rodar:
python -m data.ingest_from_urls urls.txt

# Depois re-ingerir e recalibrar
python -m data.ingest_history
python -m data.calibrate_threshold
```

O script extrai automaticamente título, texto e data de cada URL.

### Adicionar artigos manualmente ao dataset

Editar `news_dataset/clipping_enriched_dummy.jsonl` adicionando linhas no formato:

```json
{
  "date": "01/05/2026",
  "title": "Embraer fecha contrato com Delta Air Lines",
  "url": "https://...",
  "ticker": "EMBR3",
  "relevant_yes_no": true,
  "body": "Texto completo do artigo..."
}
```

Depois rodar `ingest_history` e `calibrate_threshold`.

---

## Arquivos Importantes

| Arquivo/Pasta | O que é |
|---|---|
| `output/briefing_*.docx` | Briefings gerados (um por execução) |
| `output/pipeline_YYYYMMDD.log` | Log completo de cada run |
| `output/screening_audit_*.jsonl` | Registro de todos os artigos triados (score, tier, tickers) |
| `feedback/candidates.jsonl` | Near-misses aguardando revisão |
| `feedback/labeled/` | Artigos já revisados (usados no próximo ingest) |
| `config/calibration.json` | Threshold de decisão atual do screener |
| `news_dataset/` | Base de exemplos históricos |
| `.env` | Configurações e chaves de API |

---

## Solução de Problemas

**Pipeline não encontra artigos relevantes**
- Verifique conectividade com a internet
- Rode `--verbose` para ver quais feeds estão falhando
- Considere expandir a base histórica com mais exemplos

**Ollama não responde / timeout**
- Verifique se o Ollama está rodando: `ollama list`
- O modelo `qwen3.5:9b` precisa estar disponível
- Aumentar timeout: não é necessário, o pipeline já usa 240s por artigo

**Docker / Qdrant não conecta**
- Abrir Docker Desktop e aguardar inicialização completa
- Rodar `docker compose up -d` novamente
- Verificar em `http://localhost:6333/dashboard`

**Briefing gerado mas análises são genéricas (`[Análise automática — sem API]`)**
- O Ollama retornou JSON inválido — geralmente sinal de que o modelo está
  sobrecarregado ou o thinking mode está ativo
- Reiniciar o Ollama e rodar novamente

**Screener triando poucos artigos (< 10)**
- Base histórica pequena ou pouco diversa — expandir com mais exemplos
- Rodar o feedback loop com artigos recentes para melhorar a calibração
