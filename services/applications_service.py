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
    # None nelle bozze con il solo CV, salvate prima di scegliere l'annuncio.
    match: JobMatchResult | None
    # False finché il credito della candidatura non è stato scalato: niente download dall'archivio.
    paid: bool

    @property
    def title(self) -> str:
        if name := " - ".join(x for x in (self.company_name, self.job_title) if x):
            return name
        if self.match is None:
            return f"CV di {self.cv.full_name}" if self.cv.full_name else "CV"
        return "Candidatura senza titolo"

    @property
    def stage(self) -> str:
        if self.match is None:
            return "CV e LinkedIn" if self.linkedin else "solo CV"
        return "completa" if self.linkedin else "CV e lettera"


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
    elif row["cover_letter"]:  # salvata prima che si conservasse il risultato completo
        match = JobMatchResult(match_score=row["match_score"] or 0, tailored_cv=cv, cover_letter=row["cover_letter"])
    else:
        match = None
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
        paid=row["paid"],
    )


_SELECT = (
    "SELECT id, company_name, job_title, "
    "to_char(created_at AT TIME ZONE %(tz)s, 'YYYY-MM-DD HH24:MI:SS') AS created_at, "
    "cv_json, cover_letter, linkedin_json, match_score, base_cv_json, job_text, match_json, paid "
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

    def _values(self, base_cv: ResumeData, match: JobMatchResult | None, job_text: str | None,
                linkedin: dict[str, LinkedInContent] | None, paid: bool, **extra) -> dict:
        """Valori di una candidatura; senza annuncio (bozza con il solo CV) il CV salvato è quello di partenza."""
        cv = match.tailored_cv if match else base_cv
        return self._params(
            company=(match.company_name.strip() if match else ""), title=(match.job_title.strip() if match else ""),
            cv=Jsonb(cv.model_dump(mode="json")), letter=match.cover_letter if match else "",
            li=_linkedin_json(linkedin), score=match.match_score if match else None,
            base=Jsonb(base_cv.model_dump(mode="json")), job=job_text,
            match=Jsonb(match.model_dump(mode="json")) if match else None, paid=paid, **extra,
        )

    def save(self, base_cv: ResumeData, match: JobMatchResult | None, job_text: str | None,
             linkedin: dict[str, LinkedInContent] | None, paid: bool) -> int:
        with self._transaction() as conn:
            return conn.execute(
                "INSERT INTO saved_applications (user_id, company_name, job_title, cv_json, cover_letter, "
                "linkedin_json, match_score, base_cv_json, job_text, match_json, paid) "
                "VALUES (%(uid)s, %(company)s, %(title)s, %(cv)s, %(letter)s, %(li)s, %(score)s, %(base)s, "
                "%(job)s, %(match)s, %(paid)s) RETURNING id",
                self._values(base_cv, match, job_text, linkedin, paid),
            ).fetchone()["id"]

    def update(self, application_id: int, base_cv: ResumeData, match: JobMatchResult | None, job_text: str | None,
               linkedin: dict[str, LinkedInContent] | None, paid: bool) -> bool:
        """Aggiorna la candidatura; azienda e ruolo restano quelli già salvati se l'annuncio non li indica."""
        with self._transaction() as conn:
            return bool(conn.execute(
                "UPDATE saved_applications SET company_name = COALESCE(NULLIF(%(company)s, ''), company_name), "
                "job_title = COALESCE(NULLIF(%(title)s, ''), job_title), cv_json = %(cv)s, "
                "cover_letter = %(letter)s, linkedin_json = %(li)s, match_score = %(score)s, "
                "base_cv_json = %(base)s, job_text = %(job)s, match_json = %(match)s, paid = paid OR %(paid)s "
                "WHERE id = %(id)s AND user_id = %(uid)s",
                self._values(base_cv, match, job_text, linkedin, paid, id=application_id),
            ).rowcount)

    def delete(self, application_id: int) -> bool:
        with self._transaction() as conn:
            return bool(conn.execute("DELETE FROM saved_applications WHERE id = %(id)s AND user_id = %(uid)s",
                                     self._params(id=application_id)).rowcount)
