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

- **Fontes (instâncias):** `"Bloomberg Energy"`, `"Bloomberg Transportation"`, `"Bloomberg Industries"`
- **Arquivo:** `scrapers/bloomberg.py` (`BloombergScraper`)
- **Por que não a página /industries/<x>:** PerimeterX ("Are you a robot?") bloqueia
  qualquer browser automatizado — inclusive Chrome real + perfil dedicado. A versão antiga
  (Playwright + stealth) retornava 0 artigos todos os dias.
- **Fontes de dados (públicas, só `requests`):**
  - Sitemap Google News `https://www.bloomberg.com/sitemaps/news/latest.xml` — todas as
    matérias dos últimos ~2 dias com `publication_date` exata e título; sem seção.
    Só `/news/articles/` e `/news/features/` (opinião/newsletters/live descartados).
  - RSS Industries `https://www.bloomberg.com/feeds/industries/news.rss` — ~20 matérias
    mais recentes com descrição (vira `full_text`).
- **Classificação:** cada matéria vai para UMA seção por regex no título + slug
  (`_TRANSPORTATION_RE` primeiro, depois `_ENERGY_RE`); sobras do feed Industries →
  "Bloomberg Industries"; sobras do sitemap (política, mercados…) são descartadas.
  As três instâncias compartilham um cache de 10 min — os feeds são baixados uma vez por run.
- **published_at:** `publication_date` (sitemap) / `pubDate` (RSS), UTC. Filtrado por `since`.
- **Conteúdo:** só a descrição do RSS quando houver; corpo é paywall e o Playwright
  enrich desiste após 3 bloqueios seguidos (ver `fetcher/rss_fetcher.py`).
- **Idioma:** `en`. ~50 artigos/dia nas três fontes.

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

## Sites WordPress (REST API) — Autodata, Canal Solar, Diário do Transporte, Aerospace Global News

- **Arquivo:** `scrapers/wordpress.py` (`WordPressScraper(name, base_url)`) — uma instância por site
- **Endpoint:** `GET {base}/wp-json/wp/v2/posts?per_page=50&page=N&orderby=date&order=desc&_fields=date_gmt,link,title,excerpt,content`
  Paginação até o post mais antigo da página < `since` (ou página incompleta).
- **published_at:** `date_gmt` (UTC exato). **Conteúdo:** `content.rendered` → texto
  (fallback `excerpt`) — sem fetch por artigo.
- **Por que REST e não HTML/RSS:** o scraper HTML do Autodata quebrou (listagem
  `/ultimas-noticias/` virou 404) e o do Canal Solar também (0 artigos/dia). Os RSS são
  curtos (10–12 itens) e o do Diário do Transporte mistura posts antigos.
- **Instâncias (registry.py):** `Autodata` (autodata.com.br), `Canal Solar`
  (canalsolar.com.br — a edição /en/ é tradução automática dos mesmos posts, sem rota REST),
  `Diário do Transporte` (diariodotransporte.com.br, ~30 posts/dia), `Aerospace Global News`
  (aerospaceglobalnews.com, `en`).
- **Novo site WordPress:** basta `WordPressScraper("Nome", "https://site")` no registry +
  nome em `SCRAPERS_ENABLED`. Testar antes: `curl https://site/wp-json/wp/v2/posts?per_page=1`.

---

## Randoncorp Blog

- **Base URL:** https://www.randoncorp.com/pt/blog/
- **Arquivo:** `scrapers/randoncorp.py` (`RandoncorpScraper`)
- **Plataforma:** Umbraco (sem `/wp-json/`, sem RSS). A página do blog lista TODOS os
  posts (~800 cards, ~1.4 MB): `<a href=".../pt/blog/<slug>/" data-title="...">` com
  `Publicado em DD/MM/YYYY`.
- **published_at:** só o dia (meia-noite BR_TZ). Filtro por dia: posts do dia de `since`
  em diante — um post de ontem pode aparecer no run das 19h e no da manhã seguinte.
- **Conteúdo:** `trafilatura` na página do post (paralelo), removendo o "Buscando por" do
  widget de busca.

---

## WEG News

- **Listagem:** Google News RSS `site:weg.net/institutional` (pt-BR) — `scrapers/google_news.py`
- **Por quê:** weg.net responde 403 a tudo que não é browser interativo (inclusive
  robots.txt e sitemap). URLs decodificadas e filtradas por `/institutional/<país>/<lang>/news/`
  (o Google também indexa catálogos e PDFs).
- **Conteúdo:** só título (corpo 403; domínio em `_NEVER_ENRICH_DOMAINS`). ~1 release a cada 2 dias.

---

## Reuters (múltiplas seções)

- **Arquivo:** `scrapers/reuters.py` (`ReutersScraper`, subclasse de `GoogleNewsScraper`)
- **Seções cobertas:**
  - `"Reuters Aerospace & Defense"` → `/business/aerospace-defense/`
  - `"Reuters Autos & Transportation"` → `/business/autos-transportation/`
  - `"Reuters Energy"` → `/business/energy/`
- **Plataforma:** DataDome — páginas de seção, artigos E o sitemap
  `/arc/outboundfeeds/news-sitemap/` respondem 401. O sitemap passava de forma
  intermitente (0–7 artigos/dia); as seções publicam 10–40/dia.
- **Fonte de dados:** Google News RSS com `site:` restrito ao caminho da seção:
  ```
  https://news.google.com/rss/search?q=site:reuters.com/business/energy+when:1d&hl=en-US&gl=US&ceid=US:en
  ```
  `when:Nd` cobre a janela; filtro fino por `since` no `pubDate`. Sufixo " - Reuters" removido.
- **URLs:** os links `news.google.com/rss/articles/<id>` são decodificados para a URL real
  da Reuters (GET da página do artigo → `data-n-a-sg`/`data-n-a-ts` → um POST em lote ao
  `batchexecute`, mapeado pelo índice da requisição). Falhou → mantém o link do Google
  (redireciona no browser).
- **Conteúdo:** `full_text = ""`. Screener usa o título. Playwright enrich desiste após 3 × 401.
- **Idioma:** `en`.
- **Nova seção:** `ReutersScraper("Nome", "/secao/path/")` em `registry.py` + nome
  (lowercase) em `SCRAPERS_ENABLED`.

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
  - A editoria "mercado" (`/noticias/editoria/mercado`) já aparece inteira em
    "últimas notícias" — não precisa de listagem própria. O site publica ~1 matéria/dia.
  - A data da listagem e a do artigo são idênticas (mesmo JSON-LD). O scraper
    usa a da listagem como primária para evitar fetch redundante de data.

---

## CVM RAD — Fatos Relevantes e Comunicados ao Mercado

- **Base URL:** https://www.rad.cvm.gov.br/ENET/frmConsultaExternaCVM.aspx
- **Arquivo:** `scrapers/cvm.py` (`CVMScraper`) — só HTTP, sem browser
- **Empresas cobertas:** 10 tickers com códigos CVM pré-configurados (EMBR3, POMO4, RAPT4, FRAS3, MYPK3, WEGE3, INTB3, GGPS3, ARML3, MILS3)
- **Categorias:** "Fato Relevante" e "Comunicado ao Mercado"
- **Listagem:** `POST .../frmConsultaExternaCVM.aspx/ListarDocumentos` (JSON, o mesmo AJAX
  que a tela usa) com `categoria="IPE_-1_-1_-1"` e período `dataDe..dataAte` — UMA chamada
  traz todas as companhias (~300 docs/dia); filtro local por código CVM + categoria + `since`.
  `d.dados` = linhas separadas por `&*`, campos por `$&`; o código vem como `02008-7`
  (= 20087, o dígito após o hífen faz parte do código).
- **Download de PDFs:** `frmDownloadDocumento.aspx?Tela=ext&numSequencia=..&numVersao=..&numProtocolo=..&descTipo=IPE&CodigoInstituicao=1`
  (args de `OpenDownloadDocumentos(...)` na coluna de ações). Content-type vem `text/html`, o corpo é o PDF.
- **URL do Article:** `frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega=<protocolo>`.
- **Extração de texto:** `pdfplumber` (limite: `_MAX_PDF_CHARS = 8000`). PDFs de imagem retornam texto vazio — screener usa o título rico que já contém categoria/tipo/assunto.
- **published_at:** "Data Entrega" (`DD/MM/YYYY HH:MM`, BR_TZ → UTC).
- **source format:** `"CVM — EMBR3"` (com —) — permite filtrar no audit log.
- **Notas:**
  - A versão anterior automatizava o formulário via Playwright com seletores que não
    casavam ("não foi possível selecionar categoria") — nunca coletou documento.
  - Dados abertos (dados.cvm.gov.br IPE) só atualizam semanalmente — não servem para o diário.
  - Muitos dias retornam 0 documentos (normal).

---

## Como adicionar um novo site

0. Antes de escrever parsing de HTML, procurar fonte estruturada: RSS (`/feed/`),
   WordPress REST (`/wp-json/wp/v2/posts` → só instanciar `WordPressScraper`),
   sitemap de notícias, ou Google News (`GoogleNewsScraper` com `site:`) se o site
   bloqueia acesso automatizado.
1. Criar `scrapers/<nome>.py` com classe que satisfaz `BaseScraper`
   (`scrapers/base.py`).
2. Reusar `browser_context()` e `extract_published_at()` de
   `scrapers/playwright_utils.py`.
3. Registrar instância em `SCRAPERS` (`scrapers/registry.py`).
4. Acrescentar nome (lowercase) a `SCRAPERS_ENABLED` em `config/settings.py`.
5. Documentar aqui: URL, paginação, seletores, formato de `published_at`,
   particularidades.
6. Salvar fixtures HTML em `scrapers/tests/fixtures/` e cobrir parsing offline.
