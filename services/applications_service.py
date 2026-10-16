"""Archivio «Le mie candidature», isolato per utente.

SQLite non ha politiche RLS: l'isolamento è garantito da ApplicationStore, legato
all'id dell'utente collegato. Ogni query filtra su `user_id = ?` con quell'id, che
non arriva mai dai parametri dei metodi; un id di candidatura di un altro utente
si comporta quindi come inesistente (nessuna lettura, modifica o eliminazione).
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass

import config
from schemas import JobMatchResult, LinkedInContent, ResumeData

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_applications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_name  TEXT    NOT NULL,
    job_title     TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cv_json       TEXT    NOT NULL,
    cover_letter  TEXT    NOT NULL,
    linkedin_json TEXT,
    match_score   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_saved_applications_user ON saved_applications(user_id, created_at);
"""
# Colonne aggiunte per riaprire la candidatura nel percorso guidato (nulle nelle candidature più vecchie):
# CV di partenza, testo dell'annuncio e analisi completa (parole chiave comprese).
_EXTRA_COLUMNS = {"base_cv_json": "TEXT", "job_text": "TEXT", "match_json": "TEXT"}


@dataclass(frozen=True)
class SavedApplication:
    id: int
    company_name: str
    job_title: str
    created_at: str
    cv: ResumeData
    cover_letter: str
    linkedin: dict[str, LinkedInContent]
    match_score: int | None
    base_cv: ResumeData | None
    job_text: str | None
    match: JobMatchResult

    @property
    def title(self) -> str:
        return f"{self.company_name} - {self.job_title}"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript(_SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(saved_applications)")}
        for column, kind in _EXTRA_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE saved_applications ADD COLUMN {column} {kind}")


def _linkedin_json(linkedin: dict[str, LinkedInContent] | None) -> str | None:
    if not linkedin:
        return None
    return json.dumps({tone: content.model_dump() for tone, content in linkedin.items()}, ensure_ascii=False)


def _to_application(row: sqlite3.Row) -> SavedApplication:
    linkedin = json.loads(row["linkedin_json"]) if row["linkedin_json"] else {}
    cv = ResumeData.model_validate_json(row["cv_json"])
    if row["match_json"]:
        match = JobMatchResult.model_validate_json(row["match_json"])
    else:
        match = JobMatchResult(match_score=row["match_score"] or 0, tailored_cv=cv, cover_letter=row["cover_letter"])
    return SavedApplication(
        id=row["id"],
        company_name=row["company_name"],
        job_title=row["job_title"],
        created_at=row["created_at"],
        cv=cv,
        cover_letter=row["cover_letter"],
        linkedin={tone: LinkedInContent.model_validate(c) for tone, c in linkedin.items()},
        match_score=row["match_score"],
        base_cv=ResumeData.model_validate_json(row["base_cv_json"]) if row["base_cv_json"] else None,
        job_text=row["job_text"],
        match=match,
    )


_SELECT = ("SELECT id, company_name, job_title, datetime(created_at, 'localtime') AS created_at, "
           "cv_json, cover_letter, linkedin_json, match_score, base_cv_json, job_text, match_json "
           "FROM saved_applications")


class ApplicationStore:
    """Accesso alle candidature del solo utente `user_id`."""

    def __init__(self, user_id: int):
        self._user_id = user_id

    def list(self) -> list[SavedApplication]:
        with closing(_connect()) as conn:
            rows = conn.execute(f"{_SELECT} WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                                (self._user_id,)).fetchall()
        return [_to_application(r) for r in rows]

    def get(self, application_id: int) -> SavedApplication | None:
        with closing(_connect()) as conn:
            row = conn.execute(f"{_SELECT} WHERE id = ? AND user_id = ?",
                               (application_id, self._user_id)).fetchone()
        return _to_application(row) if row else None

    def save(self, company_name: str, job_title: str, match: JobMatchResult, base_cv: ResumeData,
             job_text: str | None, linkedin: dict[str, LinkedInContent] | None = None) -> int:
        with closing(_connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO saved_applications (user_id, company_name, job_title, cv_json, cover_letter, "
                "linkedin_json, match_score, base_cv_json, job_text, match_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._user_id, company_name.strip(), job_title.strip(), match.tailored_cv.model_dump_json(),
                 match.cover_letter, _linkedin_json(linkedin), match.match_score, base_cv.model_dump_json(),
                 job_text, match.model_dump_json()),
            )
            return cur.lastrowid

    def update_documents(self, application_id: int, match: JobMatchResult, base_cv: ResumeData) -> bool:
        with closing(_connect()) as conn, conn:
            return bool(conn.execute(
                "UPDATE saved_applications SET cv_json = ?, cover_letter = ?, match_score = ?, match_json = ?, "
                "base_cv_json = ? WHERE id = ? AND user_id = ?",
                (match.tailored_cv.model_dump_json(), match.cover_letter, match.match_score,
                 match.model_dump_json(), base_cv.model_dump_json(), application_id, self._user_id),
            ).rowcount)

    def update_linkedin(self, application_id: int, linkedin: dict[str, LinkedInContent] | None) -> bool:
        with closing(_connect()) as conn, conn:
            return bool(conn.execute(
                "UPDATE saved_applications SET linkedin_json = ? WHERE id = ? AND user_id = ?",
                (_linkedin_json(linkedin), application_id, self._user_id),
            ).rowcount)

    def delete(self, application_id: int) -> bool:
        with closing(_connect()) as conn, conn:
            return bool(conn.execute("DELETE FROM saved_applications WHERE id = ? AND user_id = ?",
                                     (application_id, self._user_id)).rowcount)
