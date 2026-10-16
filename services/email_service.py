"""Invio delle email transazionali (codici di conferma e di reset) con l'API HTTP di Brevo.

Si usa l'API e non SMTP perché Render, nel piano Free, blocca le porte SMTP in uscita.
Senza BREVO_API_KEY (es. in locale) l'email non parte e il suo contenuto finisce nel log,
così i codici restano utilizzabili durante le prove.
"""

import html
import logging

import requests

import config

log = logging.getLogger(__name__)

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class EmailError(Exception):
    """Errore leggibile dall'utente durante l'invio di un'email."""


def email_enabled() -> bool:
    return bool(config.BREVO_API_KEY and config.EMAIL_SENDER)


def send_code(to: str, subject: str, intro: str, code: str) -> None:
    """Invia un codice a 6 cifre con una frase introduttiva."""
    minutes = config.EMAIL_CODE_MINUTES
    text = (f"{intro}\n\nIl tuo codice: {code}\n\nScade tra {minutes} minuti. "
            "Se non hai richiesto tu questo codice, ignora questa email.\n\n— " + config.APP_TITLE)
    body = f"""\
<div style="font-family:Arial,sans-serif;font-size:15px;color:#111827;max-width:480px">
  <p>{html.escape(intro)}</p>
  <p style="font-size:30px;font-weight:bold;letter-spacing:6px;color:#2563EB;margin:24px 0">{code}</p>
  <p style="color:#4B5563">Scade tra {minutes} minuti. Se non hai richiesto tu questo codice, ignora questa email.</p>
  <p style="color:#4B5563">— {html.escape(config.APP_TITLE)}</p>
</div>"""
    _send(to, subject, body, text)


def _send(to: str, subject: str, html_body: str, text_body: str) -> None:
    if not email_enabled():
        log.warning("Email non configurata (BREVO_API_KEY/EMAIL_SENDER): non inviata a %s.\n%s\n%s",
                    to, subject, text_body)
        return
    try:
        response = requests.post(
            _BREVO_URL,
            headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"},
            json={
                "sender": {"email": config.EMAIL_SENDER, "name": config.EMAIL_SENDER_NAME},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        log.error("Invio email a %s fallito: %s", to, exc)
        raise EmailError("Non è stato possibile inviare l'email. Riprova tra qualche istante.") from exc
    if response.status_code >= 400:
        log.error("Brevo ha rifiutato l'email a %s: %s %s", to, response.status_code, response.text[:500])
        raise EmailError("Non è stato possibile inviare l'email. Riprova tra qualche istante.")
