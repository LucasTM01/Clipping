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
    ollama_model: str = "llama3.1:8b"

    # OpenAI GPT
    gpt_model: str = "gpt-4o"
    gpt_max_tokens: int = 1524

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

    # Document
    document_title: str = "Briefing de Mercado — Equity Research"
    document_subtitle: str = "Capital Goods & Industrials"

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
        "name": "Financial Times Brazil",
        "url": "https://www.ft.com/brazil?format=rss",
        "language": "en",
    },
]

# --- Universo de Cobertura ---
COVERAGE_UNIVERSE = {
    "ARML3": {
        "company": "Armac",
        "sector": "Equipment Rental",
        "keywords": [
            "armac", "arml3", "locação de equipamentos", "equipment rental",
            "plataformas aéreas", "movimentação de carga",
        ],
    },
    "MILS3": {
        "company": "Mills",
        "sector": "Equipment Rental",
        "keywords": [
            "mills", "mils3", "locação de equipamentos", "plataformas elevatórias",
            "equipment rental", "infraestrutura",
        ],
    },
    "WEGE3": {
        "company": "WEG",
        "sector": "Industrials / Electrical Equipment",
        "keywords": [
            "weg", "wege3", "motores elétricos", "transformadores", "energia eólica",
            "energia solar", "automação industrial", "geração distribuída",
            "electric motors", "renewable energy",
        ],
    },
    "EMBR3": {
        "company": "Embraer",
        "sector": "Aerospace & Defense",
        "keywords": [
            "embraer", "embr3", "embj3", "aviação", "jatos executivos", "defesa",
            "eve", "evtol", "aerospace", "c-390", "kc-390", "e-jet", "e2",
            "praetor", "phenom",
        ],
    },
    "POMO4": {
        "company": "Marcopolo",
        "sector": "Automotive / Bus Manufacturing",
        "keywords": [
            "marcopolo", "pomo4", "ônibus", "bus", "transporte público",
            "carrocerias", "brt",
        ],
    },
    "RAPT4": {
        "company": "Randoncorp",
        "sector": "Automotive / Trailers",
        "keywords": [
            "randoncorp", "randon", "rapt4", "implementos rodoviários",
            "semirreboques", "reboques", "trailers", "autopeças",
        ],
    },
    "FRAS3": {
        "company": "Frasle",
        "sector": "Auto Parts",
        "keywords": [
            "frasle", "fras3", "freios", "pastilhas", "lonas de freio",
            "autopeças", "aftermarket",
        ],
    },
    "MYPK3": {
        "company": "Iochpe-Maxion",
        "sector": "Auto Parts / Wheels",
        "keywords": [
            "iochpe", "maxion", "mypk3", "rodas", "chassis",
            "autopeças", "wheels", "automotive components",
        ],
    },
    "GGPS3": {
        "company": "GPS Participações",
        "sector": "Services / Facility Management",
        "keywords": [
            "gps participações", "ggps3", "terceirização", "facilities",
            "serviços", "outsourcing", "manutenção industrial",
        ],
    },
    "INTB3": {
        "company": "Intelbras",
        "sector": "Industrials / Electronics & Security",
        "keywords": [
            "intelbras", "intb3", "segurança eletrônica", "câmeras de segurança",
            "automação residencial", "redes", "energia solar", "alarmes",
            "controle de acesso", "cftv", "smart home",
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
