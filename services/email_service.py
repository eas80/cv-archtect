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


def send_contact_message(name: str, reply_to: str, topic: str, message: str, account: str | None) -> None:
    """Inoltra il messaggio del modulo «Contattaci»: «Rispondi» nella casella va all'utente."""
    account_line = f"Account: {account}" if account else "Account: non ha fatto l'accesso"
    text = f"Da: {name} <{reply_to}>\n{account_line}\nArgomento: {topic}\n\n{message}"
    body = f"""\
<div style="font-family:Arial,sans-serif;font-size:15px;color:#111827;max-width:640px">
  <p style="color:#4B5563">Da: <b>{html.escape(name)}</b> &lt;{html.escape(reply_to)}&gt;<br>
     {html.escape(account_line)}<br>Argomento: {html.escape(topic)}</p>
  <p style="white-space:pre-wrap">{html.escape(message)}</p>
</div>"""
    _send(config.CONTACT_EMAIL, f"[{config.APP_TITLE}] {topic} — {name}", body, text,
          reply_to={"email": reply_to, "name": name})


def _send(to: str, subject: str, html_body: str, text_body: str, reply_to: dict | None = None) -> None:
    if not email_enabled():
        log.warning("Email non configurata (BREVO_API_KEY/EMAIL_SENDER): non inviata a %s.\n%s\n%s",
                    to, subject, text_body)
        return
    payload = {
        "sender": {"email": config.EMAIL_SENDER, "name": config.EMAIL_SENDER_NAME},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }
    if reply_to:
        payload["replyTo"] = reply_to
    try:
        response = requests.post(
            _BREVO_URL,
            headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"},
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        log.error("Invio email a %s fallito: %s", to, exc)
        raise EmailError("Non è stato possibile inviare l'email. Riprova tra qualche istante.") from exc
    if response.status_code >= 400:
        log.error("Brevo ha rifiutato l'email a %s: %s %s", to, response.status_code, response.text[:500])
        raise EmailError("Non è stato possibile inviare l'email. Riprova tra qualche istante.")
