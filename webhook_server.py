"""Webhook Stripe: riceve i pagamenti completati e accredita i crediti.

In produzione la rotta gira dentro l'app (server.py), sullo stesso indirizzo:
    https://<tuo-dominio>/stripe/webhook

Si può anche avviare da sola (es. in locale accanto a `streamlit run`):
    python webhook_server.py
In locale Stripe non può raggiungere localhost: inoltra gli eventi con la Stripe CLI
    stripe listen --forward-to localhost:8521/stripe/webhook
e copia il segreto «whsec_...» che stampa in STRIPE_WEBHOOK_SECRET nel file .env.
"""

import logging
import sys

import stripe
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import config
from services.payment_service import fulfill_checkout

log = logging.getLogger("webhook")

FULFILL_EVENTS = {"checkout.session.completed", "checkout.session.async_payment_succeeded"}


async def stripe_webhook(request: Request) -> JSONResponse:
    if not config.STRIPE_WEBHOOK_SECRET:
        log.error("Evento ricevuto ma STRIPE_WEBHOOK_SECRET non è configurato")
        return JSONResponse({"error": "Webhook non configurato"}, status_code=503)
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, request.headers.get("stripe-signature"), config.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.SignatureVerificationError) as exc:
        log.warning("Evento rifiutato: %s", exc)
        return JSONResponse({"error": "Firma non valida"}, status_code=400)

    if event.type in FULFILL_EVENTS:
        # Accesso al database bloccante: fuori dall'event loop. Un'eccezione produce un 500
        # e Stripe ritenta l'invio più tardi.
        await run_in_threadpool(fulfill_checkout, event.data.object)
    return JSONResponse({"received": True})


ROUTES = [Route("/stripe/webhook", stripe_webhook, methods=["POST"])]


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.STRIPE_WEBHOOK_SECRET:
        sys.exit("STRIPE_WEBHOOK_SECRET mancante nel file .env.")
    uvicorn.run(Starlette(routes=ROUTES), host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)
