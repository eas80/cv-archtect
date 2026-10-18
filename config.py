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
# Crediti regalati a ogni nuovo account (0: si acquistano).
INITIAL_CREDITS = int(os.getenv("INITIAL_CREDITS", "0"))
# Ogni lotto di crediti (acquisto, regalo dell'admin) scade dopo questi mesi (Termini, art. 5.5).
CREDITS_VALIDITY_MONTHS = 24
# 1 credito = 1 candidatura completa (CV, annuncio, LinkedIn): si scala al primo documento
# scaricato o salvato, o ai testi LinkedIn. Le operazioni con l'AI non costano singolarmente,
# ma hanno dei tetti per contenere i costi: per candidatura e per utente nelle ultime 24 ore.
SESSION_AI_CALLS_LIMIT = int(os.getenv("SESSION_AI_CALLS_LIMIT", "20"))
DAILY_AI_CALLS_LIMIT = int(os.getenv("DAILY_AI_CALLS_LIMIT", "40"))
# Account admin (uso illimitato), separati da virgola. Sovrascrivibile da .env.
ADMIN_EMAILS = [
    e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "easycons@gmail.com").split(",") if e.strip()
]
# Per quanti giorni il browser resta collegato (cookie), anche ricaricando la pagina.
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
# Ogni quanti secondi la barra in alto rilegge il saldo (per vedere subito i crediti acquistati).
CREDITS_REFRESH_SECONDS = 5

# --- Pagamenti (Stripe) -----------------------------------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_CURRENCY = "eur"
# Chiavi di prova (sk_test_/rk_test_): l'app mostra la fascia «Modalità test» e accredita
# solo pagamenti di prova; con chiavi live solo pagamenti veri.
STRIPE_TEST_MODE = STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_"))
# Indirizzo dell'app a cui Stripe riporta l'utente dopo il pagamento.
# Senza APP_BASE_URL si usa l'indirizzo pubblico che Render imposta da solo (RENDER_EXTERNAL_URL).
APP_BASE_URL = (os.getenv("APP_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8520").rstrip("/")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "127.0.0.1")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8521"))

# --- Email (Brevo) ----------------------------------------------------------
# API key da Brevo > SMTP & API > API Keys. Senza chiave le email finiscono nel log (utile in locale).
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
# Mittente: deve essere un indirizzo verificato su Brevo (Senders, domains & dedicated IPs).
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "").strip()
EMAIL_SENDER_NAME = os.getenv("EMAIL_SENDER_NAME", APP_TITLE).strip()
# Validità dei codici inviati per email, tentativi ammessi e attesa minima tra due invii.
# Destinatario dei messaggi inviati dal modulo «Contattaci».
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "cv.architect.info@gmail.com").strip()
CONTACT_RESEND_SECONDS = 60
EMAIL_CODE_MINUTES = 15
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_RESEND_SECONDS = 60

# --- Documenti legali ---------------------------------------------------------
# Testi in legal/*.md. Cambiando i testi in modo sostanziale si aggiorna la versione (data):
# alla registrazione si salva quella accettata dall'utente.
LEGAL_VERSION = "2026-10-18"

# --- SEO ----------------------------------------------------------------------
# L'ambiente di test (ramo staging su Render) non va indicizzato dai motori di ricerca.
SEO_NOINDEX = os.getenv("SEO_NOINDEX", "").strip() == "1" or os.getenv("RENDER_GIT_BRANCH") == "staging"
# Codice del meta tag di verifica di Google Search Console (solo il valore di «content»).
GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "").strip()

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
