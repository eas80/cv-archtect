"""Editor manuali per CV, lettera e testi LinkedIn.

L'editor del CV salva da solo: ogni widget ha un callback `on_change` che ricostruisce
il CV dai valori in session_state e lo passa a `on_save`. I callback girano prima dello
script, quindi una modifica non va persa nemmeno se l'utente cambia subito passo.
Lettera e LinkedIn sono testi semplici e usano invece un `st.form` con «Salva».

Le chiavi dei widget includono una revisione: va incrementata con `bump()` ogni volta
che il contenuto viene sostituito (nuova generazione, aggiunta, eliminazione),
altrimenti Streamlit continuerebbe a mostrare i valori precedenti.
"""

import re
from datetime import date
from typing import Callable

import streamlit as st

from schemas import Education, Experience, LinkedInContent, ResumeData


def bump(key: str) -> None:
    st.session_state[f"{key}_rev"] = st.session_state.get(f"{key}_rev", 0) + 1


def _key_fn(key: str):
    rev = st.session_state.get(f"{key}_rev", 0)
    return lambda name: f"edit_{key}_{rev}_{name}"


def _lines(text: str) -> list[str]:
    """Una voce per riga; rimuove eventuali punti elenco incollati a inizio riga."""
    return [re.sub(r"^[•\-*·]\s*", "", line).strip() for line in text.splitlines() if line.strip()]


def _opt(text: str) -> str | None:
    return text.strip() or None


# --- Date ----------------------------------------------------------------------------
_PRESENT = {"present", "presente", "in corso", "attuale", "oggi"}
_NONE = "—"
_MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
           "settembre", "ottobre", "novembre", "dicembre")


def _date_key(value: str | None, empty_is_present: bool = False) -> tuple[int, int]:
    """Chiave di ordinamento per date 'MM/YYYY' o 'YYYY'; date non riconosciute in fondo."""
    value = (value or "").strip().lower()
    if not value:
        return (9999, 12) if empty_is_present else (0, 0)
    if value in _PRESENT:
        return (9999, 12)
    if m := re.search(r"(\d{1,2})[/.\-](\d{4})", value):
        return int(m.group(2)), int(m.group(1))
    if m := re.search(r"\d{4}", value):
        return int(m.group()), 0
    return (0, 0)


def _parse_date(value: str | None) -> tuple[int | None, int | None]:
    """(mese, anno) da 'MM/YYYY' o 'YYYY'; (None, None) se il formato non è riconosciuto."""
    value = (value or "").strip()
    if m := re.fullmatch(r"(\d{1,2})[/.\-](\d{4})", value):
        month = int(m.group(1))
        return (month if 1 <= month <= 12 else None), int(m.group(2))
    if re.fullmatch(r"\d{4}", value):
        return None, int(value)
    return None, None


def _is_present(value: str | None) -> bool:
    return (value or "").strip().lower() in _PRESENT


def _date_widgets(label: str, value: str | None, kk: str, on_change, disabled: bool = False) -> None:
    """Due menu affiancati (mese facoltativo, anno) per una data."""
    month, year = _parse_date(value)
    # Fino a +6 anni per le date di fine previste (es. lauree in corso).
    years = list(range(date.today().year + 6, 1959, -1))
    if year and year not in years:
        years = sorted({*years, year}, reverse=True)
    c1, c2 = st.columns(2)
    c1.selectbox(
        f"Mese {label}", [_NONE, *range(1, 13)], index=month or 0, key=f"{kk}_m", on_change=on_change,
        format_func=lambda m: m if m == _NONE else _MONTHS[m - 1].capitalize(), disabled=disabled,
    )
    c2.selectbox(
        f"Anno {label}", [_NONE, *years], index=years.index(year) + 1 if year else 0, key=f"{kk}_y",
        on_change=on_change, disabled=disabled,
    )
    if value and year is None and not _is_present(value):
        st.caption(f"Data letta dal CV: «{value}». Sceglila dai menu per correggerla.")


def _date_value(kk: str, original: str | None) -> str | None:
    """Data 'MM/YYYY' o 'YYYY' dai menu; senza anno scelto conserva l'originale non riconosciuto."""
    month0, year0 = _parse_date(original)
    year = st.session_state.get(f"{kk}_y", year0 or _NONE)
    month = st.session_state.get(f"{kk}_m", month0 or _NONE)
    if year == _NONE:
        unparsed = bool(original) and year0 is None and not _is_present(original)
        return original if unparsed else None
    return f"{month:02d}/{year}" if month != _NONE else str(year)


def _exp_is_current(exp: Experience) -> bool:
    # Senza data di fine il ruolo viene mostrato come in corso, ovunque nell'app.
    return exp.end_date is None or _is_present(exp.end_date)


# --- Lettura dei valori dai widget ----------------------------------------------------------
def _read_experience(k, prefix: str, exp: Experience) -> Experience:
    def get(name, default):
        return st.session_state.get(k(f"{prefix}_{name}"), default)

    current = get("current", _exp_is_current(exp))
    return Experience(
        role=get("role", exp.role).strip(),
        company=get("company", exp.company).strip(),
        start_date=_date_value(k(f"{prefix}_start"), exp.start_date) or "",
        end_date="Present" if current else _date_value(k(f"{prefix}_end"), exp.end_date),
        location=_opt(get("location", exp.location or "")),
        highlights=_lines(get("hl", "\n".join(exp.highlights))),
    )


def _read_education(k, prefix: str, edu: Education) -> Education:
    def get(name, default):
        return st.session_state.get(k(f"{prefix}_{name}"), default)

    return Education(
        degree=get("degree", edu.degree).strip(),
        institution=get("inst", edu.institution).strip(),
        start_date=_date_value(k(f"{prefix}_start"), edu.start_date),
        end_date=_date_value(k(f"{prefix}_end"), edu.end_date),
        details=_opt(get("details", edu.details or "")),
    )


def _read_resume(cv: ResumeData, k) -> ResumeData:
    def get(name, default):
        return st.session_state.get(k(name), default)

    return ResumeData(
        full_name=get("name", cv.full_name).strip(),
        email=_opt(get("email", cv.email or "")),
        phone=_opt(get("phone", cv.phone or "")),
        location=_opt(get("location", cv.location or "")),
        linkedin_url=_opt(get("linkedin", cv.linkedin_url or "")),
        professional_summary=get("summary", cv.professional_summary).strip(),
        experiences=[_read_experience(k, f"exp{i}", e) for i, e in enumerate(cv.experiences)],
        education=[_read_education(k, f"edu{i}", e) for i, e in enumerate(cv.education)],
        hard_skills=_lines(get("hard", "\n".join(cv.hard_skills))),
        soft_skills=_lines(get("soft", "\n".join(cv.soft_skills))),
        certifications=_lines(get("certs", "\n".join(cv.certifications))),
        languages=_lines(get("langs", "\n".join(cv.languages))),
    )


def _sorted(cv: ResumeData) -> ResumeData:
    experiences = sorted(
        cv.experiences,
        key=lambda e: (_date_key(e.end_date, empty_is_present=True), _date_key(e.start_date)),
        reverse=True,
    )
    education = sorted(cv.education, key=lambda e: _date_key(e.end_date or e.start_date), reverse=True)
    return cv.model_copy(update={"experiences": experiences, "education": education})


# --- Blocchi dell'editor ----------------------------------------------------------------
_EMPTY_EXP = Experience(role="", company="", start_date="", end_date="")
_EMPTY_EDU = Education(degree="", institution="")


def _experience_fields(k, prefix: str, exp: Experience, on_change) -> None:
    a, b = st.columns(2)
    a.text_input("Ruolo *", exp.role, key=k(f"{prefix}_role"), on_change=on_change,
                 placeholder="es. Responsabile amministrativo")
    b.text_input("Azienda *", exp.company, key=k(f"{prefix}_company"), on_change=on_change,
                 placeholder="es. Rossi S.p.A.")
    current_key = k(f"{prefix}_current")
    current = st.session_state.get(current_key, _exp_is_current(exp))
    start, end = st.columns(2)
    with start:
        _date_widgets("di inizio", exp.start_date, k(f"{prefix}_start"), on_change)
    with end:
        _date_widgets("di fine", None if _exp_is_current(exp) else exp.end_date, k(f"{prefix}_end"),
                      on_change, disabled=current)
    a, b = st.columns(2)
    a.checkbox("Ci lavoro ancora", value=current, key=current_key, on_change=on_change)
    b.text_input("Città (facoltativa)", exp.location or "", key=k(f"{prefix}_location"), on_change=on_change,
                 placeholder="es. Milano, oppure Da remoto")
    st.text_area(
        "Cosa hai fatto e ottenuto (una frase per riga)",
        "\n".join(exp.highlights), height=130, key=k(f"{prefix}_hl"), on_change=on_change,
        placeholder="es. Ridotto i tempi di chiusura del bilancio da 15 a 8 giorni con un nuovo gestionale\n"
                    "es. Coordinato un gruppo di 4 persone nella gestione dei fornitori",
        help="Parti da un verbo (gestito, ridotto, avviato...) e, se puoi, indica un risultato o un numero.",
    )


def _education_fields(k, prefix: str, edu: Education, on_change) -> None:
    a, b = st.columns(2)
    a.text_input("Titolo di studio *", edu.degree, key=k(f"{prefix}_degree"), on_change=on_change,
                 placeholder="es. Laurea in Economia aziendale")
    b.text_input("Scuola o università *", edu.institution, key=k(f"{prefix}_inst"), on_change=on_change,
                 placeholder="es. Università di Bologna")
    start, end = st.columns(2)
    with start:
        _date_widgets("di inizio", edu.start_date, k(f"{prefix}_start"), on_change)
    with end:
        _date_widgets("di fine (o prevista)", edu.end_date, k(f"{prefix}_end"), on_change)
    st.text_input("Dettagli (facoltativi)", edu.details or "", key=k(f"{prefix}_details"), on_change=on_change,
                  placeholder="es. 110/110, tesi sul controllo di gestione")


def _add_panel(k, kind: str, button_label: str, title: str, render_fields, on_add) -> None:
    """Pulsante «Aggiungi» che apre una scheda con i campi del nuovo elemento."""
    panel = k(f"show_new_{kind}")

    def set_panel(show: bool) -> None:
        st.session_state[panel] = show

    if not st.session_state.get(panel, False):
        st.button(button_label, key=k(f"open_{kind}"), on_click=set_panel, args=(True,))
        return
    with st.container(border=True):
        st.markdown(f"**{title}**")
        render_fields()
        if error := st.session_state.pop(k(f"new_{kind}_error"), None):
            st.error(error)
        a, b = st.columns(2)
        a.button("Aggiungi al CV", type="primary", use_container_width=True, key=k(f"add_{kind}"), on_click=on_add)
        b.button("Annulla", use_container_width=True, key=k(f"cancel_{kind}"), on_click=set_panel, args=(False,))


def _item_header(title: str, delete_key: str, on_delete, args: tuple) -> None:
    head, delete = st.columns([3, 1])
    head.markdown(f"**{title}**")
    with delete.popover("🗑️ Elimina", use_container_width=True):
        st.markdown(f"Vuoi eliminare **{title}**?")
        st.button("Sì, elimina", key=delete_key, type="primary", on_click=on_delete, args=args)


def edit_resume(
    cv: ResumeData,
    key: str,
    on_save: Callable[[ResumeData], None],
    on_done: Callable[[], None] | None = None,
) -> None:
    """Editor del CV con salvataggio automatico; `on_done` viene chiamato da «Ho finito»."""
    k = _key_fn(key)

    def autosave() -> None:
        on_save(_read_resume(cv, k))

    def replace(new_cv: ResumeData, message: str) -> None:
        on_save(_sorted(new_cv))
        bump(key)
        st.toast(message)

    def add_experience() -> None:
        item = _read_experience(k, "new_exp", _EMPTY_EXP)
        if not (item.role and item.company):
            st.session_state[k("new_exp_error")] = "Inserisci almeno ruolo e azienda."
            return
        current = _read_resume(cv, k)
        replace(current.model_copy(update={"experiences": [*current.experiences, item]}), "Esperienza aggiunta.")

    def add_education() -> None:
        item = _read_education(k, "new_edu", _EMPTY_EDU)
        if not (item.degree and item.institution):
            st.session_state[k("new_edu_error")] = "Inserisci almeno titolo di studio e scuola."
            return
        current = _read_resume(cv, k)
        replace(current.model_copy(update={"education": [*current.education, item]}), "Titolo di studio aggiunto.")

    def delete_item(field: str, index: int) -> None:
        current = _read_resume(cv, k)
        items = [x for i, x in enumerate(getattr(current, field)) if i != index]
        replace(current.model_copy(update={field: items}), "Eliminato.")

    def done() -> None:
        on_save(_sorted(_read_resume(cv, k)))
        bump(key)
        st.toast("Modifiche salvate.")
        if on_done:
            on_done()

    st.caption("Le modifiche si salvano da sole mentre scrivi. I campi con * sono obbligatori.")

    st.markdown("##### 👤 Dati personali")
    st.text_input("Nome e cognome *", cv.full_name, key=k("name"), on_change=autosave)
    c1, c2 = st.columns(2)
    c1.text_input("Email", cv.email or "", key=k("email"), on_change=autosave)
    c2.text_input("Telefono", cv.phone or "", key=k("phone"), on_change=autosave)
    c1.text_input("Città", cv.location or "", key=k("location"), on_change=autosave)
    c2.text_input("Link al profilo LinkedIn", cv.linkedin_url or "", key=k("linkedin"), on_change=autosave)
    st.text_area("Profilo (presentazione in 3-4 righe)", cv.professional_summary, height=120,
                 key=k("summary"), on_change=autosave)

    st.markdown("##### 💼 Esperienze di lavoro")
    _add_panel(k, "exp", "➕ Aggiungi un'esperienza", "Nuova esperienza",
               lambda: _experience_fields(k, "new_exp", _EMPTY_EXP, None), on_add=add_experience)
    if not cv.experiences:
        st.caption("Nessuna esperienza inserita.")
    for i, exp in enumerate(cv.experiences):
        with st.container(border=True):
            title = f"{exp.role} — {exp.company}" if exp.role else "Esperienza senza titolo"
            _item_header(title, k(f"exp{i}_delete"), delete_item, ("experiences", i))
            _experience_fields(k, f"exp{i}", exp, autosave)

    st.markdown("##### 🎓 Formazione")
    _add_panel(k, "edu", "➕ Aggiungi un titolo di studio", "Nuovo titolo di studio",
               lambda: _education_fields(k, "new_edu", _EMPTY_EDU, None), on_add=add_education)
    if not cv.education:
        st.caption("Nessun titolo di studio inserito.")
    for i, edu in enumerate(cv.education):
        with st.container(border=True):
            title = f"{edu.degree} — {edu.institution}" if edu.degree else "Titolo senza nome"
            _item_header(title, k(f"edu{i}_delete"), delete_item, ("education", i))
            _education_fields(k, f"edu{i}", edu, autosave)

    st.markdown("##### 🛠️ Competenze, certificazioni e lingue")
    st.caption("Una voce per riga.")
    a, b = st.columns(2)
    a.text_area("Competenze tecniche", "\n".join(cv.hard_skills), height=160, key=k("hard"), on_change=autosave,
                placeholder="es. Excel avanzato\nSAP")
    b.text_area("Competenze trasversali", "\n".join(cv.soft_skills), height=160, key=k("soft"),
                on_change=autosave, placeholder="es. Gestione delle priorità")
    a.text_area("Certificazioni", "\n".join(cv.certifications), height=110, key=k("certs"), on_change=autosave)
    b.text_area("Lingue", "\n".join(cv.languages), height=110, key=k("langs"), on_change=autosave,
                placeholder="es. Inglese (B2)")

    st.button("✅ Ho finito di correggere", type="primary", use_container_width=True, key=k("done"), on_click=done)


# --- Lettera e LinkedIn ---------------------------------------------------------------
def edit_text(text: str, key: str, label: str, height: int = 400) -> str | None:
    k = _key_fn(key)
    with st.form(k("form")):
        new_text = st.text_area(label, text, height=height, key=k("text"))
        if st.form_submit_button("Salva modifiche", type="primary"):
            return new_text.strip()
    return None


def edit_linkedin(content: LinkedInContent, key: str) -> LinkedInContent | None:
    k = _key_fn(key)
    with st.form(k("form")):
        headline = st.text_input("Headline (max 220 caratteri)", content.headline, key=k("headline"))
        about = st.text_area("Sezione About (max 2600 caratteri)", content.about_section, height=320, key=k("about"))
        post = st.text_area("Post di lancio", content.launch_post, height=240, key=k("post"))
        if st.form_submit_button("Salva modifiche", type="primary"):
            return LinkedInContent(
                headline=headline.strip(), about_section=about.strip(), launch_post=post.strip()
            )
    return None
