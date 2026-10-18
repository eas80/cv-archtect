"""Utenti, accessi e crediti su Postgres (Supabase).

Le password sono salvate solo come hash PBKDF2 con sale casuale.
L'indirizzo email si conferma con un codice a 6 cifre inviato alla registrazione; lo stesso
meccanismo (tabella email_codes, codici salvati solo come hash) serve per il reset della password.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from psycopg import errors

import config
from services import db, email_service

_PBKDF2_ITERATIONS = 600_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

# Data di creazione già nel fuso dell'app, come testo «AAAA-MM-GG HH:MM:SS».
_LOCAL_CREATED_AT = "to_char(created_at AT TIME ZONE %(tz)s, 'YYYY-MM-DD HH24:MI:SS') AS created_at"
_USER_COLUMNS = f"id, email, credits, is_admin, can_save_applications, email_verified, {_LOCAL_CREATED_AT}"


class AuthError(Exception):
    """Errore leggibile dall'utente (credenziali errate, email già usata, ...)."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    credits: int
    is_admin: bool
    created_at: str
    # Funzione «Le mie candidature», attivata per singolo utente dal pannello admin (gli admin l'hanno sempre).
    can_save_applications: bool
    email_verified: bool

    @property
    def has_applications_archive(self) -> bool:
        return self.is_admin or self.can_save_applications


def _hash_secret(secret: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def _verify_secret(secret: str, stored: str | None) -> bool:
    try:
        _, iterations, salt, expected = (stored or "").split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


def _new_email_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError("Indirizzo email non valido.")
    return email


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"La password deve avere almeno {MIN_PASSWORD_LENGTH} caratteri.")


def _to_user(row: dict | None) -> User | None:
    if row is None:
        return None
    return User(row["id"], row["email"], row["credits"], row["is_admin"], row["created_at"],
                row["can_save_applications"], row["email_verified"])


def _select_user(where: str, params: dict) -> User | None:
    with db.transaction() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE {where}",
                           {"tz": config.APP_TIMEZONE, **params}).fetchone()
        # Il saldo letto tiene già conto dei lotti scaduti.
        if row and row["credits"] and conn.execute(_EXPIRE_DUE_LOTS, {"uid": row["id"]}).rowcount:
            row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %(id)s",
                               {"tz": config.APP_TIMEZONE, "id": row["id"]}).fetchone()
    return _to_user(row)


# --- Lotti di crediti -----------------------------------------------------------------
# Azzera i lotti scaduti (di un utente, o di tutti con uid NULL) e li toglie dal saldo.
# rowcount = numero di utenti il cui saldo è cambiato.
_EXPIRE_DUE_LOTS = """
WITH due AS (
    SELECT id, user_id, credits_left FROM credit_lots
    WHERE credits_left > 0 AND expires_at <= now()
      AND (%(uid)s::bigint IS NULL OR user_id = %(uid)s::bigint)
    FOR UPDATE
), cleared AS (
    UPDATE credit_lots l SET expired_credits = l.expired_credits + due.credits_left, credits_left = 0
    FROM due WHERE l.id = due.id
)
UPDATE users u SET credits = GREATEST(u.credits - t.n, 0)
FROM (SELECT user_id, SUM(credits_left) AS n FROM due GROUP BY user_id) t
WHERE u.id = t.user_id
"""


def _add_lot(conn, user_id: int, credits: int, source: str, stripe_session_id: str | None = None) -> None:
    """Nuovo lotto con scadenza a CREDITS_VALIDITY_MONTHS mesi, aggiunto al saldo."""
    conn.execute(
        "INSERT INTO credit_lots (user_id, source, stripe_session_id, credits, credits_left, expires_at) "
        "VALUES (%(uid)s, %(source)s, %(sid)s, %(n)s, %(n)s, now() + make_interval(months => %(months)s))",
        {"uid": user_id, "source": source, "sid": stripe_session_id, "n": credits,
         "months": config.CREDITS_VALIDITY_MONTHS},
    )
    conn.execute("UPDATE users SET credits = credits + %s WHERE id = %s", (credits, user_id))


def _take_credits(conn, user_id: int, amount: int) -> bool:
    """Toglie `amount` crediti partendo dai lotti che scadono prima. False (e nulla cambia) se non bastano."""
    conn.execute(_EXPIRE_DUE_LOTS, {"uid": user_id})
    lots = conn.execute(
        "SELECT id, credits_left FROM credit_lots WHERE user_id = %s AND credits_left > 0 "
        "ORDER BY expires_at, id FOR UPDATE", (user_id,),
    ).fetchall()
    if sum(lot["credits_left"] for lot in lots) < amount:
        return False
    left = amount
    for lot in lots:
        take = min(left, lot["credits_left"])
        conn.execute("UPDATE credit_lots SET credits_left = credits_left - %s WHERE id = %s", (take, lot["id"]))
        left -= take
        if not left:
            break
    conn.execute("UPDATE users SET credits = credits - %s WHERE id = %s", (amount, user_id))
    return True


def credit_expiries(user_id: int) -> list[dict]:
    """Crediti ancora validi raggruppati per giorno di scadenza (data «GG/MM/AAAA» nel fuso dell'app)."""
    with db.transaction() as conn:
        conn.execute(_EXPIRE_DUE_LOTS, {"uid": user_id})
        return conn.execute(
            "SELECT to_char(expires_at AT TIME ZONE %(tz)s, 'DD/MM/YYYY') AS expires_on, SUM(credits_left) AS credits "
            "FROM credit_lots WHERE user_id = %(uid)s AND credits_left > 0 "
            "GROUP BY 1, (expires_at AT TIME ZONE %(tz)s)::date ORDER BY (expires_at AT TIME ZONE %(tz)s)::date",
            {"tz": config.APP_TIMEZONE, "uid": user_id},
        ).fetchall()


def expire_all_credits() -> None:
    with db.transaction() as conn:
        conn.execute(_EXPIRE_DUE_LOTS, {"uid": None})


def init_db() -> None:
    """Crea o aggiorna le tabelle (una volta per processo) e marca come admin gli account in ADMIN_EMAILS."""
    db.ensure_schema()


def get_user(user_id: int) -> User | None:
    return _select_user("id = %(id)s", {"id": user_id})


def register(email: str, password: str, terms_version: str) -> User:
    """Crea l'utente con email da confermare (poi va chiamato send_verification_code).

    `terms_version` è la versione di Termini e Informativa privacy accettata: si registra con data e ora.
    """
    email = _normalize_email(email)
    _check_password(password)
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "INSERT INTO users (email, password_hash, credits, is_admin, terms_accepted_at, terms_version) "
                f"VALUES (%(email)s, %(pw)s, 0, %(admin)s, now(), %(terms)s) RETURNING {_USER_COLUMNS}",
                {"email": email, "pw": _hash_secret(password),
                 "admin": email in config.ADMIN_EMAILS, "terms": terms_version, "tz": config.APP_TIMEZONE},
            ).fetchone()
            if config.INITIAL_CREDITS:
                _add_lot(conn, row["id"], config.INITIAL_CREDITS, "initial")
                row = {**row, "credits": config.INITIAL_CREDITS}
    except errors.UniqueViolation as exc:
        raise AuthError("Esiste già un account con questa email. Se è tuo e non ricordi la password, "
                        "usa «Password dimenticata».") from exc
    return _to_user(row)


def login(email: str, password: str) -> User:
    with db.transaction() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = %s", (email.strip().lower(),)).fetchone()
    if row is None or not _verify_secret(password, row["password_hash"]):
        raise AuthError("Email o password non corretti.")
    return get_user(row["id"])


# --- Codici via email -----------------------------------------------------------------
_CODE_EMAILS = {
    "verify": ("Conferma il tuo indirizzo email",
               "Benvenuto in {app}! Inserisci questo codice nell'app per confermare il tuo indirizzo email."),
    "reset": ("Reimposta la password",
              "Hai chiesto di reimpostare la password di {app}. Inserisci questo codice nell'app."),
}


def _issue_code(user_id: int, email: str, purpose: str) -> None:
    """Genera un nuovo codice (sostituisce il precedente) e lo invia per email."""
    code = _new_email_code()
    with db.transaction() as conn:
        recent = conn.execute(
            "SELECT 1 FROM email_codes WHERE user_id = %s AND purpose = %s AND created_at > now() - %s",
            (user_id, purpose, timedelta(seconds=config.EMAIL_CODE_RESEND_SECONDS)),
        ).fetchone()
        if recent:
            raise AuthError("Ti abbiamo appena inviato un codice: attendi un minuto prima di chiederne un altro.")
        conn.execute(
            "INSERT INTO email_codes (user_id, purpose, code_hash, expires_at) VALUES (%s, %s, %s, now() + %s) "
            "ON CONFLICT (user_id, purpose) DO UPDATE SET code_hash = excluded.code_hash, attempts = 0, "
            "created_at = now(), expires_at = excluded.expires_at",
            (user_id, purpose, _hash_secret(code), timedelta(minutes=config.EMAIL_CODE_MINUTES)),
        )
    subject, intro = _CODE_EMAILS[purpose]
    try:
        email_service.send_code(email, subject, intro.format(app=config.APP_TITLE), code)
    except email_service.EmailError:
        # Codice mai arrivato: lo si toglie, così se ne può chiedere subito un altro.
        with db.transaction() as conn:
            conn.execute("DELETE FROM email_codes WHERE user_id = %s AND purpose = %s", (user_id, purpose))
        raise


def _check_code(user_id: int, purpose: str, code: str) -> None:
    """Verifica il codice; AuthError se errato, scaduto o dopo troppi tentativi."""
    # Il tentativo si conta in una transazione a sé, che resta salvata anche se il codice è errato.
    with db.transaction() as conn:
        row = conn.execute(
            "UPDATE email_codes SET attempts = attempts + 1 WHERE user_id = %s AND purpose = %s "
            "RETURNING code_hash, attempts, expires_at > now() AS valid",
            (user_id, purpose),
        ).fetchone()
    if row is None or not row["valid"]:
        raise AuthError("Codice scaduto o non richiesto: chiedine uno nuovo.")
    if row["attempts"] > config.EMAIL_CODE_MAX_ATTEMPTS:
        raise AuthError("Troppi tentativi con questo codice: chiedine uno nuovo.")
    if not _verify_secret(code.strip().replace(" ", ""), row["code_hash"]):
        raise AuthError("Codice non corretto.")


def send_verification_code(user_id: int) -> None:
    user = get_user(user_id)
    if user is None or user.email_verified:
        return
    _issue_code(user.id, user.email, "verify")


def verify_email(user_id: int, code: str) -> None:
    _check_code(user_id, "verify", code)
    with db.transaction() as conn:
        conn.execute("UPDATE users SET email_verified = TRUE WHERE id = %s", (user_id,))
        conn.execute("DELETE FROM email_codes WHERE user_id = %s AND purpose = 'verify'", (user_id,))


def request_password_reset(email: str) -> None:
    """Invia il codice di reset se l'account esiste; altrimenti non fa nulla (non rivela chi è registrato)."""
    email = _normalize_email(email)
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    if row is not None:
        _issue_code(row["id"], email, "reset")


def reset_password(email: str, code: str, new_password: str) -> User:
    """Imposta la nuova password; il codice ricevuto per email conferma anche l'indirizzo."""
    _check_password(new_password)
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s", (email.strip().lower(),)).fetchone()
    if row is None:
        raise AuthError("Codice scaduto o non richiesto: chiedine uno nuovo.")
    _check_code(row["id"], "reset", code)
    with db.transaction() as conn:
        conn.execute("UPDATE users SET password_hash = %s, email_verified = TRUE WHERE id = %s",
                     (_hash_secret(new_password), row["id"]))
        conn.execute("DELETE FROM email_codes WHERE user_id = %s", (row["id"],))
        # Nuova password: gli accessi ricordati sugli altri dispositivi non valgono più.
        conn.execute("DELETE FROM login_sessions WHERE user_id = %s", (row["id"],))
    return get_user(row["id"])


def _token_hash(token: str) -> str:
    # Il token è casuale a 256 bit: basta un hash veloce, il sale servirebbe solo per segreti deboli.
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_session(user_id: int) -> str:
    """Crea un accesso ricordato e restituisce il token da salvare nel cookie del browser."""
    token = secrets.token_urlsafe(32)
    with db.transaction() as conn:
        conn.execute("DELETE FROM login_sessions WHERE expires_at <= now()")
        conn.execute(
            "INSERT INTO login_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s)",
            (_token_hash(token), user_id, timedelta(days=config.SESSION_DAYS)),
        )
    return token


def user_from_login_session(token: str | None) -> User | None:
    if not token or not isinstance(token, str):
        return None
    return _select_user(
        "id = (SELECT user_id FROM login_sessions WHERE token_hash = %(token)s AND expires_at > now())",
        {"token": _token_hash(token)},
    )


def delete_login_session(token: str | None) -> None:
    if token:
        with db.transaction() as conn:
            conn.execute("DELETE FROM login_sessions WHERE token_hash = %s", (_token_hash(token),))


def consume_credit(user_id: int) -> bool:
    """Scala un credito; True se riuscito. Gli admin non consumano crediti."""
    with db.transaction() as conn:
        # Riga dell'utente bloccata: due richieste concorrenti non scalano lo stesso credito.
        row = conn.execute("SELECT is_admin FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if row is None:
            return False
        return row["is_admin"] or _take_credits(conn, user_id, 1)


def record_purchase(stripe_session_id: str, user_id: int, credits: int, amount_total: int, currency: str,
                    livemode: bool) -> bool:
    """Registra l'acquisto e accredita i crediti. False se la sessione era già stata erogata."""
    with db.transaction() as conn:
        # ON CONFLICT invece di intercettare l'errore: la transazione resta valida per il resto.
        inserted = conn.execute(
            "INSERT INTO payments (stripe_session_id, user_id, credits, amount_total, currency, livemode) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (stripe_session_id) DO NOTHING",
            (stripe_session_id, user_id, credits, amount_total, currency, livemode),
        ).rowcount
        if not inserted:
            return False
        if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
            raise AuthError(f"Utente {user_id} inesistente.")
        _add_lot(conn, user_id, credits, "purchase", stripe_session_id)
    return True


FLAG_FREE_MANUAL_MODE = "free_manual_mode"


def get_setting(key: str) -> str | None:
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_flag(key: str) -> bool:
    return get_setting(key) == "1"


def set_flag(key: str, enabled: bool) -> None:
    set_setting(key, "1" if enabled else "0")


def log_usage(user_id: int, action: str) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO usage_log (user_id, action) VALUES (%s, %s)", (user_id, action))


def ai_calls_last_day(user_id: int) -> int:
    """Operazioni con Claude dell'utente nelle ultime 24 ore (per il tetto giornaliero)."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM usage_log WHERE user_id = %s AND created_at > now() - interval '1 day'",
            (user_id,),
        ).fetchone()
    return row["n"]


def add_credits(email: str, delta: int) -> int:
    """Aggiunge (o toglie, se negativo) crediti all'utente; restituisce il nuovo saldo."""
    email = email.strip().lower()
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s FOR UPDATE", (email,)).fetchone()
        if row is None:
            raise AuthError(f"Nessun utente con email {email}.")
        if delta > 0:
            _add_lot(conn, row["id"], delta, "admin")
        elif delta < 0 and not _take_credits(conn, row["id"], -delta):
            balance = conn.execute("SELECT credits FROM users WHERE id = %s", (row["id"],)).fetchone()["credits"]
            raise AuthError(f"{email} ha solo {balance} crediti: non se ne possono togliere {-delta}.")
        return conn.execute("SELECT credits FROM users WHERE id = %s", (row["id"],)).fetchone()["credits"]


def users_overview() -> list[dict]:
    """Utenti con saldo, operazioni con Claude e acquisti, per il pannello admin."""
    with db.transaction() as conn:
        conn.execute(_EXPIRE_DUE_LOTS, {"uid": None})
        return conn.execute("""
            SELECT u.email, u.credits, u.is_admin, u.can_save_applications, u.email_verified,
                   to_char(u.created_at AT TIME ZONE %(tz)s, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                   (SELECT COUNT(*) FROM usage_log l WHERE l.user_id = u.id) AS claude_calls,
                   (SELECT COUNT(*) FROM payments p WHERE p.user_id = u.id AND p.livemode = %(live)s) AS purchases,
                   (SELECT COALESCE(SUM(amount_total), 0) FROM payments p
                    WHERE p.user_id = u.id AND p.livemode = %(live)s) AS spent_cents
            FROM users u ORDER BY u.id
        """, {"tz": config.APP_TIMEZONE, "live": not config.STRIPE_TEST_MODE}).fetchall()


def usage_stats() -> dict:
    """Totale operazioni con Claude, quelle di oggi e il dettaglio per tipo."""
    with db.transaction() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE (created_at AT TIME ZONE %(tz)s)::date = "
            "(now() AT TIME ZONE %(tz)s)::date) AS today FROM usage_log",
            {"tz": config.APP_TIMEZONE},
        ).fetchone()
        by_action = {r["action"]: r["n"] for r in conn.execute(
            "SELECT action, COUNT(*) AS n FROM usage_log GROUP BY action ORDER BY n DESC"
        )}
    return {"total": totals["total"], "today": totals["today"], "by_action": by_action}


def sales_stats() -> dict:
    """Vendite complessive: numero di acquisti, incasso in centesimi e crediti venduti.

    Contano solo i pagamenti della modalità Stripe attiva: in produzione le prove fatte con le
    chiavi di test non risultano come incassi.
    """
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(amount_total), 0) AS revenue_cents, "
            "COALESCE(SUM(credits), 0) AS credits FROM payments WHERE livemode = %s",
            (not config.STRIPE_TEST_MODE,),
        ).fetchone()
    return {"count": row["count"], "revenue_cents": int(row["revenue_cents"]), "credits": int(row["credits"])}


def _update_user_field(email: str, column: str, value) -> None:
    with db.transaction() as conn:
        if not conn.execute(f"UPDATE users SET {column} = %s WHERE email = %s", (value, email.strip().lower())).rowcount:
            raise AuthError(f"Nessun utente con email {email}.")


def set_credits(email: str, credits: int) -> None:
    """Porta il saldo a `credits`, aggiungendo un lotto o togliendo crediti dai lotti."""
    user = _select_user("email = %(email)s", {"email": email.strip().lower()})
    if user is None:
        raise AuthError(f"Nessun utente con email {email}.")
    if credits != user.credits:
        add_credits(email, credits - user.credits)


def set_can_save_applications(email: str, enabled: bool) -> None:
    _update_user_field(email, "can_save_applications", enabled)


def set_admin(email: str, is_admin: bool) -> None:
    _update_user_field(email, "is_admin", is_admin)


def set_email_verified(email: str, verified: bool) -> None:
    _update_user_field(email, "email_verified", verified)


def set_password(email: str, new_password: str) -> None:
    """Password impostata dall'admin: chiude gli accessi aperti dell'utente su tutti i dispositivi."""
    _check_password(new_password)
    with db.transaction() as conn:
        row = conn.execute("UPDATE users SET password_hash = %s WHERE email = %s RETURNING id",
                           (_hash_secret(new_password), email.strip().lower())).fetchone()
        if row is None:
            raise AuthError(f"Nessun utente con email {email}.")
        conn.execute("DELETE FROM login_sessions WHERE user_id = %s", (row["id"],))
        conn.execute("DELETE FROM email_codes WHERE user_id = %s AND purpose = 'reset'", (row["id"],))


def delete_user(email: str) -> None:
    """Elimina l'account con accessi, codici e candidature salvate.

    Pagamenti e statistiche di utilizzo restano, senza più l'utente collegato (ON DELETE SET NULL).
    """
    with db.transaction() as conn:
        if not conn.execute("DELETE FROM users WHERE email = %s", (email.strip().lower(),)).rowcount:
            raise AuthError(f"Nessun utente con email {email}.")


def delete_own_account(user_id: int, password: str) -> None:
    """Eliminazione richiesta dall'utente stesso, confermata con la password (diritto all'oblio)."""
    with db.transaction() as conn:
        row = conn.execute("SELECT email, password_hash FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None or not _verify_secret(password, row["password_hash"]):
        raise AuthError("Password non corretta.")
    delete_user(row["email"])


def list_users() -> list[User]:
    with db.transaction() as conn:
        conn.execute(_EXPIRE_DUE_LOTS, {"uid": None})
        rows = conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY id", {"tz": config.APP_TIMEZONE}).fetchall()
    return [_to_user(r) for r in rows]
