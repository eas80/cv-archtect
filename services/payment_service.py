"""Acquisto di crediti con Stripe Checkout.

L'app crea la sessione di pagamento; i crediti li eroga solo il webhook
(webhook_server.py), dopo che Stripe ha confermato il pagamento.
"""

import logging
from dataclasses import dataclass

import stripe

import config
from services import auth_service

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreditPack:
    label: str
    credits: int
    amount_cents: int

    @property
    def price_label(self) -> str:
        return f"{self.amount_cents / 100:.2f} €".replace(".", ",")


CREDIT_PACKS = {
    "cv1": CreditPack("1 candidatura", 1, 399),
    "cv3": CreditPack("3 candidature", 3, 899),
    "cv10": CreditPack("10 candidature", 10, 1999),
}


class PaymentError(Exception):
    """Errore leggibile dall'utente durante l'avvio del pagamento."""


def payments_enabled() -> bool:
    return bool(config.STRIPE_SECRET_KEY)


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(config.STRIPE_SECRET_KEY)


def create_checkout(user: auth_service.User, pack_id: str, base_url: str | None = None) -> tuple[str, str]:
    """Crea una Checkout Session per il pacchetto; restituisce (id sessione, URL di pagamento).

    `base_url` è l'indirizzo dell'app a cui Stripe riporta l'utente (di norma quello della pagina
    aperta); se manca si usa APP_BASE_URL.
    """
    pack = CREDIT_PACKS[pack_id]
    base_url = (base_url or config.APP_BASE_URL).rstrip("/")
    try:
        session = _client().v1.checkout.sessions.create(params={
            "mode": "payment",
            "line_items": [{
                "quantity": 1,
                "price_data": {
                    "currency": config.STRIPE_CURRENCY,
                    "unit_amount": pack.amount_cents,
                    "product_data": {
                        "name": f"{config.APP_TITLE} · {pack.label}",
                        "description": f"{pack.credits} crediti: 1 credito = 1 candidatura completa "
                                       "(CV, annuncio e LinkedIn)",
                    },
                },
            }],
            "customer_email": user.email,
            "client_reference_id": str(user.id),
            "metadata": {"user_id": str(user.id), "pack_id": pack_id},
            "success_url": f"{base_url}/?payment=success",
            "cancel_url": f"{base_url}/?payment=cancel",
        })
    except stripe.StripeError as exc:
        log.exception("Creazione Checkout Session fallita")
        raise PaymentError(f"Impossibile avviare il pagamento: {exc.user_message or exc}") from exc
    return session.id, session.url


def sync_checkout(session_id: str) -> tuple[str, int]:
    """Chiede a Stripe lo stato della sessione ed eroga i crediti se è pagata.

    Alternativa al webhook (o sua rete di sicurezza): l'erogazione è idempotente, quindi
    webhook e controllo diretto possono convivere senza accreditare due volte.
    Restituisce (stato della sessione: open | complete | expired, crediti accreditati ora).
    """
    try:
        session = _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError:
        log.exception("Lettura della sessione %s fallita", session_id)
        return "open", 0
    return session.status, fulfill_checkout(session) if session.status == "complete" else 0


def fulfill_checkout(session) -> int:
    """Eroga i crediti di una Checkout Session completata. Restituisce i crediti accreditati (0 se nessuno)."""
    if session.payment_status != "paid":
        # Pagamenti asincroni (es. bonifico): si erogano con checkout.session.async_payment_succeeded.
        log.info("Sessione %s non ancora pagata (%s)", session.id, session.payment_status)
        return 0
    metadata = session.metadata.to_dict() if session.metadata else {}
    pack = CREDIT_PACKS.get(metadata.get("pack_id", ""))
    # Prezzo e valuta si confrontano con il listino: i crediti dipendono da ciò che è stato davvero pagato.
    if pack is None or session.amount_total != pack.amount_cents or session.currency != config.STRIPE_CURRENCY:
        log.error("Sessione %s non corrisponde a nessun pacchetto: %s %s %s",
                  session.id, metadata, session.amount_total, session.currency)
        return 0
    user_id = int(metadata["user_id"])
    if not auth_service.record_purchase(session.id, user_id, pack.credits, session.amount_total, session.currency):
        log.info("Sessione %s già erogata", session.id)
        return 0
    log.info("Accreditati %s crediti all'utente %s (sessione %s)", pack.credits, user_id, session.id)
    return pack.credits
