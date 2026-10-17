"""Generazione PDF di CV e lettera di presentazione.

Gli stili a colonna singola (layout "single") rispettano i vincoli ATS: testo selezionabile,
nessuna tabella, immagine o intestazione/piè di pagina, titoli di sezione standard; cambiano
solo font, colori, spaziature e ordine delle sezioni. Gli stili grafici (colonne, fasce
colorate) sono più d'impatto ma alcuni sistemi ATS li leggono peggio: l'app lo dice all'utente.
"""

import io
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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
    # "single" (colonna singola, ATS), "label_column", "sidebar", "banner" (grafici).
    layout: str = "single"

    @property
    def ats_friendly(self) -> bool:
        return self.layout == "single"


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
    "professionale": PdfTemplate(
        label="Professionale a colonne",
        description="Fascia colorata a sinistra, contatti in evidenza e titoli delle sezioni in una colonna "
        "a parte. Grafico e ordinato, per profili tecnici e manageriali.",
        body_font="calibri",
        heading_font="arial",
        accent="#1B6B5F",
        base_size=10,
        name_size=26,
        centered_header=False,
        uppercase_headings=False,
        margin_mm=14,
        section_order=_STANDARD_ORDER,
        layout="label_column",
    ),
    "laterale": PdfTemplate(
        label="Barra laterale",
        description="Due colonne: contatti, competenze, lingue e formazione nella barra colorata, profilo ed "
        "esperienze a destra. Molte informazioni in poco spazio.",
        body_font="calibri",
        heading_font="calibri",
        accent="#1F3A5F",
        base_size=9.5,
        name_size=24,
        centered_header=False,
        uppercase_headings=True,
        margin_mm=14,
        section_order=_STANDARD_ORDER,
        layout="sidebar",
    ),
    "fascia": PdfTemplate(
        label="Intestazione a fascia",
        description="Nome e contatti in una fascia colorata in cima, sezioni ariose a colonna singola. "
        "Moderno e d'impatto, per ruoli creativi e commerciali.",
        body_font="calibri",
        heading_font="arial",
        accent="#3B2F7A",
        base_size=10.5,
        name_size=26,
        centered_header=False,
        uppercase_headings=True,
        margin_mm=18,
        section_order=_STANDARD_ORDER,
        layout="banner",
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
        "contacts": "Contatti",
        "links": "Profili online",
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
        "contacts": "Contact",
        "links": "Online Profiles",
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


def _keep_with_next(flowables: list) -> list:
    """Il titolo di sezione non resta da solo in fondo alla pagina: va a capo con il contenuto."""
    for flowable in flowables:
        flowable.keepWithNext = 1
    return flowables


def _heading(title: str, tpl: PdfTemplate, st: _Styles) -> list:
    text = title.upper() if tpl.uppercase_headings else title
    return _keep_with_next([
        _p(text, st.heading),
        HRFlowable(width="100%", thickness=0.6, color=HexColor(tpl.accent), spaceBefore=0, spaceAfter=4),
    ])


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


def pdf_page_images(pdf: bytes, width_px: int = 900) -> list[bytes]:
    """Pagine del PDF come immagini PNG, per l'anteprima nell'app."""
    document = pdfium.PdfDocument(pdf)
    try:
        images = []
        for page in document:
            image = page.render(scale=width_px / page.get_width()).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            images.append(buffer.getvalue())
        return images
    finally:
        document.close()


# --- Layout grafici ----------------------------------------------------------------
# Gli stili "single" sono a colonna singola (massima leggibilità ATS). Gli altri usano
# fasce colorate, colonne e disegni sulla pagina: più d'impatto, ma alcuni sistemi ATS
# li leggono peggio. Il testo resta comunque selezionabile.
PAGE_W, PAGE_H = A4


def _tint(hex_color: str, amount: float) -> HexColor:
    """Colore schiarito verso il bianco (amount 0 = colore pieno, 1 = bianco)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return HexColor("#" + "".join(f"{round(c + (255 - c) * amount):02X}" for c in (r, g, b)))


def _date_range(start: str | None, end: str | None, labels: dict) -> str:
    end = end or labels["present"]
    if end.strip().lower() in {"present", "presente", "in corso", "current"}:
        end = labels["present"]
    return " – ".join(d for d in (start, end) if d)


class _SpacedName(Flowable):
    """Nome in maiuscolo spaziato: prima parola nel colore d'accento, il resto in grigio scuro."""

    def __init__(self, full_name: str, font: str, size: float, accent: HexColor, rest: HexColor):
        super().__init__()
        self.words = full_name.upper().split() or [""]
        self.font, self.size, self.accent, self.rest = font, size, accent, rest
        self.char_space = size * 0.12

    def _text_width(self, size: float) -> float:
        text = " ".join(self.words)
        return pdfmetrics.stringWidth(text, self.font, size) + self.char_space * len(text)

    def wrap(self, avail_width, avail_height):
        while self.size > 12 and self._text_width(self.size) > avail_width:
            self.size -= 1
            self.char_space = self.size * 0.12
        self.width, self.height = avail_width, self.size * 1.3
        return self.width, self.height

    def draw(self):
        text = self.canv.beginText(0, self.size * 0.3)
        text.setFont(self.font, self.size)
        text.setCharSpace(self.char_space)
        text.setFillColor(self.accent)
        text.textOut(self.words[0] + " ")
        text.setFillColor(self.rest)
        text.textOut(" ".join(self.words[1:]))
        self.canv.drawText(text)


class _ContactBand(Flowable):
    """Banda dei contatti larga quanto la pagina: prima cella nel colore d'accento, poi grigio scuro."""

    def __init__(self, items: list[str], font: str, left_bleed: float, first_cell: float, right_bleed: float,
                 accent: HexColor, dark: HexColor, height: float = 11 * mm):
        super().__init__()
        self.items, self.font = items, font
        self.left_bleed, self.first_cell, self.right_bleed = left_bleed, first_cell, right_bleed
        self.accent, self.dark, self.band_height = accent, dark, height

    def wrap(self, avail_width, avail_height):
        self.width, self.height = avail_width, self.band_height
        return self.width, self.height

    def _draw_fitted(self, text: str, x: float, max_width: float) -> None:
        size = 9.5
        while size > 6.5 and pdfmetrics.stringWidth(text, self.font, size) > max_width:
            size -= 0.5
        self.canv.setFont(self.font, size)
        self.canv.drawString(x, self.band_height / 2 - size * 0.35, text)

    def draw(self):
        c = self.canv
        x0 = -self.left_bleed
        c.setFillColor(self.accent)
        c.rect(x0, 0, self.first_cell, self.band_height, stroke=0, fill=1)
        c.setFillColor(self.dark)
        c.rect(x0 + self.first_cell, 0, self.left_bleed - self.first_cell + self.width + self.right_bleed,
               self.band_height, stroke=0, fill=1)
        if not self.items:
            return
        c.setFillColor(HexColor("#FFFFFF"))
        pad = 3 * mm
        self._draw_fitted(self.items[0], x0 + pad, self.first_cell - 2 * pad)
        rest = self.items[1:]
        if rest:
            slot = (self.width + self.right_bleed - self.left_bleed + self.first_cell) / len(rest)
            start = x0 + self.first_cell
            for i, item in enumerate(rest):
                self._draw_fitted(item, start + i * slot + pad, slot - 2 * pad)


class _MarginLabel(Flowable):
    """Titolo di sezione disegnato nella colonna a sinistra del testo, con la riga di separazione.

    Il titolo sta fuori dal riquadro del testo: il contenuto della sezione scorre normalmente
    e si divide tra le pagine senza tabelle.
    """

    def __init__(self, title: str, style: ParagraphStyle, label_width: float, accent: HexColor, line: HexColor,
                 gap: float = 10):
        super().__init__()
        self.title, self.style, self.label_width = title, style, label_width
        self.accent, self.line, self.gap = accent, line, gap

    def wrap(self, avail_width, avail_height):
        self.width, self.height = avail_width, self.gap
        self.para = Paragraph(escape(self.title), self.style)
        self.para_height = self.para.wrap(self.label_width - 4 * mm, avail_height)[1]
        return self.width, self.height

    def draw(self):
        c = self.canv
        x0 = -self.label_width
        c.setStrokeColor(self.line)
        c.setLineWidth(0.8)
        c.line(x0, self.gap, self.width, self.gap)
        c.setStrokeColor(self.accent)
        c.setLineWidth(2.2)
        c.line(x0, self.gap, x0 + self.label_width - 6 * mm, self.gap)
        self.para.drawOn(c, x0, -self.para_height + self.style.fontSize * 0.25)


def _doc(buffer, title: str, author: str, templates: list[PageTemplate]) -> BaseDocTemplate:
    doc = BaseDocTemplate(buffer, pagesize=A4, title=title, author=author, subject=title, creator="CV Architect")
    doc.addPageTemplates(templates)
    return doc


def _frame(x: float, y: float, w: float, h: float, frame_id: str) -> Frame:
    return Frame(x, y, w, h, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id=frame_id)


def _bullets(items: list[str], style: ParagraphStyle) -> list:
    return [_p(item, style, bulletText="•") for item in items]


def _entry_blocks(cv: ResumeData, labels: dict, st: _Styles, upper_roles: bool = False) -> dict[str, list]:
    """Blocchi di contenuto per sezione, condivisi dai layout grafici."""
    blocks: dict[str, list] = {}
    if cv.professional_summary:
        blocks["summary"] = [_p(cv.professional_summary, st.body)]
    if cv.experiences:
        flow = []
        for exp in cv.experiences:
            role = exp.role.upper() if upper_roles else exp.role
            head = [
                Paragraph(f"<b>{escape(role)}</b>, {escape(_date_range(exp.start_date, exp.end_date, labels))}",
                          st.entry_title),
                Paragraph(f"<b>{escape(exp.company)}</b>" + (f", {escape(exp.location)}" if exp.location else ""),
                          st.entry_meta),
            ]
            bullets = _bullets(exp.highlights, st.bullet)
            flow.append(KeepTogether(head + bullets[:1]))
            flow.extend(bullets[1:])
        blocks["experience"] = flow
    if cv.education:
        flow = []
        for edu in cv.education:
            dates = " – ".join(d for d in (edu.start_date, edu.end_date) if d)
            block = [Paragraph(f"<b>{escape(edu.degree)}</b>" + (f", {escape(dates)}" if dates else ""),
                               st.entry_title),
                     _p(edu.institution, st.entry_meta)]
            if edu.details:
                block.append(_p(edu.details, st.body))
            flow.append(KeepTogether(block))
        blocks["education"] = flow
    if cv.certifications:
        blocks["certifications"] = _bullets(cv.certifications, st.bullet)
    if cv.languages:
        blocks["languages"] = _bullets(cv.languages, st.bullet)
    return blocks


# Layout «Professionale a colonne»: fascia verticale, nome spaziato, banda contatti, titoli in colonna.
_LC_STRIPE, _LC_GAP, _LC_LABEL, _LC_RIGHT, _LC_V = 7 * mm, 6 * mm, 44 * mm, 14 * mm, 14 * mm


def _label_column_page(tpl: PdfTemplate):
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(tpl.accent))
        canvas.rect(0, 0, _LC_STRIPE, PAGE_H, stroke=0, fill=1)
        canvas.restoreState()
    x = _LC_STRIPE + _LC_GAP + _LC_LABEL
    frame = _frame(x, _LC_V, PAGE_W - x - _LC_RIGHT, PAGE_H - 2 * _LC_V, "content")
    return PageTemplate(id="page", frames=[frame], onPage=on_page)


def _label_column_header(cv: ResumeData, tpl: PdfTemplate, st: _Styles) -> list:
    head_f = _fonts(tpl.heading_font)
    accent, dark = HexColor(tpl.accent), HexColor("#4A4A4A")
    items = [x for x in (cv.phone, cv.location, cv.email) if x]
    return [
        Spacer(1, 4 * mm),
        _SpacedName(cv.full_name, head_f.bold, tpl.name_size, accent, dark),
        Spacer(1, 5 * mm),
        _ContactBand(items, _fonts(tpl.body_font).bold, left_bleed=_LC_LABEL + _LC_GAP,
                     first_cell=_LC_LABEL + _LC_GAP, right_bleed=_LC_RIGHT, accent=accent, dark=dark),
        Spacer(1, 6 * mm),
    ]


def _label_column_cv(cv: ResumeData, tpl: PdfTemplate, labels: dict, st: _Styles) -> list:
    head_f = _fonts(tpl.heading_font)
    accent = HexColor(tpl.accent)
    label_style = ParagraphStyle("margin_label", fontName=head_f.bold, fontSize=tpl.base_size + 3,
                                 leading=(tpl.base_size + 3) * 1.15, textColor=accent)
    blocks = _entry_blocks(cv, labels, st, upper_roles=True)
    if cv.hard_skills or cv.soft_skills:
        skills = cv.hard_skills + cv.soft_skills
        cell = ParagraphStyle("skill_cell", parent=st.bullet)
        rows = [[_p(s, cell, bulletText="•") for s in skills[i:i + 3]] for i in range(0, len(skills), 3)]
        rows[-1] += [""] * (3 - len(rows[-1]))
        table = Table(rows, colWidths=["33%", "33%", "34%"])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        blocks["skills"] = [table]
    if cv.linkedin_url:
        blocks["links"] = _bullets([cv.linkedin_url], st.bullet)

    flow = _label_column_header(cv, tpl, st)
    for key in ("summary", "skills", "links", "experience", "education", "certifications", "languages"):
        if key not in blocks:
            continue
        label = _MarginLabel(labels[key], label_style, _LC_LABEL, accent, HexColor("#555555"))
        content = blocks[key]
        # Il titolo non resta da solo in fondo alla pagina (KeepTogether annidati spostano l'intera sezione).
        flow.extend([Spacer(1, 4 * mm), CondPageBreak(30 * mm), label, Spacer(1, 2), *content])
    return flow


# Layout «Barra laterale»: colonna colorata con contatti e competenze, testo principale a destra.
_SB_WIDTH, _SB_PAD, _SB_V = 66 * mm, 8 * mm, 14 * mm


def _sidebar_page(tpl: PdfTemplate, side_items: list) -> PageTemplate:
    """Pagina con barra laterale: la barra si disegna a ogni pagina consumando `side_items`.

    La barra non è un riquadro del flusso principale: se il suo contenuto non sta in una pagina
    continua nella barra della pagina successiva, e profilo ed esperienze partono sempre dalla prima.
    """
    tint = _tint(tpl.accent, 0.88)

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(tint)
        canvas.rect(0, 0, _SB_WIDTH, PAGE_H, stroke=0, fill=1)
        canvas.setFillColor(HexColor(tpl.accent))
        canvas.rect(_SB_WIDTH - 1.5 * mm, 0, 1.5 * mm, PAGE_H, stroke=0, fill=1)
        canvas.restoreState()
        if side_items:
            before = len(side_items)
            _frame(_SB_PAD, _SB_V, _SB_WIDTH - 2 * _SB_PAD, PAGE_H - 2 * _SB_V, "side").addFromList(side_items, canvas)
            if len(side_items) == before:
                side_items.pop(0)  # più alto di una barra intera: non entrerebbe mai

    main_x = _SB_WIDTH + 9 * mm
    main = _frame(main_x, _SB_V, PAGE_W - main_x - 14 * mm, PAGE_H - 2 * _SB_V, "main")
    return PageTemplate(id="page", frames=[main], onPage=on_page)


def _sidebar_build(make_side, make_main, tpl: PdfTemplate, title: str, author: str, variants: int = 1) -> bytes:
    """Prova le varianti di distribuzione (0 = barra più piena) finché la barra entra nelle pagine
    del testo principale; con l'ultima variante aggiunge pagine finché la barra è disegnata tutta."""
    for variant in range(variants):
        for extra_pages in range(6 if variant == variants - 1 else 1):
            side = [f for item in make_side(variant) for f in
                    (item._content if isinstance(item, KeepTogether) else [item])]
            main = make_main(variant) + [PageBreak(), Spacer(1, 1)] * extra_pages
            buffer = io.BytesIO()
            _doc(buffer, title, author, [_sidebar_page(tpl, side)]).build(main)
            if not side:
                return buffer.getvalue()
    return buffer.getvalue()


def _sidebar_heading(title: str, tpl: PdfTemplate, st: _Styles) -> list:
    style = ParagraphStyle("side_heading", parent=st.heading, fontSize=tpl.base_size + 1,
                           leading=(tpl.base_size + 1) * 1.3, spaceBefore=tpl.base_size * 1.2)
    return _keep_with_next([
        _p(title.upper(), style),
        HRFlowable(width="30%", thickness=1.4, color=HexColor(tpl.accent), hAlign="LEFT", spaceAfter=4),
    ])


def _sidebar_contacts(cv: ResumeData, tpl: PdfTemplate, labels: dict, st: _Styles) -> list:
    small = ParagraphStyle("side_small", parent=st.body, fontSize=tpl.base_size - 0.5,
                           leading=(tpl.base_size - 0.5) * 1.35, spaceAfter=3)
    items = [x for x in (cv.email, cv.phone, cv.location, cv.linkedin_url) if x]
    return [*_sidebar_heading(labels["contacts"], tpl, st), *(_p(x, small) for x in items)] if items else []


# Sezioni che passano dalla barra al testo principale quando la barra non entra nella pagina.
_SIDEBAR_MOVABLE = ("certifications", "education")


def _sidebar_side(cv: ResumeData, tpl: PdfTemplate, labels: dict, st: _Styles, compact: bool = False) -> list:
    blocks = _entry_blocks(cv, labels, st)
    side = _sidebar_contacts(cv, tpl, labels, st)
    for key, skills in (("hard_skills", cv.hard_skills), ("soft_skills", cv.soft_skills)):
        if skills:
            side += [*_sidebar_heading(labels[key], tpl, st), *_bullets(skills, st.bullet)]
    for key in ("languages", "certifications", "education"):
        if key in blocks and not (compact and key in _SIDEBAR_MOVABLE):
            side += [*_sidebar_heading(labels[key], tpl, st), *blocks[key]]
    return side


def _sidebar_main(cv: ResumeData, tpl: PdfTemplate, labels: dict, st: _Styles, compact: bool = False) -> list:
    blocks = _entry_blocks(cv, labels, st)
    name = ParagraphStyle("side_name", parent=st.name, alignment=TA_LEFT)
    main = [_p(cv.full_name, name)]
    if cv.experiences:
        main.append(_p(cv.experiences[0].role, ParagraphStyle("subtitle", parent=st.contacts, alignment=TA_LEFT,
                                                              fontSize=tpl.base_size + 2)))
    for key in ("summary", "experience", *(("education", "certifications") if compact else ())):
        if key in blocks:
            main += [*_heading(labels[key], tpl, st), *blocks[key]]
    return main


# Layout «Intestazione a fascia»: banda colorata in cima alla prima pagina con nome e contatti.
_BN_HEIGHT, _BN_MARGIN = 40 * mm, 18 * mm


def _banner_pages(cv: ResumeData, tpl: PdfTemplate, st: _Styles) -> list[PageTemplate]:
    head_f, body_f = _fonts(tpl.heading_font), _fonts(tpl.body_font)
    white = HexColor("#FFFFFF")

    def on_first(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(tpl.accent))
        canvas.rect(0, PAGE_H - _BN_HEIGHT, PAGE_W, _BN_HEIGHT, stroke=0, fill=1)
        width = PAGE_W - 2 * _BN_MARGIN
        name = Paragraph(escape(cv.full_name), ParagraphStyle(
            "banner_name", fontName=head_f.bold, fontSize=tpl.name_size, leading=tpl.name_size * 1.15,
            textColor=white))
        contacts = Paragraph(escape(_contacts_line(cv)), ParagraphStyle(
            "banner_contacts", fontName=body_f.regular, fontSize=tpl.base_size - 0.5,
            leading=(tpl.base_size - 0.5) * 1.3, textColor=_tint(tpl.accent, 0.75)))
        name_h = name.wrap(width, _BN_HEIGHT)[1]
        contacts_h = contacts.wrap(width, _BN_HEIGHT)[1]
        top = PAGE_H - (_BN_HEIGHT - name_h - contacts_h - 3 * mm) / 2
        name.drawOn(canvas, _BN_MARGIN, top - name_h)
        contacts.drawOn(canvas, _BN_MARGIN, top - name_h - 3 * mm - contacts_h)
        canvas.restoreState()

    first = _frame(_BN_MARGIN, _BN_MARGIN, PAGE_W - 2 * _BN_MARGIN, PAGE_H - _BN_HEIGHT - 8 * mm - _BN_MARGIN, "first")
    later = _frame(_BN_MARGIN, _BN_MARGIN, PAGE_W - 2 * _BN_MARGIN, PAGE_H - 2 * _BN_MARGIN, "later")
    return [PageTemplate(id="first", frames=[first], onPage=on_first, autoNextPageTemplate="later"),
            PageTemplate(id="later", frames=[later])]


def _banner_heading(title: str, tpl: PdfTemplate, st: _Styles) -> list:
    style = ParagraphStyle("banner_heading", parent=st.heading, fontSize=tpl.base_size + 1,
                           leading=(tpl.base_size + 1) * 1.3)
    return _keep_with_next([
        _p(title.upper(), style),
        HRFlowable(width="100%", thickness=0.5, color=_tint(tpl.accent, 0.6), spaceAfter=4),
    ])


def _banner_cv(cv: ResumeData, tpl: PdfTemplate, labels: dict, st: _Styles) -> list:
    blocks = _entry_blocks(cv, labels, st)
    if cv.hard_skills or cv.soft_skills:
        blocks["skills"] = [Paragraph(f"<b>{escape(labels[k])}:</b> {escape(', '.join(v))}",
                                      ParagraphStyle("skills", parent=st.body, spaceAfter=3))
                            for k, v in (("hard_skills", cv.hard_skills), ("soft_skills", cv.soft_skills)) if v]
    flow = []
    for key in ("summary", "experience", "skills", "education", "certifications", "languages"):
        if key in blocks:
            flow += [*_banner_heading(labels[key], tpl, st), *blocks[key]]
    return flow


def _letter_body(cv: ResumeData, letter: str, tpl: PdfTemplate, labels: dict, st: _Styles, language: str) -> list:
    body = ParagraphStyle("letter", parent=st.body, fontSize=tpl.base_size + 0.5,
                          leading=(tpl.base_size + 0.5) * 1.45, spaceAfter=tpl.base_size * 0.9)
    today = date.today()
    month = labels["months"][today.month - 1]
    date_text = f"{today.day} {month} {today.year}" if language == "it" else f"{month} {today.day}, {today.year}"
    flow = [_p(date_text, ParagraphStyle("date", parent=body, alignment=2))]
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", letter.strip()) if p.strip()):
        flow.append(Paragraph(escape(paragraph).replace("\n", "<br/>"), body))
    # Firma solo se la lettera non si chiude già con il nome del candidato.
    last_line = letter.strip().splitlines()[-1].strip() if letter.strip() else ""
    if last_line.lower() != cv.full_name.strip().lower():
        flow.extend([Spacer(1, 6), _p(cv.full_name, body)])
    return flow


def render_cv_pdf(cv: ResumeData, template: str = "moderno", language: str = "it") -> bytes:
    tpl, labels = TEMPLATES[template], _LABELS[language]
    st = _styles(tpl)
    title = f"CV - {cv.full_name}"
    if tpl.layout == "single":
        flow = _header(cv, st)
        for key in tpl.section_order:
            flow.extend(_section(key, cv, tpl, st, labels))
        return _build(flow, tpl, title=title, author=cv.full_name)

    if tpl.layout == "sidebar":
        return _sidebar_build(lambda v: _sidebar_side(cv, tpl, labels, st, compact=v == 1),
                              lambda v: _sidebar_main(cv, tpl, labels, st, compact=v == 1),
                              tpl, title, cv.full_name, variants=2)
    buffer = io.BytesIO()
    if tpl.layout == "label_column":
        pages, flow = [_label_column_page(tpl)], _label_column_cv(cv, tpl, labels, st)
    else:
        pages, flow = _banner_pages(cv, tpl, st), _banner_cv(cv, tpl, labels, st)
    _doc(buffer, title, cv.full_name, pages).build(flow)
    return buffer.getvalue()


def render_cover_letter_pdf(
    cv: ResumeData, letter: str, template: str = "moderno", language: str = "it"
) -> bytes:
    tpl, labels = TEMPLATES[template], _LABELS[language]
    st = _styles(tpl)
    title = f"{labels['cover_letter']} - {cv.full_name}"
    body = _letter_body(cv, letter, tpl, labels, st, language)
    if tpl.layout == "single":
        flow = _header(cv, st)
        flow.append(HRFlowable(width="100%", thickness=0.6, color=HexColor(tpl.accent), spaceBefore=6,
                               spaceAfter=14))
        return _build(flow + body, tpl, title=title, author=cv.full_name)

    if tpl.layout == "sidebar":
        name = ParagraphStyle("side_name", parent=st.name, alignment=TA_LEFT)
        return _sidebar_build(
            lambda v: _sidebar_contacts(cv, tpl, labels, st),
            lambda v: [_p(cv.full_name, name), Spacer(1, 8 * mm), *_letter_body(cv, letter, tpl, labels, st, language)],
            tpl, title, cv.full_name,
        )
    buffer = io.BytesIO()
    if tpl.layout == "label_column":
        pages, flow = [_label_column_page(tpl)], _label_column_header(cv, tpl, st) + body
    else:
        pages, flow = _banner_pages(cv, tpl, st), body
    _doc(buffer, title, cv.full_name, pages).build(flow)
    return buffer.getvalue()
