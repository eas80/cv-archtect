"""Generazione PDF di CV e lettera di presentazione con template ATS-friendly.

Tutti i template rispettano gli stessi vincoli ATS: colonna singola, testo
selezionabile, nessuna tabella, immagine o intestazione/piè di pagina, titoli
di sezione standard. Cambiano solo font, colori, spaziature e ordine delle sezioni.
"""

import io
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from schemas import ResumeData

# --- Font ---------------------------------------------------------------------
# Font di sistema se disponibili (resa migliore), altrimenti i font PDF standard.
_SYSTEM_FONTS = {
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf"),
}
_FALLBACK_FONTS = {
    "georgia": ("Times-Roman", "Times-Bold", "Times-Italic"),
    "calibri": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"),
    "arial": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"),
}
_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


@dataclass(frozen=True)
class _Fonts:
    regular: str
    bold: str
    italic: str


_font_cache: dict[str, _Fonts] = {}


def _fonts(family: str) -> _Fonts:
    if family in _font_cache:
        return _font_cache[family]
    names = _FALLBACK_FONTS[family]
    files = [_FONT_DIR / f for f in _SYSTEM_FONTS[family]]
    if all(f.is_file() for f in files):
        names = tuple(f"{family}-{variant}" for variant in ("regular", "bold", "italic"))
        for name, file in zip(names, files):
            pdfmetrics.registerFont(TTFont(name, str(file)))
        # Necessario perché <b> e <i> nei Paragraph trovino le varianti giuste.
        pdfmetrics.registerFontFamily(
            names[0], normal=names[0], bold=names[1], italic=names[2], boldItalic=names[1]
        )
    _font_cache[family] = _Fonts(*names)
    return _font_cache[family]


# --- Template -------------------------------------------------------------------
@dataclass(frozen=True)
class PdfTemplate:
    label: str
    description: str
    body_font: str
    heading_font: str
    accent: str
    base_size: float
    name_size: float
    centered_header: bool
    uppercase_headings: bool
    margin_mm: float
    section_order: tuple[str, ...]


_STANDARD_ORDER = ("summary", "experience", "education", "skills", "certifications", "languages")

TEMPLATES: dict[str, PdfTemplate] = {
    "classico": PdfTemplate(
        label="Classico",
        description="Serif, intestazione centrata, titoli maiuscoli. Adatto a contesti tradizionali "
        "(legale, finanza, PA).",
        body_font="georgia",
        heading_font="georgia",
        accent="#000000",
        base_size=10.5,
        name_size=20,
        centered_header=True,
        uppercase_headings=True,
        margin_mm=20,
        section_order=_STANDARD_ORDER,
    ),
    "moderno": PdfTemplate(
        label="Moderno",
        description="Sans-serif, allineato a sinistra, titoli in blu scuro. Buono per la maggior parte "
        "dei ruoli in azienda.",
        body_font="calibri",
        heading_font="calibri",
        accent="#1F4E79",
        base_size=11,
        name_size=22,
        centered_header=False,
        uppercase_headings=False,
        margin_mm=18,
        section_order=_STANDARD_ORDER,
    ),
    "tecnico": PdfTemplate(
        label="Tecnico compatto",
        description="Compatto, competenze subito dopo il profilo. Pensato per ruoli tecnici e CV lunghi "
        "da tenere su una o due pagine.",
        body_font="arial",
        heading_font="arial",
        accent="#2F4F4F",
        base_size=9.5,
        name_size=18,
        centered_header=False,
        uppercase_headings=True,
        margin_mm=14,
        section_order=("summary", "skills", "experience", "education", "certifications", "languages"),
    ),
    "executive": PdfTemplate(
        label="Executive",
        description="Titoli serif e testo sans, spaziature ampie. Per profili senior e manageriali.",
        body_font="calibri",
        heading_font="georgia",
        accent="#3A3A3A",
        base_size=11,
        name_size=24,
        centered_header=True,
        uppercase_headings=False,
        margin_mm=22,
        section_order=_STANDARD_ORDER,
    ),
}

# --- Etichette per lingua ----------------------------------------------------------
LANGUAGES = {"it": "Italiano", "en": "English"}

_LABELS = {
    "it": {
        "summary": "Profilo",
        "experience": "Esperienza professionale",
        "education": "Formazione",
        "hard_skills": "Competenze tecniche",
        "soft_skills": "Competenze trasversali",
        "skills": "Competenze",
        "certifications": "Certificazioni",
        "languages": "Lingue",
        "present": "In corso",
        "cover_letter": "Lettera di presentazione",
        "months": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                   "settembre", "ottobre", "novembre", "dicembre"),
    },
    "en": {
        "summary": "Summary",
        "experience": "Professional Experience",
        "education": "Education",
        "hard_skills": "Technical Skills",
        "soft_skills": "Soft Skills",
        "skills": "Skills",
        "certifications": "Certifications",
        "languages": "Languages",
        "present": "Present",
        "cover_letter": "Cover Letter",
        "months": ("January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"),
    },
}

_EN_MARKERS = {"the", "and", "with", "for", "of", "to", "in", "on", "led", "built", "managed"}
_IT_MARKERS = {"il", "la", "e", "con", "per", "di", "del", "della", "nel", "gestito", "sviluppato"}


def guess_language(*texts: str) -> str:
    """Stima grossolana IT/EN, usata solo come valore predefinito nell'interfaccia."""
    words = re.findall(r"[a-zà-ù]+", " ".join(texts).lower())
    en = sum(w in _EN_MARKERS for w in words)
    it = sum(w in _IT_MARKERS for w in words)
    return "en" if en > it else "it"


# --- Stili -----------------------------------------------------------------------
@dataclass(frozen=True)
class _Styles:
    name: ParagraphStyle
    contacts: ParagraphStyle
    heading: ParagraphStyle
    body: ParagraphStyle
    entry_title: ParagraphStyle
    entry_meta: ParagraphStyle
    bullet: ParagraphStyle


def _styles(tpl: PdfTemplate) -> _Styles:
    body_f, head_f = _fonts(tpl.body_font), _fonts(tpl.heading_font)
    accent = HexColor(tpl.accent)
    size = tpl.base_size
    header_align = TA_CENTER if tpl.centered_header else TA_LEFT
    base = ParagraphStyle("body", fontName=body_f.regular, fontSize=size, leading=size * 1.35)
    return _Styles(
        name=ParagraphStyle(
            "name", parent=base, fontName=head_f.bold, fontSize=tpl.name_size,
            leading=tpl.name_size * 1.2, alignment=header_align, textColor=accent,
        ),
        contacts=ParagraphStyle(
            "contacts", parent=base, fontSize=size - 0.5, alignment=header_align,
            textColor=HexColor("#444444"), spaceBefore=2,
        ),
        heading=ParagraphStyle(
            "heading", parent=base, fontName=head_f.bold, fontSize=size + 1.5,
            leading=(size + 1.5) * 1.3, textColor=accent, spaceBefore=size * 1.1, spaceAfter=1,
        ),
        body=base,
        entry_title=ParagraphStyle("entry_title", parent=base, fontName=body_f.bold, spaceBefore=size * 0.6),
        entry_meta=ParagraphStyle(
            "entry_meta", parent=base, fontName=body_f.italic, fontSize=size - 0.5,
            textColor=HexColor("#555555"), spaceAfter=2,
        ),
        bullet=ParagraphStyle("bullet", parent=base, leftIndent=12, bulletIndent=2, spaceAfter=1.5),
    )


def _p(text: str, style: ParagraphStyle, **kwargs) -> Paragraph:
    return Paragraph(escape(text), style, **kwargs)


def _heading(title: str, tpl: PdfTemplate, st: _Styles) -> list:
    text = title.upper() if tpl.uppercase_headings else title
    return [
        _p(text, st.heading),
        HRFlowable(width="100%", thickness=0.6, color=HexColor(tpl.accent), spaceBefore=0, spaceAfter=4),
    ]


def _contacts_line(cv: ResumeData) -> str:
    return "  ·  ".join(x for x in (cv.email, cv.phone, cv.location, cv.linkedin_url) if x)


def _header(cv: ResumeData, st: _Styles) -> list:
    flow = [_p(cv.full_name, st.name)]
    if contacts := _contacts_line(cv):
        flow.append(_p(contacts, st.contacts))
    return flow


def _section(key: str, cv: ResumeData, tpl: PdfTemplate, st: _Styles, labels: dict) -> list:
    if key == "summary" and cv.professional_summary:
        return [*_heading(labels["summary"], tpl, st), _p(cv.professional_summary, st.body)]

    if key == "experience" and cv.experiences:
        flow = _heading(labels["experience"], tpl, st)
        for exp in cv.experiences:
            end = exp.end_date or labels["present"]
            if end.strip().lower() in {"present", "presente", "in corso"}:
                end = labels["present"]
            meta = f"{exp.start_date} – {end}" + (f"  ·  {exp.location}" if exp.location else "")
            # Titolo e date restano uniti: un ruolo non va spezzato a fine pagina.
            block = [
                Paragraph(f"<b>{escape(exp.role)}</b> — {escape(exp.company)}", st.entry_title),
                _p(meta, st.entry_meta),
            ]
            bullets = [_p(item, st.bullet, bulletText="•") for item in exp.highlights]
            flow.append(KeepTogether(block + bullets[:1]))
            flow.extend(bullets[1:])
        return flow

    if key == "education" and cv.education:
        flow = _heading(labels["education"], tpl, st)
        for edu in cv.education:
            dates = " – ".join(d for d in (edu.start_date, edu.end_date) if d)
            title = f"<b>{escape(edu.degree)}</b> — {escape(edu.institution)}"
            block = [Paragraph(title, st.entry_title)]
            if dates:
                block.append(_p(dates, st.entry_meta))
            if edu.details:
                block.append(_p(edu.details, st.body))
            flow.append(KeepTogether(block))
        return flow

    if key == "skills" and (cv.hard_skills or cv.soft_skills):
        flow = _heading(labels["skills"], tpl, st)
        for label_key, skills in (("hard_skills", cv.hard_skills), ("soft_skills", cv.soft_skills)):
            if skills:
                text = f"<b>{escape(labels[label_key])}:</b> {escape(', '.join(skills))}"
                flow.append(Paragraph(text, ParagraphStyle("skills", parent=st.body, spaceAfter=3)))
        return flow

    if key == "certifications" and cv.certifications:
        return [
            *_heading(labels["certifications"], tpl, st),
            *(_p(cert, st.bullet, bulletText="•") for cert in cv.certifications),
        ]

    if key == "languages" and cv.languages:
        return [*_heading(labels["languages"], tpl, st), _p(", ".join(cv.languages), st.body)]

    return []


def _build(flow: list, tpl: PdfTemplate, title: str, author: str) -> bytes:
    buffer = io.BytesIO()
    margin = tpl.margin_mm * mm
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin,
        bottomMargin=margin, title=title, author=author, subject=title, creator="CV Architect",
    )
    doc.build(flow)
    return buffer.getvalue()


def render_cv_pdf(cv: ResumeData, template: str = "moderno", language: str = "it") -> bytes:
    tpl, labels = TEMPLATES[template], _LABELS[language]
    st = _styles(tpl)
    flow = _header(cv, st)
    for key in tpl.section_order:
        flow.extend(_section(key, cv, tpl, st, labels))
    return _build(flow, tpl, title=f"CV - {cv.full_name}", author=cv.full_name)


def render_cover_letter_pdf(
    cv: ResumeData, letter: str, template: str = "moderno", language: str = "it"
) -> bytes:
    tpl, labels = TEMPLATES[template], _LABELS[language]
    st = _styles(tpl)
    body = ParagraphStyle("letter", parent=st.body, fontSize=tpl.base_size + 0.5,
                          leading=(tpl.base_size + 0.5) * 1.45, spaceAfter=tpl.base_size * 0.9)

    today = date.today()
    month = labels["months"][today.month - 1]
    date_text = f"{today.day} {month} {today.year}" if language == "it" else f"{month} {today.day}, {today.year}"

    flow = _header(cv, st)
    flow.append(HRFlowable(width="100%", thickness=0.6, color=HexColor(tpl.accent), spaceBefore=6, spaceAfter=14))
    flow.append(_p(date_text, ParagraphStyle("date", parent=body, alignment=2)))

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", letter.strip()) if p.strip()]
    for paragraph in paragraphs:
        flow.append(Paragraph(escape(paragraph).replace("\n", "<br/>"), body))

    # Firma solo se la lettera non si chiude già con il nome del candidato.
    last_line = letter.strip().splitlines()[-1].strip() if letter.strip() else ""
    if last_line.lower() != cv.full_name.strip().lower():
        flow.extend([Spacer(1, 6), _p(cv.full_name, body)])

    return _build(flow, tpl, title=f"{labels['cover_letter']} - {cv.full_name}", author=cv.full_name)
