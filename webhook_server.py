"""Webhook Stripe: riceve i pagamenti completati e accredita i crediti su SQLite.

Avvio:
    python webhook_server.py

In locale Stripe non può raggiungere localhost: inoltra gli eventi con la Stripe CLI
    stripe listen --forward-to localhost:8521/stripe/webhook
e copia il segreto «whsec_...» che stampa in STRIPE_WEBHOOK_SECRET nel file .env.
"""

import logging
import sys

import stripe
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

import config
from services import auth_service
from services.payment_service import fulfill_checkout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webhook")

FULFILL_EVENTS = {"checkout.session.completed", "checkout.session.async_payment_succeeded"}

app = FastAPI(title=f"{config.APP_TITLE} · webhook Stripe", docs_url=None, redoc_url=None)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(None)) -> dict:
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, config.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        log.warning("Evento rifiutato: %s", exc)
        raise HTTPException(status_code=400, detail="Firma non valida") from exc

    if event.type in FULFILL_EVENTS:
        # Un'eccezione qui produce un 500 e Stripe ritenta l'invio più tardi.
        fulfill_checkout(event.data.object)
    return {"received": True}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    if not config.STRIPE_WEBHOOK_SECRET:
        sys.exit("STRIPE_WEBHOOK_SECRET mancante nel file .env.")
    auth_service.init_db()
    uvicorn.run(app, host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)
