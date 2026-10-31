"""
Configurações centrais do pipeline de clipping.
"""

from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # API keys
    openai_api_key: str = ""

    # Analysis backend
    analysis_backend: str = "ollama"   # "ollama" | "openai"
    analysis_model: str = "qwen3.5:9b"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    # ollama_model: legado — usado APENAS com --legacy-screener (não recomendado).
    # O screener padrão v2 usa BAAI/bge-m3 via sentence-transformers, sem Ollama.
    # O deep dive Estágio A usa ANALYSIS_MODEL (acima), não este campo.
    ollama_model: str = "llama3.1:8b"

    # OpenAI GPT
    gpt_model: str = "gpt-4o-mini"
    gpt_max_tokens: int = 1524

    # DeepSeek (API compatível com OpenAI — usa o mesmo SDK com base_url própria)
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Deep dive — análise em dois estágios (3º email)
    #   Estágio A (resumo por notícia + números + grupo): roda no modelo LOCAL para
    #   economizar custo de API. Estágio B (síntese macro conectando tudo): API potente.
    deep_dive_enabled: bool = False
    summary_backend: str = "ollama"          # Estágio A: "ollama" | "openai" | "deepseek"
    summary_model: str = ""                   # override; "" → analysis_model (ollama) / default do backend
    synthesis_backend: str = "deepseek"       # Estágio B: "deepseek" | "openai"
    synthesis_model: str = ""                 # override; "" → default do backend
    deep_dive_max_workers: int = 3            # paralelismo do Estágio A (Ollama enfileira)
    deep_dive_max_full_text: int = 6000       # chars de full_text enviados ao Estágio A
    deep_dive_to: list[str] = ["lucas.melotti.safra@gmail.com"]

    # Parallelism
    api_max_workers: int = 5
    enrich_max_workers: int = 10

    # Screener (legacy)
    screener_confidence_threshold: float = 0.6
    screener_snippet_length: int = 500
    max_article_age_hours: int = 24

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "clipping_history"

    # Embedding
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = 32
    embedding_top_k: int = 5
    embedding_top_k_neg: int = 3  # negativos para margin scoring (Fase 2)

    # Document
    document_title: str = "Briefing de Mercado — Equity Research"
    document_subtitle: str = "Capital Goods & Industrials"
    briefing_max_articles: int = 50  # top-N por score; 0 = sem limite

    # Scrapers (non-RSS)
    scraper_max_pages: int = 10
    scraper_concurrency: int = 4

    # Proprietary news database (SQLite)
    news_db_path: str = "output/news_database.db"

    # DataHandler lake (data/drive_sync.py): G: mount first, rclone/Drive API
    # when the mount is unreachable (scheduled S4U runs). "" remote = API off.
    datahandler_data_dir: str = ""
    datahandler_remote: str = "gdrive:4. Trabalho/Safra_CG_db/data"

    # Email notifications
    email_enabled: bool = False
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_username: str = ""
    email_password: str = ""          # App Password do Google (não a senha normal)
    email_from: str = ""
    email_briefing_to: list[str] = ["lucas.melotti@safra.com.br", "lucas.melotti.safra@gmail.com"]
    email_ops_to: list[str] = ["lucas.melotti.safra@gmail.com"]

    @model_validator(mode="after")
    def _validate_openai(self) -> "Settings":
        if self.analysis_backend == "openai" and not self.openai_api_key:
            raise ValueError(
                "ANALYSIS_BACKEND=openai requires OPENAI_API_KEY to be set in .env"
            )
        return self


settings = Settings()

# --- Diretórios ---
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Aliases de retrocompatibilidade ---
# Permite que os módulos existentes continuem importando as constantes antigas
# sem alteração enquanto não são migrados para `settings.*`.
OPENAI_API_KEY = settings.openai_api_key
OLLAMA_BASE_URL = settings.ollama_base_url
OLLAMA_MODEL = settings.ollama_model
ANALYSIS_BACKEND = settings.analysis_backend
ANALYSIS_MODEL = settings.analysis_model
GPT_MODEL = settings.gpt_model
GPT_MAX_TOKENS = settings.gpt_max_tokens
API_MAX_WORKERS = settings.api_max_workers
ENRICH_MAX_WORKERS = settings.enrich_max_workers
SCREENER_CONFIDENCE_THRESHOLD = settings.screener_confidence_threshold
SCREENER_SNIPPET_LENGTH = settings.screener_snippet_length
MAX_ARTICLE_AGE_HOURS = settings.max_article_age_hours
DOCUMENT_TITLE = settings.document_title
DOCUMENT_SUBTITLE = settings.document_subtitle
SCRAPER_MAX_PAGES = settings.scraper_max_pages
SCRAPER_CONCURRENCY = settings.scraper_concurrency

# --- Scrapers habilitados (nomes em lowercase, devem casar com BaseScraper.name) ---
SCRAPERS_ENABLED = [
    "aeroin",
    "airway",
    "autodata",
    "automotive business",
    "canal solar",
    "diário do transporte",
    "aerospace global news",
    "randoncorp blog",
    "weg news",
    "infomoney",
    "oglobo",
    "portal solar",
    "reuters aerospace & defense",
    "reuters autos & transportation",
    "reuters energy",
    "bloomberg transportation",
    "bloomberg energy",
    "bloomberg industries",
    "cvm filings",
]

# --- Feeds RSS ---
RSS_FEEDS = [
    {
        "name": "Pipeline Valor",
        "url": "https://pox.globo.com/rss/pipelinevalor/",
        "language": "pt-br",
    },
    {
        "name": "Valor Econômico",
        "url": "https://pox.globo.com/rss/valor",
        "language": "pt-br",
    },
    {
        "name": "Estadão Política",
        "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/politica/",
        "language": "pt-br",
    },
    {
        "name": "Estadão Internacional",
        "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/internacional/",
        "language": "pt-br",
    },
    {
        "name": "Estadão Economia",
        "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/",
        "language": "pt-br",
    },
    {
        "name": "CNN",
        "url": "https://admin.cnnbrasil.com.br/feed/",
        "language": "pt-br",
    },
    {
        "name": "Folha Mercados",
        "url": "https://feeds.folha.uol.com.br/mercado/rss091.xml",
        "language": "pt-br",
    },
    {
        "name": "Folha Mundo",
        "url": "https://feeds.folha.uol.com.br/mundo/rss091.xml",
        "language": "pt-br",
    },
    {
        # O feed traz o texto completo; substitui o scraper HTML (0 artigos/dia).
        "name": "Brazil Journal",
        "url": "https://braziljournal.com/feed/",
        "language": "pt-br",
    },
]

# --- Universo de Cobertura ---
# NOTA: o matching de keywords usa limite de palavra (\b) — ver
# screener.ticker_classifier._keyword_fallback. Por isso termos curtos como
# "weg", "mills", "randon" são seguros (não casam "norwegian", "windmills", etc.).
# Mantenha as keywords ESPECÍFICAS da empresa; termos genéricos do setor
# (ex.: "aviação", "aircraft", "rodas") geram falsos positivos — deixe a
# desambiguação fina para o LLM (config/prompts.py → ANALYSIS_SYSTEM_PROMPT).
COVERAGE_UNIVERSE = {
    "ARML3": {
        "company": "Armac",
        "sector": "Equipment Rental",
        "keywords": [
            "armac", "arml3",
            "locação de equipamentos", "locação de máquinas", "locação de caminhões",
            "aluguel de equipamentos", "locação pesada", "máquinas pesadas",
            "equipment rental", "machinery rental", "heavy equipment rental",
            "heavy machinery rental", "plataforma elevatória",
        ],
    },
    "MILS3": {
        "company": "Mills",
        "sector": "Equipment Rental",
        "keywords": [
            "mills", "mils3", "mills estruturas",
            "plataformas elevatórias", "plataforma elevatória",
            "andaimes", "locação de andaimes", "escoramento", "cimbramento",
            "obras civis",
            "aerial platforms", "access platforms", "scaffolding",
            "equipment rental", "machinery rental",
        ],
    },
    "WEGE3": {
        "company": "WEG",
        "sector": "Industrials / Electrical Equipment",
        "keywords": [
            "weg", "wege3",
            "motores elétricos", "motor elétrico", "electric motors", "electric motor",
            "transformadores", "transformers",
            "energia eólica", "wind energy", "wind power",
            "energia solar", "solar energy",
            "automação industrial", "industrial automation",
            "geração distribuída", "data center", "renewable energy",
        ],
    },
    "EMBR3": {
        "company": "Embraer",
        "sector": "Aerospace & Defense",
        # Apenas termos ESPECÍFICOS da Embraer — sem "aviação"/"aircraft"/"aerospace"
        # genéricos, que puxavam notícias de Boeing/Airbus/cias aéreas para EMBR3.
        "keywords": [
            "embraer", "embr3", "embj3",
            "eve air mobility", "evtol",
            "c-390", "kc-390", "e-jet", "e190", "e195", "e175", "e170",
            "e195-e2", "e190-e2", "praetor", "phenom", "legacy 600",
        ],
    },
    "POMO4": {
        "company": "Marcopolo",
        "sector": "Automotive / Bus Manufacturing",
        "keywords": [
            "marcopolo", "pomo4", "volare", "paradiso", "fabus",
            "ônibus", "onibus", "ônibus elétrico", "ônibus urbano", "ônibus rodoviário",
            "carroceria", "carrocerias", "chassis de ônibus", "brt",
            "transporte público",
            "buses", "minibus", "minibuses", "electric bus", "city bus",
            "intercity bus", "bus maker", "bus manufacturer",
            "articulado", "biarticulado",
        ],
    },
    "RAPT4": {
        "company": "Randoncorp",
        "sector": "Automotive / Trailers",
        "keywords": [
            "randoncorp", "randon", "rapt4", "anfir",
            "implementos rodoviários", "implementos", "plataforma rodoviária",
            "semirreboque", "semirreboques", "reboque", "reboques",
            "carreta", "graneleiro", "furgão", "tanque rodoviário",
            "trailer", "trailers", "semi-trailer", "trailer maker",
            "road implements", "autopeças",
        ],
    },
    "FRAS3": {
        "company": "Frasle",
        "sector": "Auto Parts",
        "keywords": [
            "frasle", "fras-le", "fras3", "frasle mobility",
            "freios", "pastilha de freio", "lona de freio", "disco de freio",
            "friction material", "brake pad", "brake lining", "brakes",
            "aftermarket", "autopeças",
        ],
    },
    "MYPK3": {
        "company": "Iochpe-Maxion",
        "sector": "Auto Parts / Wheels",
        "keywords": [
            "iochpe", "maxion", "mypk3", "iochpe-maxion", "maxion wheels",
            "rodas de aço", "rodas de alumínio", "roda automotiva", "roda veicular",
            "automotive wheels", "steel wheels", "aluminum wheels", "wheel maker",
        ],
    },
    "GGPS3": {
        "company": "GPS Participações",
        "sector": "Services / Facility Management",
        "keywords": [
            "gps participações", "gps participacoes", "ggps3",
            "terceirização", "terceirizacao", "serviços terceirizados",
            "facilities", "facility management", "facilities management",
            "outsourcing", "manutenção industrial",
            # temas trabalhistas que afetam diretamente a base de custo de GPS
            "mão de obra", "reforma trabalhista", "jornada de trabalho",
            "6x1", "escala 6x1", "jornada 6x1",
        ],
    },
    "INTB3": {
        "company": "Intelbras",
        "sector": "Industrials / Electronics & Security",
        "keywords": [
            "intelbras", "intb3",
            "segurança eletrônica", "electronic security",
            "câmeras de segurança", "security cameras", "surveillance",
            "automação residencial", "smart home",
            "controle de acesso", "access control", "cftv", "alarmes",
        ],
    },
}

# Setores relevantes para a seção macro/setorial
RELEVANT_SECTORS = [
    "equipment rental", "locação de equipamentos",
    "aerospace", "aeroespacial", "aviação", "defesa",
    "industrials", "industrial", "manufatura",
    "energia", "energy", "eólica", "solar", "transmissão",
    "infraestrutura", "infrastructure", "concessões", "rodovias",
    "saneamento", "transporte", "logística",
    "automotivo", "automotive", "autopeças",
    "bndes", "pac", "leilão", "concessão",
    "câmbio", "dólar", "selic", "juros", "inflação",
]
