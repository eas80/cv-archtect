"""Configurazione centralizzata dell'applicazione CV Architect."""

import os

from dotenv import load_dotenv

load_dotenv()

APP_TITLE = "CV Architect"
APP_ICON = "📄"

# --- Claude -----------------------------------------------------------------
# Sovrascrivibile da .env con CLAUDE_MODEL=<model-id>.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "16000"))
# low | medium | high | xhigh | max
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")
CLAUDE_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "300"))

# --- Utenti e crediti -------------------------------------------------------
# Stringa di connessione Postgres (Supabase: Project Settings > Database > Connection string,
# «Session pooler», raggiungibile anche dai server senza IPv6 come Render).
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
# Fuso orario per date e ore mostrate nell'app (i server, come Render, girano in UTC).
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Rome")
# Crediti regalati a ogni nuovo account (0: si acquistano); ogni analisi/generazione ne consuma 1.
INITIAL_CREDITS = int(os.getenv("INITIAL_CREDITS", "0"))
# Account admin (uso illimitato), separati da virgola. Sovrascrivibile da .env.
ADMIN_EMAILS = [
    e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "easycons@gmail.com").split(",") if e.strip()
]
# Per quanti giorni il browser resta collegato (cookie), anche ricaricando la pagina.
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
# Ogni quanti secondi la sidebar rilegge il saldo (per vedere subito i crediti acquistati).
CREDITS_REFRESH_SECONDS = 5

# --- Pagamenti (Stripe) -----------------------------------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_CURRENCY = "eur"
# Indirizzo dell'app a cui Stripe riporta l'utente dopo il pagamento.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8520").rstrip("/")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "127.0.0.1")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8521"))

# --- Email (Brevo) ----------------------------------------------------------
# API key da Brevo > SMTP & API > API Keys. Senza chiave le email finiscono nel log (utile in locale).
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
# Mittente: deve essere un indirizzo verificato su Brevo (Senders, domains & dedicated IPs).
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "").strip()
EMAIL_SENDER_NAME = os.getenv("EMAIL_SENDER_NAME", APP_TITLE).strip()
# Validità dei codici inviati per email, tentativi ammessi e attesa minima tra due invii.
EMAIL_CODE_MINUTES = 15
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_RESEND_SECONDS = 60

# --- Scraper ----------------------------------------------------------------
SCRAPER_TIMEOUT_SECONDS = 15
SCRAPER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Sotto questa soglia la pagina è quasi certamente un login wall o è resa via JS.
SCRAPER_MIN_TEXT_LENGTH = 200


def get_env_api_key() -> str | None:
    """Restituisce la API key dalla variabile d'ambiente, se valorizzata."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    # Il valore segnaposto di .env.example non è una chiave reale.
    if not key or key == "sk-ant-...":
        return None
    return key
