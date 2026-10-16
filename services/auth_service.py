"""Utenti e crediti su database SQLite locale.

Password e codici di recupero sono salvati solo come hash PBKDF2 con sale casuale.
Non essendoci un server di posta, il reset della password usa un codice di recupero
mostrato una sola volta alla registrazione (e rigenerato a ogni reset).
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass

import config

_PBKDF2_ITERATIONS = 600_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    credits       INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    recovery_hash TEXT
);
-- Acquisti già erogati: la chiave sulla sessione Stripe rende l'erogazione idempotente
-- (Stripe può inviare lo stesso evento più volte).
CREATE TABLE IF NOT EXISTS payments (
    stripe_session_id TEXT    PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id),
    credits           INTEGER NOT NULL,
    amount_total      INTEGER NOT NULL,
    currency          TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Accessi ricordati dal browser (cookie): si salva solo l'hash del token.
CREATE TABLE IF NOT EXISTS login_sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT    NOT NULL
);
-- Impostazioni modificabili dal pannello admin (valori '0'/'1' per i flag).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Un record per ogni operazione con Claude andata a buon fine (anche degli admin).
CREATE TABLE IF NOT EXISTS usage_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    action     TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def _to_user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(row["id"], row["email"], row["credits"], bool(row["is_admin"]), row["created_at"],
                bool(row["can_save_applications"]))


def init_db() -> None:
    """Crea le tabelle se mancano e marca come admin gli account in ADMIN_EMAILS."""
    with closing(_connect()) as conn, conn:
        conn.executescript(_SCHEMA)
        # Colonne aggiunte dopo la prima versione: i database esistenti vanno aggiornati.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "can_save_applications" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN can_save_applications INTEGER NOT NULL DEFAULT 0")
        if config.ADMIN_EMAILS:
            marks = ",".join("?" * len(config.ADMIN_EMAILS))
            conn.execute(f"UPDATE users SET is_admin = 1 WHERE email IN ({marks})", config.ADMIN_EMAILS)


def get_user(user_id: int) -> User | None:
    with closing(_connect()) as conn:
        return _to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def register(email: str, password: str) -> tuple[User, str]:
    """Crea l'utente e restituisce anche il codice di recupero da mostrargli una volta."""
    email = _normalize_email(email)
    _check_password(password)
    recovery_code = _new_recovery_code()
    try:
        with closing(_connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, credits, is_admin, recovery_hash) VALUES (?, ?, ?, ?, ?)",
                (email, _hash_secret(password), config.INITIAL_CREDITS, email in config.ADMIN_EMAILS,
                 _hash_secret(recovery_code)),
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise AuthError("Esiste già un account con questa email.") from exc
    return get_user(user_id), recovery_code


def login(email: str, password: str) -> User:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if row is None or not _verify_secret(password, row["password_hash"]):
        raise AuthError("Email o password non corretti.")
    return _to_user(row)


def reset_password(email: str, recovery_code: str, new_password: str) -> str:
    """Imposta la nuova password e restituisce il nuovo codice di recupero."""
    _check_password(new_password)
    email = email.strip().lower()
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT id, recovery_hash FROM users WHERE email = ?", (email,)).fetchone()
        if row is None or not _verify_secret(recovery_code.strip().upper(), row["recovery_hash"]):
            raise AuthError("Email o codice di recupero non corretti.")
        new_code = _new_recovery_code()
        conn.execute(
            "UPDATE users SET password_hash = ?, recovery_hash = ? WHERE id = ?",
            (_hash_secret(new_password), _hash_secret(new_code), row["id"]),
        )
        # Nuova password: gli accessi ricordati sugli altri dispositivi non valgono più.
        conn.execute("DELETE FROM login_sessions WHERE user_id = ?", (row["id"],))
    return new_code


def _token_hash(token: str) -> str:
    # Il token è casuale a 256 bit: basta un hash veloce, il sale servirebbe solo per segreti deboli.
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_session(user_id: int) -> str:
    """Crea un accesso ricordato e restituisce il token da salvare nel cookie del browser."""
    token = secrets.token_urlsafe(32)
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM login_sessions WHERE expires_at <= datetime('now')")
        conn.execute(
            "INSERT INTO login_sessions (token_hash, user_id, expires_at) VALUES (?, ?, datetime('now', ?))",
            (_token_hash(token), user_id, f"+{config.SESSION_DAYS} days"),
        )
    return token


def user_from_login_session(token: str | None) -> User | None:
    if not token or not isinstance(token, str):
        return None
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT u.* FROM login_sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > datetime('now')",
            (_token_hash(token),),
        ).fetchone()
    return _to_user(row)


def delete_login_session(token: str | None) -> None:
    if token:
        with closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM login_sessions WHERE token_hash = ?", (_token_hash(token),))


def consume_credit(user_id: int) -> bool:
    """Scala un credito; True se riuscito. Gli admin non consumano crediti."""
    with closing(_connect()) as conn, conn:
        # Condizione nella stessa UPDATE: due richieste concorrenti non portano i crediti sotto zero.
        cur = conn.execute(
            "UPDATE users SET credits = credits - 1 WHERE id = ? AND is_admin = 0 AND credits > 0", (user_id,)
        )
        if cur.rowcount:
            return True
        row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["is_admin"])


def record_purchase(stripe_session_id: str, user_id: int, credits: int, amount_total: int, currency: str) -> bool:
    """Registra l'acquisto e accredita i crediti. False se la sessione era già stata erogata."""
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO payments (stripe_session_id, user_id, credits, amount_total, currency) "
                "VALUES (?, ?, ?, ?, ?)",
                (stripe_session_id, user_id, credits, amount_total, currency),
            )
            if not conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (credits, user_id)).rowcount:
                raise AuthError(f"Utente {user_id} inesistente.")
    except sqlite3.IntegrityError:
        return False
    return True


FLAG_FREE_MANUAL_MODE = "free_manual_mode"


def get_flag(key: str) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return bool(row and row["value"] == "1")


def set_flag(key: str, enabled: bool) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "1" if enabled else "0"),
        )


def log_usage(user_id: int, action: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO usage_log (user_id, action) VALUES (?, ?)", (user_id, action))


def add_credits(email: str, delta: int) -> int:
    """Aggiunge (o toglie, se negativo) crediti all'utente; restituisce il nuovo saldo."""
    email = email.strip().lower()
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT id, credits FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            raise AuthError(f"Nessun utente con email {email}.")
        if row["credits"] + delta < 0:
            raise AuthError(f"{email} ha solo {row['credits']} crediti: non se ne possono togliere {-delta}.")
        conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (delta, row["id"]))
        return row["credits"] + delta


def users_overview() -> list[dict]:
    """Utenti con saldo, operazioni con Claude e acquisti, per il pannello admin."""
    with closing(_connect()) as conn:
        rows = conn.execute("""
            SELECT u.email, u.credits, u.is_admin, u.can_save_applications, datetime(u.created_at, 'localtime') AS created_at,
                   (SELECT COUNT(*) FROM usage_log l WHERE l.user_id = u.id) AS claude_calls,
                   (SELECT COUNT(*) FROM payments p WHERE p.user_id = u.id) AS purchases,
                   (SELECT COALESCE(SUM(amount_total), 0) FROM payments p WHERE p.user_id = u.id) AS spent_cents
            FROM users u ORDER BY u.id
        """).fetchall()
    return [dict(r) for r in rows]


def usage_stats() -> dict:
    """Totale operazioni con Claude, quelle di oggi e il dettaglio per tipo."""
    with closing(_connect()) as conn:
        total, today = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(date(created_at, 'localtime') = date('now', 'localtime')), 0) FROM usage_log"
        ).fetchone()
        by_action = dict(conn.execute(
            "SELECT action, COUNT(*) FROM usage_log GROUP BY action ORDER BY COUNT(*) DESC"
        ).fetchall())
    return {"total": total, "today": today, "by_action": by_action}


def sales_stats() -> dict:
    """Vendite complessive: numero di acquisti, incasso in centesimi e crediti venduti."""
    with closing(_connect()) as conn:
        count, revenue, credits = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_total), 0), COALESCE(SUM(credits), 0) FROM payments"
        ).fetchone()
    return {"count": count, "revenue_cents": revenue, "credits": credits}


def set_credits(email: str, credits: int) -> None:
    with closing(_connect()) as conn, conn:
        if not conn.execute("UPDATE users SET credits = ? WHERE email = ?", (credits, email.strip().lower())).rowcount:
            raise AuthError(f"Nessun utente con email {email}.")


def set_can_save_applications(email: str, enabled: bool) -> None:
    with closing(_connect()) as conn, conn:
        if not conn.execute("UPDATE users SET can_save_applications = ? WHERE email = ?",
                            (enabled, email.strip().lower())).rowcount:
            raise AuthError(f"Nessun utente con email {email}.")


def set_admin(email: str, is_admin: bool) -> None:
    with closing(_connect()) as conn, conn:
        if not conn.execute("UPDATE users SET is_admin = ? WHERE email = ?", (is_admin, email.strip().lower())).rowcount:
            raise AuthError(f"Nessun utente con email {email}.")


def list_users() -> list[User]:
    with closing(_connect()) as conn:
        return [_to_user(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]
