"""
Configurações centrais do pipeline de clipping.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Diretórios ---
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# --- API Keys ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "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")  # Coloque a chave no .env, não aqui!

# --- Ollama ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# --- Analysis backend ---
# Set ANALYSIS_BACKEND=openai to use OpenAI GPT instead of local Ollama.
ANALYSIS_BACKEND = os.getenv("ANALYSIS_BACKEND", "ollama")   # "ollama" | "openai"
ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "qwen3.5:9b")   # Ollama model for analysis

# --- OpenAI GPT (análise principal, usado quando ANALYSIS_BACKEND=openai) ---
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4o")
GPT_MAX_TOKENS = 1524

# --- Paralelismo ---
API_MAX_WORKERS = int(os.getenv("API_MAX_WORKERS", "5"))   # Chamadas GPT simultâneas
ENRICH_MAX_WORKERS = int(os.getenv("ENRICH_MAX_WORKERS", "10"))  # Fetches HTTP simultâneos

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

# --- Filtro temporal (horas) ---
MAX_ARTICLE_AGE_HOURS = 24

# --- Screener ---
SCREENER_SNIPPET_LENGTH = 500  # chars do texto para enviar ao LLM local
SCREENER_CONFIDENCE_THRESHOLD = 0.6

# --- Document ---
DOCUMENT_TITLE = "Briefing de Mercado — Equity Research"
DOCUMENT_SUBTITLE = "Capital Goods & Industrials"
