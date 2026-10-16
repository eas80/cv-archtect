"""Copia i dati dal vecchio database SQLite (database.db) al database Postgres in DATABASE_URL.

    python migrate_sqlite_to_postgres.py [percorso/database.db]

Da eseguire una volta, su un database Postgres vuoto: utenti, pagamenti, statistiche,
impostazioni e candidature mantengono gli stessi id. Gli accessi ricordati (cookie)
non vengono copiati: basta rifare il login.
"""

import json
import sqlite3
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from services import db

# Tabella -> colonne da copiare (le colonne mancanti nei database più vecchi diventano NULL o default).
TABLES = {
    "users": ["id", "email", "password_hash", "credits", "is_admin", "created_at", "recovery_hash",
              "can_save_applications"],
    "payments": ["stripe_session_id", "user_id", "credits", "amount_total", "currency", "created_at"],
    "settings": ["key", "value"],
    "usage_log": ["id", "user_id", "action", "created_at"],
    "saved_applications": ["id", "user_id", "company_name", "job_title", "created_at", "cv_json", "cover_letter",
                           "linkedin_json", "match_score", "base_cv_json", "job_text", "match_json"],
}
BOOLEAN_COLUMNS = {"is_admin", "can_save_applications"}
JSON_COLUMNS = {"cv_json", "linkedin_json", "base_cv_json", "match_json"}
IDENTITY_TABLES = ("users", "usage_log", "saved_applications")


def convert(column: str, value):
    if value is None:
        return None
    if column in BOOLEAN_COLUMNS:
        return bool(value)
    if column in JSON_COLUMNS:
        return Jsonb(json.loads(value))
    if column == "created_at":
        return f"{value}+00"  # SQLite salvava CURRENT_TIMESTAMP in UTC, senza fuso
    return value


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / "database.db")
    if not source.exists():
        sys.exit(f"Database SQLite non trovato: {source}")
    sqlite = sqlite3.connect(source)
    sqlite.row_factory = sqlite3.Row
    existing = {r[0] for r in sqlite.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    db.ensure_schema()
    with db.transaction() as conn:
        if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]:
            sys.exit("Il database Postgres contiene già utenti: la migrazione va fatta su un database vuoto.")
        for table, columns in TABLES.items():
            if table not in existing:
                print(f"{table}: assente nel database SQLite, saltata")
                continue
            available = {r["name"] for r in sqlite.execute(f"PRAGMA table_info({table})")}
            cols = [c for c in columns if c in available]
            rows = sqlite.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            placeholders = ", ".join(["%s"] * len(cols))
            for row in rows:
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                             [convert(c, row[c]) for c in cols])
            print(f"{table}: {len(rows)} righe copiate")
        # Gli id sono stati copiati a mano: i contatori ripartono dopo l'ultimo.
        for table in IDENTITY_TABLES:
            conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                         f"COALESCE((SELECT max(id) FROM {table}), 0) + 1, false)")
    print("Migrazione completata.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
