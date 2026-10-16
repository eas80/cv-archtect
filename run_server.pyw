"""Avvio dell'app in background (Utilità di pianificazione), senza finestra.

Con pythonw non esiste una console: l'output di Streamlit, errori compresi,
viene scritto in logs/streamlit.log.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "streamlit.log"
MAX_LOG_BYTES = 5 * 1024 * 1024

LOG_DIR.mkdir(exist_ok=True)
# Rotazione minima: un solo file precedente, così il log non cresce all'infinito.
if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
    LOG_FILE.replace(LOG_DIR / "streamlit.log.1")

# Va fatto prima di importare Streamlit, che aggancia i suoi logger a stderr all'import.
log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

# .streamlit/config.toml (tema) e .env vengono letti dalla cartella corrente.
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

# Come in produzione: app e webhook Stripe (/stripe/webhook) nello stesso processo.
uvicorn.run("server:app", host="127.0.0.1", port=8520)
