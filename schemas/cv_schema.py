"""Schemi Pydantic V2 per CV, job match e contenuti LinkedIn."""

from typing import Optional

from pydantic import BaseModel, Field


class Experience(BaseModel):
    company: str = Field(description="Nome dell'azienda.")
    role: str = Field(description="Titolo del ruolo ricoperto.")
    location: Optional[str] = Field(default=None, description="Sede di lavoro (città, paese o 'Remote').")
    start_date: str = Field(description="Data di inizio, formato 'MM/YYYY' o 'YYYY'.")
    end_date: Optional[str] = Field(
        default=None, description="Data di fine, formato 'MM/YYYY' o 'YYYY'. 'Present' se in corso."
    )
    highlights: list[str] = Field(
        default_factory=list,
        description=(
            "Bullet point in formato XYZ: 'Ottenuto [X] misurato da [Y] facendo [Z]'. "
            "Includere metriche solo se presenti nei dati di origine."
        ),
    )


class Education(BaseModel):
    institution: str = Field(description="Università, scuola o ente formativo.")
    degree: str = Field(description="Titolo di studio conseguito e indirizzo.")
    start_date: Optional[str] = Field(default=None, description="Data di inizio, formato 'MM/YYYY' o 'YYYY'.")
    end_date: Optional[str] = Field(default=None, description="Data di fine, formato 'MM/YYYY' o 'YYYY'.")
    details: Optional[str] = Field(default=None, description="Voto, tesi, riconoscimenti o corsi rilevanti.")


class ResumeData(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    location: Optional[str] = None
    professional_summary: str = Field(
        description="Sintesi professionale di 3-4 righe, concreta e orientata ai risultati."
    )
    experiences: list[Experience] = Field(
        default_factory=list, description="Esperienze in ordine cronologico inverso."
    )
    education: list[Education] = Field(default_factory=list)
    hard_skills: list[str] = Field(default_factory=list, description="Competenze tecniche, tool, metodologie.")
    soft_skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list, description="Lingue con livello, es. 'Inglese (C1)'.")


class JobMatchResult(BaseModel):
    match_score: int = Field(ge=0, le=100, description="Affinità CV/annuncio da 0 a 100.")
    matching_keywords: list[str] = Field(
        default_factory=list, description="Keyword dell'annuncio già presenti nel CV."
    )
    missing_keywords: list[str] = Field(
        default_factory=list, description="Keyword rilevanti dell'annuncio assenti dal CV."
    )
    tailored_cv: ResumeData = Field(description="CV riscritto e ottimizzato per l'annuncio.")
    cover_letter: str = Field(description="Lettera di presentazione mirata all'annuncio.")


class DocumentRevision(BaseModel):
    tailored_cv: ResumeData = Field(description="CV adattato dopo le modifiche richieste.")
    cover_letter: str = Field(description="Lettera di presentazione dopo le modifiche richieste.")
    changes_summary: list[str] = Field(
        default_factory=list, description="Elenco breve delle modifiche applicate, una per voce."
    )


class LinkedInContent(BaseModel):
    headline: str = Field(description="Headline LinkedIn, massimo 220 caratteri.")
    about_section: str = Field(description="Sezione 'Informazioni', massimo 2600 caratteri.")
    launch_post: str = Field(description="Post di lancio per il feed LinkedIn.")
