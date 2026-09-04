import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
load_dotenv(Path(__file__).with_name(".env.local"))

# The Central's public article catalog can live in a different Supabase project.
KB_SUPABASE_URL = os.getenv("KB_SUPABASE_URL", "")
KB_SUPABASE_ANON_KEY = os.getenv("KB_SUPABASE_ANON_KEY", "")
KB_LIVE_ARTICLE_HYDRATION_ENABLED = os.getenv(
    "KB_LIVE_ARTICLE_HYDRATION_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ORG_ID = os.getenv("OPENAI_ORG_ID")
OPENAI_PROJECT_ID = os.getenv("OPENAI_PROJECT_ID")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5.5")
DEFAULT_MINI_MODEL = os.getenv("DEFAULT_MINI_MODEL", "gpt-5.4-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "gpt-transcribe")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_HOST = os.getenv("PINECONE_HOST")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "inchurch-hubspot-kb")
PINECONE_SCORE_THRESHOLD = float(os.getenv("PINECONE_SCORE_THRESHOLD", "0.3"))
HUBSPOT_GRAPHQL_API = os.getenv("HUBSPOT_GRAPHQL_API")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
INRADAR_API_URL = os.getenv(
    "INRADAR_API_URL",
    "https://www.inradar.com.br/api/v1/webhook/operations/read_event/",
)
INRADAR_AUTH_TOKEN = os.getenv("INRADAR_AUTH_TOKEN")

WHATSAPP_MAX_MESSAGE_LENGTH = min(4096, max(256, int(os.getenv("WHATSAPP_MAX_MESSAGE_LENGTH", "3500"))))
HUBSPOT_POLLING_ENABLED = os.getenv("HUBSPOT_POLLING_ENABLED", "true").lower() in {"1", "true", "yes"}
HUBSPOT_POLLING_INTERVAL = max(5, int(os.getenv("HUBSPOT_POLLING_INTERVAL", "10")))
HUBSPOT_MESSAGE_DEBOUNCE_SECONDS = min(30, max(0, int(os.getenv("HUBSPOT_MESSAGE_DEBOUNCE_SECONDS", "5"))))
DELIVERY_DB_PATH = os.getenv("DELIVERY_DB_PATH", str(Path(__file__).parent / ".local" / "delivery.sqlite3"))
SUPABASE_CONVERSATION_MEMORY_ENABLED = os.getenv("SUPABASE_CONVERSATION_MEMORY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
