"""Utenti, accessi e crediti su Postgres (Supabase).

Password e codici di recupero sono salvati solo come hash PBKDF2 con sale casuale.
Non essendoci un server di posta, il reset della password usa un codice di recupero
mostrato una sola volta alla registrazione (e rigenerato a ogni reset).
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from psycopg import errors

import config
from services import db

_PBKDF2_ITERATIONS = 600_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

# Data di creazione già nel fuso dell'app, come testo «AAAA-MM-GG HH:MM:SS».
_LOCAL_CREATED_AT = "to_char(created_at AT TIME ZONE %(tz)s, 'YYYY-MM-DD HH24:MI:SS') AS created_at"
_USER_COLUMNS = f"id, email, credits, is_admin, can_save_applications, {_LOCAL_CREATED_AT}"


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


def _new_recovery_code() -> str:
    raw = secrets.token_hex(6).upper()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


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
                row["can_save_applications"])


def _select_user(where: str, params: dict) -> User | None:
    with db.transaction() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE {where}",
                           {"tz": config.APP_TIMEZONE, **params}).fetchone()
    return _to_user(row)


def init_db() -> None:
    """Crea o aggiorna le tabelle (una volta per processo) e marca come admin gli account in ADMIN_EMAILS."""
    db.ensure_schema()


def get_user(user_id: int) -> User | None:
    return _select_user("id = %(id)s", {"id": user_id})


def register(email: str, password: str) -> tuple[User, str]:
    """Crea l'utente e restituisce anche il codice di recupero da mostrargli una volta."""
    email = _normalize_email(email)
    _check_password(password)
    recovery_code = _new_recovery_code()
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "INSERT INTO users (email, password_hash, credits, is_admin, recovery_hash) "
                f"VALUES (%(email)s, %(pw)s, %(credits)s, %(admin)s, %(rec)s) RETURNING {_USER_COLUMNS}",
                {"email": email, "pw": _hash_secret(password), "credits": config.INITIAL_CREDITS,
                 "admin": email in config.ADMIN_EMAILS, "rec": _hash_secret(recovery_code), "tz": config.APP_TIMEZONE},
            ).fetchone()
    except errors.UniqueViolation as exc:
        raise AuthError("Esiste già un account con questa email.") from exc
    return _to_user(row), recovery_code


def login(email: str, password: str) -> User:
    with db.transaction() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = %s", (email.strip().lower(),)).fetchone()
    if row is None or not _verify_secret(password, row["password_hash"]):
        raise AuthError("Email o password non corretti.")
    return get_user(row["id"])


def reset_password(email: str, recovery_code: str, new_password: str) -> str:
    """Imposta la nuova password e restituisce il nuovo codice di recupero."""
    _check_password(new_password)
    email = email.strip().lower()
    with db.transaction() as conn:
        row = conn.execute("SELECT id, recovery_hash FROM users WHERE email = %s FOR UPDATE", (email,)).fetchone()
        if row is None or not _verify_secret(recovery_code.strip().upper(), row["recovery_hash"]):
            raise AuthError("Email o codice di recupero non corretti.")
        new_code = _new_recovery_code()
        conn.execute(
            "UPDATE users SET password_hash = %s, recovery_hash = %s WHERE id = %s",
            (_hash_secret(new_password), _hash_secret(new_code), row["id"]),
        )
        # Nuova password: gli accessi ricordati sugli altri dispositivi non valgono più.
        conn.execute("DELETE FROM login_sessions WHERE user_id = %s", (row["id"],))
    return new_code


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
        # Condizione nella stessa UPDATE: due richieste concorrenti non portano i crediti sotto zero.
        if conn.execute("UPDATE users SET credits = credits - 1 WHERE id = %s AND NOT is_admin AND credits > 0",
                        (user_id,)).rowcount:
            return True
        row = conn.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,)).fetchone()
        return bool(row and row["is_admin"])


def record_purchase(stripe_session_id: str, user_id: int, credits: int, amount_total: int, currency: str) -> bool:
    """Registra l'acquisto e accredita i crediti. False se la sessione era già stata erogata."""
    with db.transaction() as conn:
        # ON CONFLICT invece di intercettare l'errore: la transazione resta valida per il resto.
        inserted = conn.execute(
            "INSERT INTO payments (stripe_session_id, user_id, credits, amount_total, currency) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (stripe_session_id) DO NOTHING",
            (stripe_session_id, user_id, credits, amount_total, currency),
        ).rowcount
        if not inserted:
            return False
        if not conn.execute("UPDATE users SET credits = credits + %s WHERE id = %s", (credits, user_id)).rowcount:
            raise AuthError(f"Utente {user_id} inesistente.")
    return True


FLAG_FREE_MANUAL_MODE = "free_manual_mode"


def get_flag(key: str) -> bool:
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return bool(row and row["value"] == "1")


def set_flag(key: str, enabled: bool) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, "1" if enabled else "0"),
        )


def log_usage(user_id: int, action: str) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO usage_log (user_id, action) VALUES (%s, %s)", (user_id, action))


def add_credits(email: str, delta: int) -> int:
    """Aggiunge (o toglie, se negativo) crediti all'utente; restituisce il nuovo saldo."""
    email = email.strip().lower()
    with db.transaction() as conn:
        row = conn.execute("SELECT id, credits FROM users WHERE email = %s FOR UPDATE", (email,)).fetchone()
        if row is None:
            raise AuthError(f"Nessun utente con email {email}.")
        if row["credits"] + delta < 0:
            raise AuthError(f"{email} ha solo {row['credits']} crediti: non se ne possono togliere {-delta}.")
        conn.execute("UPDATE users SET credits = credits + %s WHERE id = %s", (delta, row["id"]))
        return row["credits"] + delta


def users_overview() -> list[dict]:
    """Utenti con saldo, operazioni con Claude e acquisti, per il pannello admin."""
    with db.transaction() as conn:
        return conn.execute("""
            SELECT u.email, u.credits, u.is_admin, u.can_save_applications,
                   to_char(u.created_at AT TIME ZONE %(tz)s, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                   (SELECT COUNT(*) FROM usage_log l WHERE l.user_id = u.id) AS claude_calls,
                   (SELECT COUNT(*) FROM payments p WHERE p.user_id = u.id) AS purchases,
                   (SELECT COALESCE(SUM(amount_total), 0) FROM payments p WHERE p.user_id = u.id) AS spent_cents
            FROM users u ORDER BY u.id
        """, {"tz": config.APP_TIMEZONE}).fetchall()


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
    """Vendite complessive: numero di acquisti, incasso in centesimi e crediti venduti."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(amount_total), 0) AS revenue_cents, "
            "COALESCE(SUM(credits), 0) AS credits FROM payments"
        ).fetchone()
    return {"count": row["count"], "revenue_cents": int(row["revenue_cents"]), "credits": int(row["credits"])}


def _update_user_field(email: str, column: str, value) -> None:
    with db.transaction() as conn:
        if not conn.execute(f"UPDATE users SET {column} = %s WHERE email = %s", (value, email.strip().lower())).rowcount:
            raise AuthError(f"Nessun utente con email {email}.")


def set_credits(email: str, credits: int) -> None:
    _update_user_field(email, "credits", credits)


def set_can_save_applications(email: str, enabled: bool) -> None:
    _update_user_field(email, "can_save_applications", enabled)


def set_admin(email: str, is_admin: bool) -> None:
    _update_user_field(email, "is_admin", is_admin)


def list_users() -> list[User]:
    with db.transaction() as conn:
        rows = conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY id", {"tz": config.APP_TIMEZONE}).fetchall()
    return [_to_user(r) for r in rows]
