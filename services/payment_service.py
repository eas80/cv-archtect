"""Acquisto di crediti con Stripe Checkout.

L'app crea la sessione di pagamento; i crediti li eroga solo il webhook
(webhook_server.py), dopo che Stripe ha confermato il pagamento.
"""

import json
import logging
import threading
import time
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
        return format_euro(self.amount_cents)

    @property
    def unit_cents(self) -> int:
        """Prezzo a candidatura, arrotondato per difetto (10 a 19,99 € → 1,99 €)."""
        return self.amount_cents // self.credits


def format_euro(cents: int) -> str:
    return f"{cents / 100:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# Listino: l'admin lo cambia dal pannello (tabella settings); senza impostazioni vale questo.
DEFAULT_PACKS = ((1, 399), (3, 899), (10, 1999))
MIN_AMOUNT_CENTS = 50  # minimo accettato da Stripe per un pagamento in euro
MAX_PACKS = 6
_PACKS_SETTING = "credit_packs"
# Il listino si rilegge dal database al più ogni tanti secondi (lo usano anche le pagine pubbliche).
_PACKS_CACHE_SECONDS = 30
_packs_cache: "tuple[float, dict[str, CreditPack]] | None" = None
_packs_lock = threading.Lock()


def _build_packs(rows) -> dict[str, CreditPack]:
    return {
        f"cv{credits}": CreditPack(f"{credits} candidatur{'a' if credits == 1 else 'e'}", credits, cents)
        for credits, cents in sorted(rows)
    }


def validate_packs(rows) -> list[tuple[int, int]]:
    """Controlla il listino [(candidature, centesimi), ...]; solleva ValueError con un messaggio leggibile."""
    rows = [(int(c), int(a)) for c, a in rows]
    if not rows:
        raise ValueError("Serve almeno un pacchetto.")
    if len(rows) > MAX_PACKS:
        raise ValueError(f"Al massimo {MAX_PACKS} pacchetti.")
    credits = [c for c, _ in rows]
    if len(set(credits)) != len(credits):
        raise ValueError("Due pacchetti hanno lo stesso numero di candidature.")
    for c, a in rows:
        if not 1 <= c <= 1000:
            raise ValueError("Le candidature di un pacchetto vanno da 1 a 1000.")
        if not MIN_AMOUNT_CENTS <= a <= 100_000:
            raise ValueError(f"Il prezzo di un pacchetto va da {format_euro(MIN_AMOUNT_CENTS)} a 1.000 €.")
    return sorted(rows)


def credit_packs() -> dict[str, CreditPack]:
    """Listino attuale, per id (cv1, cv3, ...), dal pacchetto più piccolo al più grande."""
    global _packs_cache
    with _packs_lock:
        if _packs_cache and time.monotonic() - _packs_cache[0] < _PACKS_CACHE_SECONDS:
            return _packs_cache[1]
        rows = DEFAULT_PACKS
        try:
            if raw := auth_service.get_setting(_PACKS_SETTING):
                rows = validate_packs(json.loads(raw))
        except Exception:
            log.exception("Listino non leggibile: uso quello predefinito")
        _packs_cache = (time.monotonic(), _build_packs(rows))
        return _packs_cache[1]


def save_credit_packs(rows) -> None:
    global _packs_cache
    rows = validate_packs(rows)
    auth_service.set_setting(_PACKS_SETTING, json.dumps(rows))
    with _packs_lock:
        _packs_cache = None


def lowest_unit_cents(packs: dict[str, CreditPack] | None = None) -> int:
    """Il prezzo più basso a candidatura del listino: quello da mostrare come «da ... €»."""
    return min(p.unit_cents for p in (packs or credit_packs()).values())


class PaymentError(Exception):
    """Errore leggibile dall'utente durante l'avvio del pagamento."""


def payments_enabled() -> bool:
    return bool(config.STRIPE_SECRET_KEY)


def stripe_mode_label() -> str:
    if not payments_enabled():
        return "non configurato"
    return "test (pagamenti di prova)" if config.STRIPE_TEST_MODE else "live (pagamenti veri)"


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(config.STRIPE_SECRET_KEY)


def _customer_id(client: stripe.StripeClient, user: auth_service.User, renew: bool = False) -> str:
    """Cliente Stripe dell'utente, con nome, cognome ed email: si crea al primo acquisto e poi si riusa."""
    live = not config.STRIPE_TEST_MODE
    if not renew and (customer_id := auth_service.get_stripe_customer(user.id, live)):
        return customer_id
    customer = client.v1.customers.create(params={
        "email": user.email,
        "name": user.full_name,
        "preferred_locales": ["it"],
        "metadata": {"user_id": str(user.id)},
    })
    auth_service.set_stripe_customer(user.id, live, customer.id)
    return customer.id


def create_checkout(user: auth_service.User, pack_id: str, base_url: str | None = None,
                    withdrawal_waiver_at: str | None = None) -> tuple[str, str]:
    """Crea una Checkout Session per il pacchetto; restituisce (id sessione, URL di pagamento).

    `base_url` è l'indirizzo dell'app a cui Stripe riporta l'utente (di norma quello della pagina
    aperta); se manca si usa APP_BASE_URL. `withdrawal_waiver_at` è il momento (ISO 8601) in cui
    l'utente ha accettato di perdere il diritto di recesso: resta nei metadati del pagamento come prova.
    """
    pack = credit_packs()[pack_id]
    base_url = (base_url or config.APP_BASE_URL).rstrip("/")
    client = _client()
    params = {
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
        "client_reference_id": str(user.id),
        # Candidature e prezzo restano nel pagamento: se intanto il listino cambia, si eroga
        # comunque ciò che l'utente ha comprato.
        "metadata": {"user_id": str(user.id), "pack_id": pack_id, "customer_name": user.full_name,
                     "credits": str(pack.credits), "amount_cents": str(pack.amount_cents),
                     **({"withdrawal_waiver_at": withdrawal_waiver_at} if withdrawal_waiver_at else {})},
        "success_url": f"{base_url}/?payment=success",
        "cancel_url": f"{base_url}/?payment=cancel",
    }
    try:
        try:
            session = client.v1.checkout.sessions.create(
                params={**params, "customer": _customer_id(client, user)})
        except stripe.InvalidRequestError as exc:
            # Cliente eliminato dalla dashboard di Stripe: se ne crea uno nuovo.
            if exc.code != "resource_missing" or exc.param != "customer":
                raise
            session = client.v1.checkout.sessions.create(
                params={**params, "customer": _customer_id(client, user, renew=True)})
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
    # Un pagamento di prova non dà crediti in produzione (e uno vero non finisce nell'ambiente di prova),
    # anche se un webhook o una chiave fossero configurati per errore.
    if bool(session.livemode) == config.STRIPE_TEST_MODE:
        log.error("Sessione %s ignorata: livemode=%s ma l'app usa chiavi %s",
                  session.id, session.livemode, "test" if config.STRIPE_TEST_MODE else "live")
        return 0
    metadata = session.metadata.to_dict() if session.metadata else {}
    if "credits" in metadata:
        # I metadati li scrive solo l'app (con la chiave segreta), al momento dell'acquisto.
        credits, amount = int(metadata["credits"]), int(metadata["amount_cents"])
    elif pack := credit_packs().get(metadata.get("pack_id", "")):
        # Pagamenti avviati prima che il listino fosse modificabile.
        credits, amount = pack.credits, pack.amount_cents
    else:
        credits, amount = 0, None
    # Prezzo e valuta si confrontano con quanto è stato davvero pagato.
    if credits <= 0 or session.amount_total != amount or session.currency != config.STRIPE_CURRENCY:
        log.error("Sessione %s non corrisponde a nessun pacchetto: %s %s %s",
                  session.id, metadata, session.amount_total, session.currency)
        return 0
    user_id = int(metadata["user_id"])
    if not auth_service.record_purchase(session.id, user_id, credits, session.amount_total, session.currency,
                                        livemode=bool(session.livemode)):
        log.info("Sessione %s già erogata", session.id)
        return 0
    log.info("Accreditati %s crediti all'utente %s (sessione %s)", credits, user_id, session.id)
    return credits
