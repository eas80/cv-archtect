"""Avvio in produzione (Render): app Streamlit e webhook Stripe in un solo processo.

    uvicorn server:app --host 0.0.0.0 --port $PORT

Il webhook risponde su /stripe/webhook, accanto all'app; seo.py serve la pagina iniziale con i
meta tag, robots.txt e sitemap.xml.
"""

import logging

import streamlit as st

import seo
from webhook_server import ROUTES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = st.App("app.py", routes=ROUTES + seo.ROUTES)
