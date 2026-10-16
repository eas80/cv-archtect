"""Archivio «Le mie candidature», isolato per utente.

Due livelli di protezione:
- ApplicationStore è legato all'id dell'utente collegato e ogni query filtra su `user_id`
  con quell'id, che non arriva mai dai parametri dei metodi;
- le query girano con il ruolo applicativo soggetto alla policy RLS di Postgres
  (`user_id = app.current_user_id`, vedi services/db.py): anche una query senza filtro
  vedrebbe solo le righe dell'utente.
Un id di candidatura di un altro utente si comporta quindi come inesistente.
"""

from dataclasses import dataclass

from psycopg.types.json import Jsonb

import config
from schemas import JobMatchResult, LinkedInContent, ResumeData
from services import db


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
    # Per riaprire la candidatura (nulli in quelle salvate prima di questa funzione).
    base_cv: ResumeData | None
    job_text: str | None
    match: JobMatchResult

    @property
    def title(self) -> str:
        return f"{self.company_name} - {self.job_title}"


def init_db() -> None:
    db.ensure_schema()


def _linkedin_json(linkedin: dict[str, LinkedInContent] | None) -> Jsonb | None:
    if not linkedin:
        return None
    return Jsonb({tone: content.model_dump(mode="json") for tone, content in linkedin.items()})


def _to_application(row: dict) -> SavedApplication:
    cv = ResumeData.model_validate(row["cv_json"])
    if row["match_json"]:
        match = JobMatchResult.model_validate(row["match_json"])
    else:
        match = JobMatchResult(match_score=row["match_score"] or 0, tailored_cv=cv, cover_letter=row["cover_letter"])
    return SavedApplication(
        id=row["id"],
        company_name=row["company_name"],
        job_title=row["job_title"],
        created_at=row["created_at"],
        cv=cv,
        cover_letter=row["cover_letter"],
        linkedin={tone: LinkedInContent.model_validate(c) for tone, c in (row["linkedin_json"] or {}).items()},
        match_score=row["match_score"],
        base_cv=ResumeData.model_validate(row["base_cv_json"]) if row["base_cv_json"] else None,
        job_text=row["job_text"],
        match=match,
    )


_SELECT = (
    "SELECT id, company_name, job_title, "
    "to_char(created_at AT TIME ZONE %(tz)s, 'YYYY-MM-DD HH24:MI:SS') AS created_at, "
    "cv_json, cover_letter, linkedin_json, match_score, base_cv_json, job_text, match_json "
    "FROM saved_applications"
)


class ApplicationStore:
    """Accesso alle candidature del solo utente `user_id`."""

    def __init__(self, user_id: int):
        self._user_id = user_id

    def _transaction(self):
        return db.user_scoped_transaction(self._user_id)

    def _params(self, **extra) -> dict:
        return {"uid": self._user_id, "tz": config.APP_TIMEZONE, **extra}

    def list(self) -> list[SavedApplication]:
        with self._transaction() as conn:
            rows = conn.execute(f"{_SELECT} WHERE user_id = %(uid)s ORDER BY created_at DESC, id DESC",
                                self._params()).fetchall()
        return [_to_application(r) for r in rows]

    def get(self, application_id: int) -> SavedApplication | None:
        with self._transaction() as conn:
            row = conn.execute(f"{_SELECT} WHERE id = %(id)s AND user_id = %(uid)s",
                               self._params(id=application_id)).fetchone()
        return _to_application(row) if row else None

    def save(self, company_name: str, job_title: str, match: JobMatchResult, base_cv: ResumeData,
             job_text: str | None, linkedin: dict[str, LinkedInContent] | None = None) -> int:
        with self._transaction() as conn:
            return conn.execute(
                "INSERT INTO saved_applications (user_id, company_name, job_title, cv_json, cover_letter, "
                "linkedin_json, match_score, base_cv_json, job_text, match_json) "
                "VALUES (%(uid)s, %(company)s, %(title)s, %(cv)s, %(letter)s, %(li)s, %(score)s, %(base)s, "
                "%(job)s, %(match)s) RETURNING id",
                self._params(
                    company=company_name.strip(), title=job_title.strip(),
                    cv=Jsonb(match.tailored_cv.model_dump(mode="json")), letter=match.cover_letter,
                    li=_linkedin_json(linkedin), score=match.match_score,
                    base=Jsonb(base_cv.model_dump(mode="json")), job=job_text,
                    match=Jsonb(match.model_dump(mode="json")),
                ),
            ).fetchone()["id"]

    def update_documents(self, application_id: int, match: JobMatchResult, base_cv: ResumeData) -> bool:
        with self._transaction() as conn:
            return bool(conn.execute(
                "UPDATE saved_applications SET cv_json = %(cv)s, cover_letter = %(letter)s, match_score = %(score)s, "
                "match_json = %(match)s, base_cv_json = %(base)s WHERE id = %(id)s AND user_id = %(uid)s",
                self._params(
                    id=application_id, cv=Jsonb(match.tailored_cv.model_dump(mode="json")),
                    letter=match.cover_letter, score=match.match_score,
                    match=Jsonb(match.model_dump(mode="json")), base=Jsonb(base_cv.model_dump(mode="json")),
                ),
            ).rowcount)

    def update_linkedin(self, application_id: int, linkedin: dict[str, LinkedInContent] | None) -> bool:
        with self._transaction() as conn:
            return bool(conn.execute(
                "UPDATE saved_applications SET linkedin_json = %(li)s WHERE id = %(id)s AND user_id = %(uid)s",
                self._params(id=application_id, li=_linkedin_json(linkedin)),
            ).rowcount)

    def delete(self, application_id: int) -> bool:
        with self._transaction() as conn:
            return bool(conn.execute("DELETE FROM saved_applications WHERE id = %(id)s AND user_id = %(uid)s",
                                     self._params(id=application_id)).rowcount)
