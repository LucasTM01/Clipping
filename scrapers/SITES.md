# Sites cobertos pelo módulo de scraping

Este arquivo é a referência viva por site. Toda mudança em seletor, atributo de
data ou particularidade conhecida deve ser refletida aqui.

---

## Aeroin

- **Base URL:** https://aeroin.net/
- **Arquivo:** `scrapers/aeroin.py` (`AeroinScraper`)
- **Plataforma:** WordPress (sem RSS)
- **Paginação:** botão "Carregar mais" no final da home. Selectors tentados, em
  ordem: `a:has-text('Carregar mais')`, `button:has-text('Carregar mais')`,
  `.load-more`, `a.load-more`, `button.load-more`. Loop até `SCRAPER_MAX_PAGES`
  cliques OU até a primeira data visível na listagem ser anterior a `since`.
- **Listagem (extração de URLs/títulos):** regex sobre o HTML procurando
  `<h2 class="entry-title"><a href="..." rel="bookmark">...</a>`, com fallback
  para qualquer `<a rel="bookmark" href="...">`. Duplicatas removidas por URL.
- **published_at — cascata** (`scrapers/playwright_utils.py:extract_published_at`):
  1. JSON-LD `datePublished` (preferido — Aeroin sempre expõe)
  2. `<meta property="article:published_time">`
  3. `<time datetime="...">`
  4. Fallback: texto visível `DD/MM/YYYY` → roundup para 00:00 BR_TZ
- **Conteúdo:** `trafilatura.extract` aplicado ao HTML já em memória (vindo do
  Playwright), sem refazer fetch HTTP.
- **Notas:**
  - UI mostra apenas data; horário existe no JSON-LD.
  - Sem login, sem paywall.
  - Concorrência interna: `SCRAPER_CONCURRENCY` páginas simultâneas sob um único
    browser headless.

---

## Airway

- **Base URL:** https://www.airway.com.br/ultimas-noticias/
- **Arquivo:** `scrapers/airway.py` (`AirwayScraper`)
- **Plataforma:** WordPress (sem RSS, sem WAF — requests funciona direto)
- **Paginação:** páginas numeradas via `<a class="next page-numbers" href="...">`.
  Itera até `SCRAPER_MAX_PAGES` páginas ou até não haver mais link "próxima".
  10 artigos por página.
- **Listagem (extração de URLs/títulos):** regex `<h2 class="entry-title"><a href="..." rel="bookmark">`.
  Os cards **não** expõem data — filtragem por `since` é feita só após fetch do artigo.
- **published_at — cascata** (`scrapers/playwright_utils.py:extract_published_at`):
  1. JSON-LD `datePublished` no nó `WebPage` (Airway usa `@graph`, não `NewsArticle`)
  2. `<meta property="article:published_time">`
  3. `<time datetime="...">`
- **Conteúdo:** `trafilatura.extract` sobre HTML via requests (sem Playwright).
- **Concorrência:** artigos buscados em paralelo via `ThreadPoolExecutor`
  (`SCRAPER_CONCURRENCY` workers).
- **Notas:** Sem paywall. Artigos têm ~1000–2500 chars de corpo.
  2–3 páginas cobre bem as últimas 24h.

---

## O Globo

- **Base URL:** https://oglobo.globo.com/ultimas-noticias/
- **Arquivo:** `scrapers/oglobo.py` (`OGloboScraper`)
- **Plataforma:** React SPA (JS-rendered) — Playwright necessário
- **Paginação:** botão "Veja mais" via `.load-more a`. O href segue o padrão
  `/ultimas-noticias/index/feed/pagina-N`. Loop até `SCRAPER_MAX_PAGES` cliques
  OU até o artigo mais antigo visível ser anterior a `since`.
- **Listagem (extração de URLs/títulos):** regex sobre `a.feed-post-link` (href
  e textContent do título). Container completo: `div.feed-post-body`.
- **Tempo relativo nos cards:** `span.feed-post-datetime` — texto puro sem
  atributo `datetime`. Formatos observados: `"Agora"`, `"Há N minutos"`,
  `"Há N horas"`, `"Há N dias"`. Convertido para datetime aproximado por
  `parse_relative_time_pt()` em `playwright_utils.py` — usado apenas para
  early-exit do loop de paginação, **não** como `published_at` do artigo.
- **published_at:** `<time datetime="ISO_DATE">` no artigo. O JSON-LD contém
  apenas `articleSection` (sem `datePublished`) e não há
  `<meta property="article:published_time">`. A cascata existente em
  `extract_published_at()` acerta via `_from_time_tag()` (3ª etapa).
- **Conteúdo:** `trafilatura.extract` sobre HTML já em memória (Playwright).
- **Notas:**
  - Sem login, sem paywall para notícias gerais.
  - Sessão Playwright única (listing + artigos) — mesma razão do Aeroin
    (greenlet safety, reuso de sessão).
  - Artigos de colunistas usam URL `/blogs/` ou `/post/`; artigos normais
    usam `/noticia/`. Ambos funcionam com os mesmos seletores.

---

## Bloomberg

- **URLs cobertas:**
  - `https://www.bloomberg.com/industries/transportation` (`phx-industries-transportation`)
  - `https://www.bloomberg.com/industries/energy` (`phx-industries-energy`)
- **Arquivo:** `scrapers/bloomberg.py` (`BloombergScraper`)
- **Plataforma:** Next.js SPA + Akamai bot protection
- **Acesso:** Requer Chrome real (`channel="chrome"`) + stealth init script
  (`--disable-blink-features=AutomationControlled` + `navigator.webdriver=undefined`).
  Chromium bundled do Playwright é bloqueado mesmo sem headless.
- **Paginação:** API interna `lineup-next/api/paginate`:
  ```
  GET /lineup-next/api/paginate
    ?id=archive_story_list
    &page=phx-industries-{industry}
    &offset=0|12|24|...
    &variation=archive&type=lineup_content&locale=en
  ```
  Chamada via `page.evaluate(fetch(..., {credentials:"include"}))` para reutilizar
  cookies da sessão já estabelecida. Cada página retorna 12 artigos. Loop até
  `publishedAt < since` OU `SCRAPER_MAX_PAGES` iterações.
- **Dados da listagem (JSON):** `items[].headline`, `items[].url` (relativo),
  `items[].publishedAt` (ISO-8601 UTC — sem necessidade de parse de datas),
  `items[].premium` (booleano de paywall), `items[].summary`.
- **Conteúdo:** Bloomberg expõe 1–2 parágrafos server-rendered antes do JS de
  paywall. `trafilatura.extract` sobre o HTML da página do artigo (Playwright)
  captura esses parágrafos. Fallback: `items[].summary` da API se trafilatura
  retornar vazio.
- **published_at:** `publishedAt` da API/`__NEXT_DATA__` (preferido). Fallback: `<time datetime>`
  no HTML do artigo via `extract_published_at()`.
- **Janela de coleta:** `BLOOMBERG_LOOKBACK_HOURS` (default 336 = 14 dias). Bloomberg
  Industry Pages são digests curados que atualizam com baixa frequência — usar janela
  mais ampla que o `since` do pipeline (24h) evita dias vazios. Novelty detection do
  screener (Qdrant) lida com deduplicação de artigos antigos repetidos.
- **Extração de artigos (offset=0 vs API):** O primeiro lote (offset=0) está embutido no
  `__NEXT_DATA__` do HTML da página de listagem. `_extract_items_from_html()` extrai via
  `_walk_for_items()`. Lotes subsequentes (offset=12, 24, …) buscados via `_FETCH_JS`.
- **URLs de artigo:** `__NEXT_DATA__` usa URLs absolutas; API retorna relativas. O scraper
  normaliza ambas (se começa com `http`, usa como está; senão, prefixa `_BASE`).
- **Idioma:** `en` (English).
- **Notas:**
  - Requer Google Chrome instalado na máquina. Pipeline falha graciosamente se
    Chrome não estiver disponível (log de erro, continua sem Bloomberg).
  - `Article.source`: `"Bloomberg Transportation"` ou `"Bloomberg Energy"`.
  - Artigos premium (`premium=true`) são incluídos — o resumo/parágrafos
    visíveis (1–2 parágrafos pre-paywall) ainda têm valor para o screener.
  - Bot detection Akamai é probabilístico — `channel="chrome"` + stealth funciona na
    maioria das sessões. Quando bloqueado ("Are you a robot?"), o scraper loga erro e
    retorna `[]` graciosamente.

---

## InfoMoney

- **Base URL:** https://www.infomoney.com.br/ultimas-noticias/
- **Arquivo:** `scrapers/infomoney.py` (`InfoMoneyScraper`)
- **Plataforma:** WordPress — expõe WP REST API pública
- **Sem Playwright** — pure `requests`, sem fetch por artigo
- **Endpoint:** `GET https://www.infomoney.com.br/wp-json/wp/v2/posts`
  ```
  ?per_page=100&page=N&orderby=date&order=desc&_fields=id,link,title,date_gmt,content
  ```
  Paginação por `page=N` (1-based). Stop quando o artigo mais antigo da página < `since`
  ou quando a API retorna 400/lista vazia.
- **published_at:** campo `date_gmt` (UTC, sem sufixo de timezone) → acrescenta `+00:00`.
- **Conteúdo:** `content.rendered` (HTML completo do artigo) → tags removidas com regex +
  `html.unescape()`. Texto resultante tem ~2000–5000 chars. O step `enrich_full_text` do
  pipeline não refaz fetch (texto já supera 300 chars).
- **Idioma:** `pt-br`
- **Notas:**
  - O botão "Carregar mais" da UI carrega via JS, mas o WP REST API é equivalente e
    não requer Playwright.
  - Total de posts no índice: ~559k. A paginação reversa por data garante que só
    páginas recentes são consultadas.

---

## Autodata

- **Base URL:** https://www.autodata.com.br/ultimas-noticias/
- **Arquivo:** `scrapers/autodata.py` (`AutodataScraper`)
- **Plataforma:** Listagem JS-rendered (Bootstrap + jQuery) — Playwright necessário para
  a fase de listagem. Páginas de artigo são server-rendered — requests funciona direto.
- **Paginação:** query string `?p=N` (página 1 = URL base; página 2+ = `?p=N`).
  Itera até `SCRAPER_MAX_PAGES` ou até data do artigo mais antigo < `since`.
  Fim detectado por ausência de links `?p=N` no HTML renderizado.
- **Listagem (extração de URLs/títulos):** cada card tem a estrutura:
  ```html
  <div class="todas-as-noticias-cel">
    <span class="todas-as-noticias-cat">Categoria</span>
    <li class="titulo-ultimas-noticias">
      <a href="https://www.autodata.com.br/noticias/YYYY/MM/DD/slug/id/">Título</a>
    </li>
  </div>
  ```
  A categoria está em `<span>` (não capturada pelo regex). Regex:
  `href="(https://www.autodata.com.br/noticias/YYYY/MM/DD/...)">...</a>`
- **published_at:** extraído do caminho da URL (`/noticias/YYYY/MM/DD/`) →
  `00:00:00 BR_TZ` convertido para UTC. O artigo não expõe `<time datetime>`,
  JSON-LD `datePublished` nem `<meta property="article:published_time">`.
- **Conteúdo:** `trafilatura.extract` sobre HTML do artigo via requests (paralelo).
- **Concorrência:** Playwright sequencial para listagem; `ThreadPoolExecutor`
  (`SCRAPER_CONCURRENCY` workers) para artigos.
- **Notas:**
  - Sem paywall. Artigos têm corpo completo (tipicamente 1500–6000 chars).
  - Horário não disponível — todos os artigos do mesmo dia têm `published_at` = meia-noite BR_TZ.

---

## Reuters (múltiplas seções)

- **Arquivo:** `scrapers/reuters.py` (`ReutersScraper`) — uma instância por seção
- **Seções cobertas:**
  - `"Reuters Aerospace & Defense"` → `/business/aerospace-defense/`
  - `"Reuters Autos & Transportation"` → `/business/autos-transportation/`
  - `"Reuters Energy"` → `/business/energy/`
- **Plataforma:** SPA + Cloudflare bot protection — acesso direto à página e artigos bloqueados
- **Fonte de dados:** Google News Sitemap XML (não protegido):
  ```
  https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml
  https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml&from=50
  https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml&from=100
  ...
  ```
  50 entradas por página em ordem cronológica reversa. Loop para até `SCRAPER_MAX_PAGES`
  páginas ou até data < `since`.
- **Filtragem por seção:** URLs cujo caminho contém o `section` configurado na instância.
  Cada página do sitemap mistura todas as seções da Reuters; só as entradas relevantes
  são retidas.
- **published_at:** `<news:publication_date>` ISO-8601 UTC no sitemap — sem parsing adicional.
- **Conteúdo:** `full_text = ""` — artigos individuais retornam desafio Cloudflare.
  O screener usa apenas o título para embedding. O step `enrich_full_text` tentará
  re-fetch e falhará silenciosamente.
- **Idioma:** `en` (English).
- **Notas:**
  - Sem Playwright — apenas `requests` para o sitemap XML.
  - `robots.txt` da Reuters bloqueia bots IA identificados; o scraper usa UA de
    navegador padrão e acessa apenas o sitemap público.
  - Para adicionar nova seção: instanciar `ReutersScraper("Nome", "/secao/path/")` em
    `registry.py` e acrescentar o nome (lowercase) a `SCRAPERS_ENABLED`.

---

## Portal Solar

- **Base URL:** https://www.portalsolar.com.br/noticias/ultimas-noticias
- **Arquivo:** `scrapers/portalsolar.py` (`PortalSolarScraper`)
- **Plataforma:** Next.js com SSR — server-rendered, sem Playwright
- **Paginação:** query string `?page=N` (página 1 = URL base sem parâmetro).
  19 artigos por página. Loop até `SCRAPER_MAX_PAGES` ou até o artigo mais
  antigo da página ser anterior a `since`.
- **Listagem (extração de URLs/títulos/datas):** cada card injeta um bloco
  `<script type="application/ld+json">` com `@type: "NewsArticle"` diretamente
  na listagem. Campos extraídos: `headline`, `datePublished`,
  `mainEntityOfPage.@id` (URL absoluta). Blocos de outros tipos (`WebSite`,
  `BreadcrumbList`, etc.) são ignorados.
- **published_at:** `datePublished` do JSON-LD da listagem (ISO-8601 UTC com
  sufixo `Z`). Não é necessário buscar o artigo para obter a data.
- **Conteúdo:** `trafilatura.extract` sobre HTML do artigo via requests
  (paralelo, `SCRAPER_CONCURRENCY` workers).
- **Idioma:** `pt-br`
- **Notas:**
  - Sem paywall, sem WAF relevante — User-Agent padrão de navegador é
    suficiente.
  - A data da listagem e a do artigo são idênticas (mesmo JSON-LD). O scraper
    usa a da listagem como primária para evitar fetch redundante de data.

---

## Como adicionar um novo site

1. Criar `scrapers/<nome>.py` com classe que satisfaz `BaseScraper`
   (`scrapers/base.py`).
2. Reusar `browser_context()` e `extract_published_at()` de
   `scrapers/playwright_utils.py`.
3. Registrar instância em `SCRAPERS` (`scrapers/registry.py`).
4. Acrescentar nome (lowercase) a `SCRAPERS_ENABLED` em `config/settings.py`.
5. Documentar aqui: URL, paginação, seletores, formato de `published_at`,
   particularidades.
6. Salvar fixtures HTML em `scrapers/tests/fixtures/` e cobrir parsing offline.
