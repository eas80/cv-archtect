"""CV Architect — percorso guidato: CV, offerta di lavoro, profilo LinkedIn."""

import hashlib
import html
import io
import re
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st
from jinja2 import Template
from pypdf import PdfReader
from pypdf.errors import PyPdfError

import config
import seo
from editors import bump, edit_linkedin, edit_resume, edit_text
from schemas import JobMatchResult, LinkedInContent, ResumeData
from services import (
    LANGUAGES,
    LINKEDIN_TONES,
    REVISION_SCOPES,
    TEMPLATES,
    ClaudeCVService,
    applications_service,
    auth_service,
    email_service,
    payment_service,
    ClaudeServiceError,
    ScraperError,
    extract_job_description_from_url,
    guess_language,
    pdf_page_images,
    render_cover_letter_pdf,
    render_cv_pdf,
)

st.set_page_config(
    page_title=seo.TITLE,
    page_icon="assets/icon-180.png",
    layout="centered",
)

CV_MARKDOWN_TEMPLATE = Template(
    """## {{ cv.full_name }}
{{ [cv.email, cv.phone, cv.location, cv.linkedin_url] | select | join(" · ") }}

#### Profilo
{{ cv.professional_summary }}

{% if cv.experiences %}
#### Esperienza

{% for exp in cv.experiences %}
**{{ exp.role }} — {{ exp.company }}**
{{ exp.start_date }} – {{ exp.end_date or "Present" }}{{ " · " ~ exp.location if exp.location else "" }}

{% for item in exp.highlights %}
- {{ item }}
{% endfor %}

{% endfor %}
{% endif %}
{% if cv.education %}
#### Formazione

{% for edu in cv.education %}
- **{{ edu.degree }}**, {{ edu.institution }}{{ " (" ~ ([edu.start_date, edu.end_date] | select | join(" – ")) ~ ")" if edu.start_date or edu.end_date else "" }}{{ " — " ~ edu.details if edu.details else "" }}
{% endfor %}

{% endif %}
{% if cv.hard_skills %}
#### Competenze tecniche
{{ cv.hard_skills | join(", ") }}

{% endif %}
{% if cv.soft_skills %}
#### Competenze trasversali
{{ cv.soft_skills | join(", ") }}

{% endif %}
{% if cv.certifications %}
#### Certificazioni

{% for cert in cv.certifications %}
- {{ cert }}
{% endfor %}

{% endif %}
{% if cv.languages %}
#### Lingue
{{ cv.languages | join(", ") }}
{% endif %}
""",
    trim_blocks=True,
)

STEP_TITLES = ("Il tuo CV", "Offerta di lavoro", "Profilo LinkedIn")

TONE_DESCRIPTIONS = {
    "risultati": "mette in evidenza numeri e risultati concreti.",
    "diplomatico": "tono positivo e rispettoso: non parla di problemi, errori o situazioni che possano "
    "mettere in cattiva luce colleghi, responsabili o ex aziende.",
    "narrativo": "racconta il tuo percorso in modo più personale.",
    "essenziale": "testi brevi e diretti.",
}

STEPPER_CSS = """
<style>
.cv-stepper { display: flex; align-items: flex-start; margin: 0.25rem 0 1.75rem; }
.cv-step { display: flex; flex-direction: column; align-items: center; gap: 0.4rem; width: 7.5rem;
           text-align: center; }
.cv-dot { width: 2.2rem; height: 2.2rem; border-radius: 50%; display: flex; align-items: center;
          justify-content: center; font-weight: 700; border: 2px solid rgba(128,128,128,0.45);
          color: rgba(128,128,128,0.95); }
.cv-label { font-size: 0.85rem; line-height: 1.2; opacity: 0.75; }
.cv-step.current .cv-dot { background: #2563EB; border-color: #2563EB; color: #fff; }
.cv-step.current .cv-label { font-weight: 700; opacity: 1; }
.cv-step.done .cv-dot { background: #16A34A; border-color: #16A34A; color: #fff; }
.cv-step.skipped .cv-dot { border-style: dashed; }
.cv-bar { flex: 1; height: 3px; margin-top: 1.1rem; border-radius: 2px; background: rgba(128,128,128,0.3); }
.cv-bar.done { background: #16A34A; }
@media (max-width: 480px) {
  .cv-label { display: none; }
  .cv-stepper { margin-bottom: 1rem; }
}
.cv-brand { font-size: 1.25rem; font-weight: 700; white-space: nowrap; }
.cv-test-banner { background: #F59E0B; color: #1F2937; text-align: center; font-size: 0.85rem;
    padding: 0.35rem 0.75rem; border-radius: 0.5rem; }
/* Barra in alto: Streamlit la rende scorrevole e, con il logo più alto della riga, comparirebbe
   una barra di scorrimento accanto al menu ☰. */
.st-key-topbar, .st-key-topbar [data-testid="stHorizontalBlock"] { overflow: visible !important; }

/* Menu ☰: pulsante senza freccia, pannello a elenco con voci allineate a sinistra. */
.st-key-topbar [data-testid="stPopoverButton"] div[aria-hidden="true"] { display: none; }
.st-key-menu_panel { width: 270px; max-width: 80vw; gap: 0.1rem; }
.st-key-menu_panel button[kind="tertiary"] { width: 100%; justify-content: flex-start; padding: 0.45rem 0.7rem;
    border-radius: 0.5rem; min-height: 0; color: inherit; }
.st-key-menu_panel button[kind="tertiary"] > div { justify-content: flex-start; }
.st-key-menu_panel button[kind="tertiary"]:hover { background: rgba(128,128,128,0.14); color: inherit; }
.st-key-menu_panel [class*="st-key-menu_cur_"] button { background: rgba(37,99,235,0.14); color: #2563EB;
    font-weight: 600; }
.st-key-menu_panel .st-key-menu_logout button { color: #DC2626; }
.st-key-menu_panel .st-key-menu_logout button:hover { background: rgba(220,38,38,0.1); color: #DC2626; }
.cv-menu-head { display: flex; align-items: center; gap: 0.7rem; padding: 0.2rem 0.3rem 0.8rem;
    border-bottom: 1px solid rgba(128,128,128,0.25); }
.cv-avatar { width: 2.4rem; height: 2.4rem; border-radius: 50%; background: #2563EB; color: #fff; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1.05rem; }
.cv-menu-email { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 190px; }
.cv-menu-sub { font-size: 0.8rem; opacity: 0.7; }
.cv-menu-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.6;
    padding: 0.85rem 0.7rem 0.15rem; }
.cv-menu-sep { border-top: 1px solid rgba(128,128,128,0.25); margin: 0.4rem 0 0.2rem; }
/* I blocchi HTML del menu non devono sovrapporsi alle voci (Streamlit riduce il margine del markdown). */
.st-key-menu_panel [data-testid="stMarkdownContainer"], .st-key-menu_panel .stMarkdown { margin: 0 !important; }
.st-key-menu_panel .cv-menu-label { margin-top: 0.5rem; }
</style>
"""


# --- Stato ------------------------------------------------------------------------
# Stato legato all'annuncio: va azzerato insieme al risultato del passo 2.
# saved_app_id: candidatura dell'archivio che corrisponde all'annuncio attuale.
# reopened: (titolo, nuova versione?) della candidatura riaperta dall'archivio.
JOB_KEYS = ("match", "match_prev", "job_text", "saved_app_id", "reopened")


# Sezioni dell'app, scelte dal menu ☰.
GUIDED_VIEW, ARCHIVE_VIEW, ADMIN_VIEW = "guided", "archive", "admin"


def init_state() -> None:
    for key in ("cv", "cv_source_text", "linkedin", *JOB_KEYS):
        st.session_state.setdefault(key, None)
    st.session_state.setdefault("step", 1)
    st.session_state.setdefault("job_skipped", False)
    st.session_state.setdefault("view", GUIDED_VIEW)
    st.session_state.setdefault("session_paid", False)
    st.session_state.setdefault("session_ai_calls", 0)
    st.session_state.setdefault("session_done", [])
    # La candidatura in corso è una nuova versione di una già pagata (il titolo nell'archivio lo dice).
    st.session_state.setdefault("new_version", False)
    # Salvataggio automatico (autosave_application): autosave_cv permette di salvare una bozza con il solo
    # CV appena caricato; autosave_off lo sospende dopo che la candidatura in corso è stata eliminata.
    st.session_state.setdefault("autosave_cv", False)
    st.session_state.setdefault("autosave_off", False)


def start_session(ai_calls: int = 0) -> None:
    """Inizia una nuova candidatura (CV + annuncio + LinkedIn), che vale 1 credito.

    Il credito si scala alla prima consegna (vedi charge_session); `ai_calls` conta le operazioni
    con l'AI già fatte per questa candidatura.
    """
    st.session_state.session_paid = False
    st.session_state.session_ai_calls = ai_calls
    st.session_state.session_done = []


def reset_session() -> None:
    for key in ("cv", "cv_source_text", "linkedin", *JOB_KEYS):
        st.session_state[key] = None
    st.session_state.step = 1
    st.session_state.job_skipped = False
    st.session_state.view = GUIDED_VIEW
    st.session_state.autosave_cv = False
    st.session_state.autosave_off = False
    st.session_state.new_version = False
    start_session()
    request_scroll_top()


def new_application() -> None:
    """Nuova candidatura con lo stesso CV: si azzera solo ciò che dipende dall'annuncio.

    I testi LinkedIn restano: il profilo è uno solo e vale per tutte le candidature.
    """
    for key in JOB_KEYS:
        st.session_state[key] = None
    st.session_state.job_skipped = False
    st.session_state.view = GUIDED_VIEW
    # Lo stesso CV è già nell'archivio: la nuova candidatura si salva quando c'è l'annuncio.
    st.session_state.autosave_cv = False
    st.session_state.autosave_off = False
    st.session_state.new_version = False
    start_session()
    go_to(2)


def step_done(step: int) -> bool:
    state = st.session_state
    return bool([state.cv, state.match, state.linkedin][step - 1])


def step_unlocked(step: int) -> bool:
    state = st.session_state
    if step == 1:
        return True
    if step == 2:
        return state.cv is not None
    return state.cv is not None and (state.match is not None or state.job_skipped)


def go_to(step: int) -> None:
    st.session_state.step = step
    request_scroll_top()


def skip_job() -> None:
    st.session_state.job_skipped = True
    go_to(3)


def request_scroll_top() -> None:
    """Al prossimo giro la pagina torna in cima: Streamlit altrimenti conserva lo scorrimento."""
    st.session_state.scroll_top = True


def emit_scroll_script() -> None:
    if st.session_state.pop("scroll_top", False):
        # .st-key-topbar è la classe che Streamlit assegna al container con key="topbar".
        st.html("<script>document.querySelector('.st-key-topbar')?.scrollIntoView({block: 'start'});</script>",
                unsafe_allow_javascript=True)


def edit_toggle(label: str, key: str) -> bool:
    # Lo stato di un widget si può cambiare solo prima di disegnarlo: la chiusura
    # dopo un salvataggio viene quindi prenotata con close_toggle e applicata qui.
    if st.session_state.pop(f"{key}_close", False):
        st.session_state[key] = False
    return st.toggle(label, key=key)


def close_toggle(key: str) -> None:
    st.session_state[f"{key}_close"] = True


# --- Elementi comuni ---------------------------------------------------------------
def read_uploaded_file(uploaded) -> str:
    """Estrae il testo da un file TXT o PDF caricato."""
    data = uploaded.getvalue()
    if uploaded.name.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()


# --- Accesso e crediti ----------------------------------------------------------------
SESSION_COOKIE = "cv_architect_session"


def restore_login() -> None:
    """Ricarica della pagina = nuova sessione Streamlit: l'accesso si recupera dal cookie.

    Si prova una sola volta per sessione: st.context.cookies resta quello di apertura
    della pagina, quindi dopo «Esci» conterrebbe ancora il vecchio token.
    """
    if st.session_state.get("login_restore_tried"):
        return
    st.session_state.login_restore_tried = True
    token = st.context.cookies.get(SESSION_COOKIE)
    if (user := auth_service.user_from_login_session(token)) is not None:
        st.session_state.user_id = user.id
        st.session_state.session_token = token


def current_base_url() -> str:
    """Indirizzo dell'app così come lo vede il browser (es. https://cv-architect.onrender.com).

    Serve per il ritorno da Stripe e per il cookie: non dipende da APP_BASE_URL, che può mancare
    o essere sbagliata. Se l'indirizzo della pagina non è disponibile si usa APP_BASE_URL.
    """
    parts = urlsplit(st.context.url or "")
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return config.APP_BASE_URL


def set_session_cookie(token: str | None) -> None:
    """Prenota la scrittura (o, con None, la cancellazione) del cookie: la fa emit_cookie_script."""
    secure = "; Secure" if current_base_url().startswith("https://") else ""
    max_age = config.SESSION_DAYS * 86400 if token else 0
    st.session_state.cookie_script = (
        f"<script>document.cookie = '{SESSION_COOKIE}={token or ''}; Max-Age={max_age}; Path=/; "
        f"SameSite=Lax{secure}';</script>"
    )


def emit_cookie_script() -> None:
    if script := st.session_state.pop("cookie_script", None):
        st.html(script, unsafe_allow_javascript=True)


def current_user() -> auth_service.User | None:
    user_id = st.session_state.get("user_id")
    user = auth_service.get_user(user_id) if user_id else None
    if user is None:
        st.session_state.user_id = None
    return user


def sign_in(user: auth_service.User) -> None:
    reset_session()
    st.session_state.user_id = user.id
    st.session_state.session_token = auth_service.create_login_session(user.id)
    set_session_cookie(st.session_state.session_token)


def sign_out() -> None:
    reset_session()
    auth_service.delete_login_session(st.session_state.get("session_token"))
    set_session_cookie(None)
    for key in ("user_id", "session_token", "checkout", "known_credits", "verify_send_error", "auth_mode"):
        st.session_state[key] = None


def send_verification_code(user_id: int) -> None:
    """Invia il codice di conferma; un eventuale errore resta visibile nella schermata di conferma."""
    try:
        auth_service.send_verification_code(user_id)
        st.session_state.verify_send_error = None
    except (auth_service.AuthError, email_service.EmailError) as exc:
        st.session_state.verify_send_error = str(exc)


def render_verify_email(user: auth_service.User) -> None:
    """Schermata mostrata finché l'email non è confermata: il resto dell'app resta bloccato."""
    with st.container(border=True):
        st.subheader("📧 Conferma il tuo indirizzo email")
        st.write(f"Ti abbiamo inviato un codice di 6 cifre a **{user.email}**. Inseriscilo qui sotto per "
                 "iniziare a usare l'app.")
        if error := st.session_state.get("verify_send_error"):
            st.error(error)
        with st.form("verify_form", border=False):
            code = st.text_input("Codice", max_chars=7, placeholder="123456", key="verify_code")
            if st.form_submit_button("Conferma", type="primary", use_container_width=True):
                try:
                    auth_service.verify_email(user.id, code)
                    st.session_state.pending_toast = "Email confermata: benvenuto!"
                    st.rerun()
                except auth_service.AuthError as exc:
                    st.error(str(exc))
        st.caption(f"Il codice scade dopo {config.EMAIL_CODE_MINUTES} minuti. Non lo trovi? Controlla anche "
                   "nella cartella spam.")
        with st.container(horizontal=True):
            if st.button("Inviami un nuovo codice", key="verify_resend"):
                send_verification_code(user.id)
                if not st.session_state.get("verify_send_error"):
                    st.session_state.pending_toast = "Nuovo codice inviato."
                st.rerun()
            st.button("Email sbagliata? Esci e registrati di nuovo", key="verify_signout", type="tertiary",
                      on_click=sign_out)


def render_welcome_page() -> None:
    """Pagina per chi non ha fatto l'accesso: cosa fa l'app, poi accesso o registrazione."""
    if st.session_state.get("auth_mode") == "reset":
        st.button("← Torna all'accesso", key="reset_back", type="tertiary",
                  on_click=lambda: st.session_state.update(auth_mode=None, reset_email_sent=None))
        with st.container(border=True):
            st.subheader("🔑 Password dimenticata")
            render_password_reset()
        return

    st.markdown("## Il tuo CV su misura per ogni annuncio")
    st.markdown("Carica il curriculum una volta: l'AI lo adatta a ogni offerta di lavoro, lo rende leggibile "
                "dai sistemi di selezione (ATS), scrive la lettera di presentazione e prepara il tuo profilo "
                f"LinkedIn. Ti candidi all'estero? Traduce CV e lettera in inglese. Senza abbonamento, "
                f"da {seo.price_from()} a candidatura.")
    with st.container(horizontal=True, gap="medium"):
        for icon, title, text in (("📄", "1. Carica il CV", "PDF o testo, lo riordiniamo noi"),
                                  ("🎯", "2. Scegli l'annuncio", "compatibilità in %, CV adattato e lettera"),
                                  ("💼", "3. LinkedIn", "titolo, Informazioni e post"),
                                  ("🌍", "Anche in inglese", "CV e lettera tradotti con l'AI")):
            with st.container(border=True):
                st.markdown(f"**{icon} {title}**")
                st.caption(text)
    st.button("👀 Guarda come funziona, con esempi", type="tertiary", key="welcome_guide", on_click=open_guide_page)
    with st.container(border=True):
        render_auth_forms()
    st.markdown("#### Domande frequenti")
    for question, answer in seo.faq():
        with st.expander(question):
            st.markdown(answer)
    st.button("✉️ Hai domande? Contattaci", type="tertiary", key="welcome_contact", on_click=open_contact_dialog)
    st.caption(legal_links())


def render_auth_forms() -> None:
    login_tab, register_tab = st.tabs(["Accedi", "Crea account"], key="auth_tab")

    with login_tab:
        with st.form("login_form", border=False):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Accedi", type="primary", use_container_width=True):
                try:
                    sign_in(auth_service.login(email, password))
                    st.rerun()
                except auth_service.AuthError as exc:
                    st.error(str(exc))
        st.button("Password dimenticata?", key="forgot_password", type="tertiary",
                  on_click=lambda: st.session_state.update(auth_mode="reset"))

    with register_tab, st.form("register_form", border=False):
        if config.INITIAL_CREDITS:
            st.caption(f"Ogni nuovo account riceve {config.INITIAL_CREDITS} crediti.")
        elif free_mode_enabled():
            st.caption("La registrazione è gratuita: puoi sistemare il CV a mano e acquistare crediti per l'AI.")
        else:
            st.caption(f"Registrazione gratuita. Per usare l'AI acquisti crediti: da {seo.price_from()} a "
                       "candidatura, senza abbonamento.")
        first_col, last_col = st.columns(2)
        first_name = first_col.text_input("Nome", key="register_first_name",
                                          max_chars=auth_service.MAX_NAME_LENGTH, autocomplete="given-name")
        last_name = last_col.text_input("Cognome", key="register_last_name",
                                        max_chars=auth_service.MAX_NAME_LENGTH, autocomplete="family-name")
        email = st.text_input("Email", key="register_email")
        password = st.text_input(
            "Password", type="password", key="register_password",
            help=f"Almeno {auth_service.MIN_PASSWORD_LENGTH} caratteri.",
        )
        confirm = st.text_input("Ripeti la password", type="password", key="register_confirm")
        accepted = st.checkbox(f"Ho letto e accetto i {legal_link('termini', 'Termini e condizioni')} e "
                               f"l'{legal_link('privacy', 'Informativa privacy')}", key="register_terms")
        if st.form_submit_button("Crea account", type="primary", use_container_width=True):
            if password != confirm:
                st.error("Le due password non coincidono.")
            elif not accepted:
                st.error("Per creare l'account devi accettare i Termini e condizioni e l'Informativa privacy.")
            else:
                try:
                    user = auth_service.register(email, password, terms_version=config.LEGAL_VERSION,
                                                 first_name=first_name, last_name=last_name)
                    sign_in(user)
                    send_verification_code(user.id)
                    st.rerun()
                except auth_service.AuthError as exc:
                    st.error(str(exc))


def render_password_reset() -> None:
    """Due passi: richiesta del codice via email, poi codice e nuova password."""
    reset_email = st.session_state.get("reset_email_sent")
    if not reset_email:
        with st.form("reset_request_form", border=False):
            st.caption("Ti inviamo per email un codice per scegliere una nuova password.")
            email = st.text_input("Email", key="reset_email")
            if st.form_submit_button("Inviami il codice", type="primary", use_container_width=True):
                try:
                    auth_service.request_password_reset(email)
                    st.session_state.reset_email_sent = email.strip().lower()
                    st.rerun()
                except (auth_service.AuthError, email_service.EmailError) as exc:
                    st.error(str(exc))
        return

    st.caption(f"Se **{reset_email}** è registrata, ti abbiamo inviato un codice di 6 cifre "
               f"(valido {config.EMAIL_CODE_MINUTES} minuti; controlla anche lo spam).")
    with st.form("reset_form", border=False):
        code = st.text_input("Codice", max_chars=7, placeholder="123456", key="reset_code")
        password = st.text_input("Nuova password", type="password", key="reset_password",
                                 help=f"Almeno {auth_service.MIN_PASSWORD_LENGTH} caratteri.")
        confirm = st.text_input("Ripeti la nuova password", type="password", key="reset_confirm")
        if st.form_submit_button("Reimposta la password", type="primary", use_container_width=True):
            if password != confirm:
                st.error("Le due password non coincidono.")
            else:
                try:
                    sign_in(auth_service.reset_password(reset_email, code, password))
                    st.session_state.update(reset_email_sent=None, auth_mode=None)
                    st.session_state.pending_toast = "Password aggiornata."
                    st.rerun()
                except auth_service.AuthError as exc:
                    st.error(str(exc))
    if st.button("Usa un'altra email o richiedi un nuovo codice", key="reset_restart", type="tertiary"):
        st.session_state.reset_email_sent = None
        st.rerun()


AI_BENEFITS = (
    "**Scopri quanto sei compatibile con l'annuncio** (es. 68%) e quali parole chiave ti mancano",
    "**Legge e riordina il tuo CV in un minuto**, invece di compilarlo campo per campo",
    "**Adatta il CV all'annuncio e scrive la lettera di presentazione** su misura",
    "**Traduce CV e lettera in inglese**, per candidarti anche all'estero",
    "**Prepara il profilo LinkedIn**: titolo, sezione Informazioni e post",
)


def needs_credits(new_session: bool = False) -> bool:
    """True se le funzioni con l'AI sono bloccate: candidatura non ancora pagata e nessun credito.

    Con `new_session` l'operazione inizierebbe una nuova candidatura (altro CV, altro annuncio):
    serve un credito anche se quella attuale è già pagata.
    """
    user = current_user()
    if user is None or user.is_admin:
        return False
    if st.session_state.session_paid and not new_session:
        return False
    return user.credits < 1


def delivery_blocked() -> bool:
    """True se per consegnare i documenti va scalato il credito ma l'utente non ne ha."""
    state = st.session_state
    user = current_user()
    return bool(user and not user.is_admin and not state.session_paid and state.session_ai_calls
                and user.credits < 1)


def charge_session() -> bool:
    """Scala il credito della candidatura alla prima consegna: download, salvataggio o testi LinkedIn.

    Una sola volta per candidatura; gratis se è stata fatta tutta a mano, senza AI.
    True se la candidatura risulta pagata (o gratuita).
    """
    state = st.session_state
    if state.session_paid or not state.session_ai_calls:
        return True
    user = current_user()
    if user is None or not auth_service.consume_credit(user.id):
        return False
    state.session_paid = True
    if not user.is_admin:
        state.pending_toast = ("Usato 1 credito per questa candidatura. Download, correzioni a mano e i testi "
                               "LinkedIn (se non li hai ancora generati) restano inclusi; ogni altra elaborazione "
                               "con l'AI userà un nuovo credito.")
    return True


def render_session_note() -> None:
    """Riga sotto la barra dei passi: cosa copre il credito della candidatura in corso."""
    user = current_user()
    if user is None or user.is_admin:
        return
    if st.session_state.session_paid:
        st.caption("✅ **Candidatura pagata**: download e correzioni a mano sono inclusi, e così i testi LinkedIn "
                   "se non li hai ancora generati. Ogni altra elaborazione con l'AI (modifiche, traduzioni, nuovi "
                   "testi) crea una nuova versione e usa 1 credito.")
    else:
        st.caption("🪙 **1 credito = 1 candidatura completa** (CV, annuncio e LinkedIn). Si scala solo quando "
                   "scarichi o salvi un documento, o generi i testi LinkedIn.")


def render_delivery_blocked(key: str) -> None:
    st.warning("Per scaricare o salvare i documenti serve 1 credito: copre tutta la candidatura, "
               "comprese modifiche e testi LinkedIn.")
    st.button("✨ Acquista crediti", type="primary", key=f"{key}_delivery_buy", on_click=open_buy_dialog)


def price_hook() -> str:
    packs = payment_service.credit_packs().values()
    cheapest = min(packs, key=lambda p: p.amount_cents)
    best = min(packs, key=lambda p: p.unit_cents)
    if best == cheapest:
        return f"{cheapest.price_label} per {cheapest.label}."
    return (f"Da {cheapest.price_label} per {cheapest.label}, oppure solo "
            f"{euro(best.unit_cents)} a candidatura con il pacchetto da {best.label}.")


def render_buy_credits(user: auth_service.User, key: str = "buy") -> None:
    if not payment_service.payments_enabled():
        st.caption("Gli acquisti non sono ancora attivi.")
        return
    packs = payment_service.credit_packs()
    # Il listino può cambiare (pannello admin): una scelta che non esiste più si dimentica.
    if st.session_state.get(f"{key}_pack") not in packs:
        st.session_state.pop(f"{key}_pack", None)
    # Preselezionato il pacchetto centrale del listino (con 1, 3 e 10: quello da 3), senza spingere
    # verso il più caro; il più conveniente a candidatura è segnalato, senza inventare «il più scelto».
    best = min(packs, key=lambda k: packs[k].unit_cents)
    pack_id = st.radio(
        "Pacchetto", list(packs), key=f"{key}_pack", label_visibility="collapsed",
        index=len(packs) // 2,
        format_func=lambda k: f"{packs[k].label} — {packs[k].price_label}"
        + (f" ({euro(packs[k].unit_cents)} a candidatura)" if packs[k].credits > 1 else "")
        + (" · prezzo migliore a candidatura" if k == best and len(packs) > 1 else ""),
    )
    # Contenuto digitale: per la normativa UE il recesso di 14 giorni si perde solo con il consenso
    # espresso dell'utente prima del pagamento.
    waiver = st.checkbox(
        "Voglio usare subito i crediti e accetto di perdere il diritto di recesso di 14 giorni "
        "dal momento in cui inizio a usarli.",
        key=f"{key}_waiver",
    )
    # Account creati prima che nome e cognome si chiedessero alla registrazione: servono per Stripe.
    first_name = last_name = None
    if not user.full_name:
        st.caption("Per il pagamento servono nome e cognome: compariranno sulla ricevuta di Stripe.")
        first_col, last_col = st.columns(2)
        first_name = first_col.text_input("Nome", key=f"{key}_first_name", max_chars=auth_service.MAX_NAME_LENGTH)
        last_name = last_col.text_input("Cognome", key=f"{key}_last_name", max_chars=auth_service.MAX_NAME_LENGTH)
    st.caption(f"I crediti valgono {config.CREDITS_VALIDITY_MONTHS} mesi dall'acquisto. Acquistando accetti i "
               f"{legal_link('termini', 'Termini e condizioni')} (recesso: art. 6).")
    checkout = st.session_state.get("checkout")
    if waiver and checkout and checkout[0] == pack_id:
        render_pay_link(checkout[2])
        return
    # Preparare il pagamento su Stripe richiede qualche secondo: il pulsante si disattiva subito e
    # mostra l'attesa, così non si clicca più volte. Quando il pagamento è pronto, al suo posto
    # compare il pulsante che apre Stripe.
    slot = st.empty()
    with slot:
        go = ai_button("Procedi al pagamento", "⏳ Preparo il pagamento sicuro...", key=f"{key}_go",
                       disabled=not waiver)
    if not go:
        if not waiver:
            st.caption("Spunta la casella qui sopra per continuare.")
        return
    with st.spinner("Collegamento a Stripe in corso..."):
        try:
            if first_name is not None:
                user = auth_service.set_name(user.id, first_name, last_name)
            waiver_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            st.session_state.checkout = (pack_id, *payment_service.create_checkout(
                user, pack_id, current_base_url(), withdrawal_waiver_at=waiver_at))
        except (auth_service.AuthError, payment_service.PaymentError) as exc:
            slot.empty()
            st.error(str(exc))
            st.button("Riprova", key=f"{key}_retry")
            return
    with slot.container():
        render_pay_link(st.session_state.checkout[2])


def render_pay_link(url: str) -> None:
    st.link_button("💳 Paga con Stripe", url, type="primary", use_container_width=True)
    st.caption("Il pagamento si apre in una nuova scheda: i crediti si attivano qui appena è confermato.")


@st.dialog("🪙 Crediti")
def buy_dialog() -> None:
    if (user := current_user()) is None:
        return
    if user.is_admin:
        st.success("Sei admin: le funzioni AI non consumano crediti.")
        return
    if user.credits:
        st.markdown(f"Hai **{plural(user.credits, 'credito', 'crediti')}**. Ogni candidatura completa "
                    "(CV, annuncio e LinkedIn) ne usa 1.")
        expiries = auth_service.credit_expiries(user.id)
        st.caption("Scadenza: " + " · ".join(
            f"{plural(e['credits'], 'credito', 'crediti')} entro il {e['expires_on']}" for e in expiries)
            + ". Si usano prima quelli che scadono prima.")
        st.markdown("**Ricarica crediti**")
    else:
        if free_mode_enabled():
            st.info("**Piano gratuito.** Puoi compilare e sistemare il CV a mano, scegliere lo stile e "
                    "scaricare il PDF. Le funzioni AI richiedono crediti.")
        render_free_vs_credits()
        st.markdown("**Nessun abbonamento.** 1 credito = 1 candidatura completa: si scala solo quando scarichi o "
                    f"salvi i documenti. {price_hook()}")
        st.markdown(f"👀 {guide_link('Guarda come funziona, con esempi')} →")
    render_buy_credits(user, key="dialog")


def open_buy_dialog() -> None:
    """Callback: la finestra si apre nel corpo dello script (non si può aprire da un'altra finestra o dal menu)."""
    st.session_state.open_buy = True


def render_upsell(title: str, key: str, free_hint: str | None = None) -> None:
    """Riquadro che invita all'acquisto, al posto di una funzione AI bloccata."""
    with st.container(border=True):
        st.markdown(f"#### ✨ {title}")
        st.markdown("\n".join(f"- {b}" for b in AI_BENEFITS))
        if payment_service.payments_enabled():
            st.markdown(f"**1 credito = 1 candidatura completa** · da {seo.price_from()} a candidatura · nessun abbonamento · "
                        f"crediti validi {config.CREDITS_VALIDITY_MONTHS} mesi")
            st.caption(price_hook() + " Il credito si scala solo quando scarichi o salvi i documenti.")
            st.markdown(f"👀 {guide_link('Guarda come funziona, con esempi')} →")
            if st.button("✨ Sblocca la mia candidatura", type="primary", use_container_width=True,
                         key=f"{key}_upsell"):
                # Rerun completo: chiude un'eventuale finestra aperta e apre quella dei crediti.
                open_buy_dialog()
                st.rerun()
        else:
            st.caption("Gli acquisti non sono ancora attivi.")
        if free_hint:
            st.caption(free_hint)


@st.fragment(run_every=config.CREDITS_REFRESH_SECONDS)
def render_credit_badge() -> None:
    """Saldo nella barra in alto, riletto a intervalli regolari per mostrare subito i crediti acquistati."""
    # Pagamento in sospeso: lo si verifica anche direttamente su Stripe, così i crediti
    # arrivano pure se il webhook non è in esecuzione.
    if checkout := st.session_state.get("checkout"):
        status, _ = payment_service.sync_checkout(checkout[1])
        if status in ("complete", "expired"):
            st.session_state.checkout = None
    user = current_user()
    if user is None:
        st.rerun()
    known = st.session_state.get("known_credits")
    st.session_state.known_credits = user.credits
    if known is not None and user.credits > known:
        # Rerun dell'intera app: le funzioni AI bloccate nella pagina si sbloccano subito.
        st.session_state.checkout = None
        st.session_state.pending_toast = f"Pagamento ricevuto: +{user.credits - known} crediti. Le funzioni AI sono attive."
        st.rerun()

    if user.credits:
        label, kind = f"🪙 {user.credits} credit{'o' if user.credits == 1 else 'i'}", "tertiary"
    else:
        label, kind = "✨ Acquista crediti", "primary"
    if st.button(label, type=kind, key="credit_badge", help="Saldo e acquisto crediti"):
        open_buy_dialog()
        st.rerun()


# --- Barra in alto e menu ☰ ------------------------------------------------------------
def close_menu() -> None:
    st.session_state.menu = False


def menu_action(action, *args) -> None:
    """Callback delle voci del menu: esegue l'azione e chiude il menu."""
    close_menu()
    action(*args)


def show_view(view: str) -> None:
    st.session_state.view = view
    request_scroll_top()


def menu_item(label: str, icon: str, key: str, action, *args, current: bool = False) -> None:
    # La voce della sezione aperta ha una key «menu_cur_*», evidenziata dal CSS del menu.
    st.button(label, icon=f":material/{icon}:", key=f"menu_cur_{key}" if current else f"menu_{key}",
              type="tertiary", width="stretch", on_click=menu_action, args=(action, *args))


def menu_html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def render_menu(user: auth_service.User) -> None:
    with st.popover("", icon=":material/menu:", key="menu", on_change="rerun", help="Menu"), \
            st.container(key="menu_panel"):
        if user.is_admin:
            status = "Admin · crediti illimitati"
        elif not user.email_verified:
            status = "Email da confermare"
        else:
            status = plural(user.credits, "credito", "crediti")
        menu_html(f'<div class="cv-menu-head"><div class="cv-avatar">{html.escape(user.email[:1].upper())}</div>'
                  f'<div><div class="cv-menu-email">{html.escape(user.email)}</div>'
                  f'<div class="cv-menu-sub">{status}</div></div></div>')
        if user.email_verified:
            view = st.session_state.view
            menu_html('<div class="cv-menu-label">Candidature</div>')
            menu_item("Percorso guidato", "route", "guided", show_view, GUIDED_VIEW, current=view == GUIDED_VIEW)
            menu_item("Come funziona", "help", "guide", open_guide_page)
            if st.session_state.cv is not None:
                menu_item("Nuova candidatura", "add_circle", "new_app", new_application)
            if user.has_applications_archive:
                menu_item("Le mie candidature", "folder_open", "archive", show_view, ARCHIVE_VIEW,
                          current=view == ARCHIVE_VIEW)
            menu_item("Ricomincia da capo", "restart_alt", "restart",
                      lambda: st.session_state.update(open_restart=True))
            menu_html('<div class="cv-menu-label">Account</div>')
            if user.is_admin:
                menu_item("Pannello admin", "admin_panel_settings", "admin", show_view, ADMIN_VIEW,
                          current=view == ADMIN_VIEW)
            else:
                menu_item("Crediti e acquisti", "toll", "credits", open_buy_dialog)
        menu_item("Contattaci", "mail", "contact", open_contact_dialog)
        menu_html('<div class="cv-menu-label">Informazioni legali</div>')
        menu_item("Termini e condizioni", "gavel", "terms", open_legal_page, "termini")
        menu_item("Privacy e cookie", "shield", "privacy", open_legal_page, "privacy")
        if not user.is_admin:
            menu_item("Elimina il mio account", "person_remove", "delete_account",
                      lambda: st.session_state.update(open_delete_account=True))
        menu_html('<div class="cv-menu-sep"></div>')
        menu_item("Esci", "logout", "logout", sign_out)


def render_topbar(user: auth_service.User | None) -> None:
    """Logo a sinistra; saldo crediti e menu ☰ a destra. Sostituisce la sidebar."""
    with st.container(key="topbar", horizontal=True, horizontal_alignment="distribute",
                      vertical_alignment="center", wrap=False):
        st.markdown(f'<span class="cv-brand">{config.APP_ICON} {config.APP_TITLE}</span>', unsafe_allow_html=True)
        if user is None:
            return
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center",
                          wrap=False, width="content"):
            if user.email_verified:
                if user.is_admin:
                    st.caption("Admin · crediti illimitati")
                else:
                    render_credit_badge()
            render_menu(user)


@st.dialog("🔄 Ricomincia da capo")
def restart_dialog() -> None:
    st.markdown("Cancelli CV, documenti e testi LinkedIn e riparti dal passo 1. Le candidature salvate restano.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Annulla", key="restart_cancel"):
            st.rerun()
        # Callback: l'azzeramento avviene anche se la finestra non viene ridisegnata.
        if st.button("Sì, ricomincia", type="primary", key="restart_confirm", on_click=reset_session):
            st.rerun()


CONTACT_TOPICS = ("Domanda sul servizio", "Problema tecnico", "Pagamenti e crediti", "Suggerimento", "Altro")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@st.dialog("✉️ Contattaci")
def contact_dialog() -> None:
    user = current_user()
    st.caption("Scrivici per domande, problemi o suggerimenti: ti rispondiamo per email.")
    with st.form("contact_form", border=False):
        name = st.text_input("Nome", max_chars=80)
        email = st.text_input("La tua email", value=user.email if user else "", max_chars=120,
                              help="Ti rispondiamo a questo indirizzo.")
        topic = st.selectbox("Argomento", CONTACT_TOPICS)
        message = st.text_area("Messaggio", height=180, max_chars=5000,
                               placeholder="Descrivi la tua richiesta...")
        if not st.form_submit_button("Invia", type="primary", use_container_width=True):
            return
    name, email, message = name.strip(), email.strip(), message.strip()
    if not (name and message):
        st.warning("Scrivi il tuo nome e il messaggio.")
        return
    if not EMAIL_RE.fullmatch(email):
        st.warning("Indica un indirizzo email valido, così possiamo risponderti.")
        return
    wait = config.CONTACT_RESEND_SECONDS - (time.time() - st.session_state.get("contact_sent_at", 0))
    if wait > 0:
        st.warning(f"Hai appena inviato un messaggio: attendi {int(wait) + 1} secondi prima di inviarne un altro.")
        return
    try:
        email_service.send_contact_message(name, email, topic, message, user.email if user else None)
    except email_service.EmailError as exc:
        st.error(f"{exc} In alternativa scrivici a {config.CONTACT_EMAIL}.")
        return
    st.session_state.contact_sent_at = time.time()
    st.session_state.pending_toast = "Messaggio inviato: ti risponderemo al più presto."
    st.rerun()


def open_contact_dialog() -> None:
    st.session_state.open_contact = True


# --- Documenti legali ------------------------------------------------------------------
LEGAL_DOCS = {
    "termini": ("Termini e condizioni", "legal/termini-e-condizioni.md"),
    "privacy": ("Privacy e cookie", "legal/privacy-e-cookie.md"),
}


@st.cache_data
def read_legal_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def legal_text(doc: str) -> str:
    """Testo del documento con i dati che l'app conosce già (email di contatto, indirizzo del sito)."""
    return (read_legal_text(LEGAL_DOCS[doc][1])
            .replace("[EMAIL_CONTATTO]", config.CONTACT_EMAIL)
            .replace("[NOME_SITO]", current_base_url()))


def legal_link(doc: str, text: str | None = None) -> str:
    """Link in Markdown al documento: si apre in una nuova scheda, senza perdere il lavoro in corso."""
    return f"[{text or LEGAL_DOCS[doc][0]}]({current_base_url()}/?doc={doc})"


def legal_links() -> str:
    return f"{legal_link('termini')} · {legal_link('privacy')}"


def close_legal_page() -> None:
    st.query_params.pop("doc", None)
    request_scroll_top()


def render_legal_page(doc: str) -> None:
    st.set_page_config(page_title=seo.DOC_PAGES[doc][0])
    st.button("← Torna all'app", type="tertiary", key="legal_back", on_click=close_legal_page)
    with st.container(border=True):
        st.markdown(legal_text(doc))
    st.caption(legal_links())


def open_legal_page(doc: str) -> None:
    st.query_params["doc"] = doc
    request_scroll_top()


# --- Come funziona -----------------------------------------------------------------------
# Pagina con gli screenshot del percorso completo (?doc=come-funziona): mostra a chi non ha crediti
# cosa ottiene con una candidatura. Si apre anche senza accesso.
GUIDE_DOC = "come-funziona"
SCREENSHOTS_DIR = "docs/screenshots"

# (titolo, testo, [(screenshot, didascalia)]) per ogni passo del percorso.
GUIDE_STEPS = (
    ("Passo 1 · Carichi il tuo CV, l'AI lo legge e lo riordina",
     "Carichi il CV che hai già, in PDF o come testo. In meno di un minuto l'AI lo legge e lo riordina in un "
     "formato pulito, leggibile dai sistemi di selezione automatica (ATS). Controlli cosa ha letto e "
     "correggi quello che vuoi.",
     [("01_carica_cv.png", "Carichi il PDF o incolli il testo del CV."),
      ("02_cv_letto.png", "Il CV letto e riordinato: esperienze, risultati e competenze al loro posto."),
      ("03_cv_editor.png", "Vuoi correggere o aggiungere qualcosa? Modifichi ogni campo.")]),
    ("Passo 2 · Scegli l'annuncio: compatibilità, CV adattato e lettera",
     "Incolli il link o il testo dell'annuncio. L'AI confronta il tuo CV con l'offerta e ti dice **quanto sei "
     "compatibile**, quali parole chiave dell'annuncio hai già e quali mancano. Poi riscrive il CV mettendo in "
     "evidenza ciò che l'azienda cerca e scrive una lettera di presentazione su misura. Ti candidi all'estero? "
     "Con un clic CV e lettera vengono tradotti in inglese.",
     [("04_offerta.png", "Indichi l'annuncio con il link o incollandone il testo."),
      ("05_risultato.png", "La percentuale di compatibilità con l'annuncio e le parole chiave presenti e mancanti."),
      ("06_lettera.png", "La lettera di presentazione scritta per quell'annuncio, con i tuoi risultati concreti."),
      ("07_modifica_ai.png", "Vuoi cambiare qualcosa? Lo scrivi a parole e l'AI corregge CV e lettera."),
      ("08_modifica_applicata.png", "Le modifiche applicate, con il riepilogo di cosa è cambiato."),
      ("09_download.png", "Scegli la lingua (italiano o inglese, con la traduzione dell'AI) e lo stile guardando "
                          "l'anteprima, poi scarichi CV e lettera in PDF.")]),
    ("Passo 3 · Il profilo LinkedIn",
     "Dal tuo CV l'AI scrive il titolo del profilo (headline), la sezione «Informazioni» e un post per far "
     "sapere che sei disponibile, nel tono che preferisci. Li copi e li incolli su LinkedIn. Alla fine trovi "
     "tutti i documenti della candidatura insieme, da scaricare anche in un unico file ZIP.",
     [("10_linkedin_toni.png", "Scegli una o più versioni: risultati, diplomatico, narrativo, essenziale."),
      ("11_linkedin_testi.png", "Titolo, sezione Informazioni e post pronti da copiare.")]),
)


def open_guide_page() -> None:
    st.query_params["doc"] = GUIDE_DOC
    request_scroll_top()


def guide_link(text: str = "Guarda come funziona") -> str:
    """Link in Markdown alla pagina «Come funziona»: si apre in una nuova scheda, senza perdere il lavoro."""
    return f"[{text}]({current_base_url()}/?doc={GUIDE_DOC})"


CREDIT_INCLUDES = (
    "Lettura e riordino del CV con l'AI",
    "Percentuale di compatibilità con l'annuncio e parole chiave mancanti",
    "CV adattato all'annuncio",
    "Lettera di presentazione su misura",
    "Traduzione di CV e lettera in inglese",
    "Modifiche a parole con l'AI",
    "Titolo, Informazioni e post per LinkedIn",
    "Tutti i documenti in PDF, in 7 stili",
)


def render_free_vs_credits() -> None:
    """Cosa include un credito; con la modalità gratuita attiva, confronto con ciò che si fa gratis."""
    if not free_mode_enabled():
        st.markdown("\n".join(f"- ✅ {item}" for item in CREDIT_INCLUDES))
        return
    rows = [("Compilare il CV a mano e scaricarlo in PDF", "✅", "✅")]
    rows += [(item, "—", "✅") for item in CREDIT_INCLUDES[:-1]]
    st.table(pd.DataFrame(rows, columns=["", "Gratis", "Con 1 credito"]).set_index(""))


def close_guide_and_buy() -> None:
    close_legal_page()
    open_buy_dialog()


def render_guide_cta(key: str) -> None:
    """Invito finale: acquisto per chi ha fatto l'accesso, registrazione per gli altri."""
    user = current_user()
    with st.container(border=True):
        st.markdown("#### Pronto per la tua prossima candidatura?")
        st.markdown(f"Nessun abbonamento. {price_hook()} Il credito si scala solo quando scarichi o salvi i "
                    "documenti: fino ad allora puoi provare, correggere e rigenerare quanto vuoi.")
        if user is None:
            st.button("Crea il tuo account", type="primary", use_container_width=True, key=f"{key}_signup",
                      on_click=close_legal_page)
        elif user.is_admin or user.credits:
            st.button("Torna al percorso guidato", type="primary", use_container_width=True, key=f"{key}_back",
                      on_click=close_legal_page)
        elif payment_service.payments_enabled():
            st.button("✨ Acquista crediti", type="primary", use_container_width=True, key=f"{key}_buy",
                      on_click=close_guide_and_buy)


def render_guide_page() -> None:
    st.set_page_config(page_title=seo.DOC_PAGES[GUIDE_DOC][0])
    st.button("← Torna all'app", type="tertiary", key="guide_back", on_click=close_legal_page)
    st.markdown("## Come funziona CV Architect")
    st.markdown("Ecco cosa ottieni con **1 credito = 1 candidatura completa**: dal CV caricato ai documenti "
                "pronti da inviare, fino al profilo LinkedIn. Gli esempi sono di una candidata di fantasia.")
    st.markdown("#### Gratis o con i crediti?" if free_mode_enabled() else "#### Cosa include 1 credito")
    render_free_vs_credits()
    render_guide_cta("guide_top")
    for title, text, shots in GUIDE_STEPS:
        st.markdown(f"### {title}")
        st.markdown(text)
        for file, caption in shots:
            with st.container(border=True):
                st.image(f"{SCREENSHOTS_DIR}/{file}", caption=caption, width="stretch")
    render_guide_cta("guide_bottom")
    st.markdown("#### Domande frequenti")
    for question, answer in seo.faq():
        with st.expander(question):
            st.markdown(answer)
    st.caption(legal_links())


@st.dialog("Elimina il mio account")
def delete_account_dialog() -> None:
    user = current_user()
    if user is None:
        return
    if user.is_admin:
        st.info("Gli account admin non si eliminano da qui: usa il Pannello admin o `manage_users.py`.")
        return
    lost = [f"i tuoi **{plural(user.credits, 'credito', 'crediti')}**, che non vengono rimborsati"] if user.credits else []
    if user.has_applications_archive:
        lost.append("tutte le **candidature salvate** in «Le mie candidature»")
    st.markdown("Eliminiamo subito e per sempre il tuo account e i dati collegati. Perdi:\n"
                + "\n".join(f"- {item}" for item in ["l'accesso con questa email", *lost]))
    st.caption("Per obblighi di legge conserviamo i dati dei pagamenti, scollegati dall'account. "
               "Se ti servono CV o documenti, scaricali prima di continuare. L'operazione non si può annullare.")
    with st.form("delete_account_form", border=False):
        password = st.text_input("Per confermare, scrivi la tua password", type="password")
        if not st.form_submit_button("Elimina definitivamente l'account", type="primary", use_container_width=True):
            return
    try:
        auth_service.delete_own_account(user.id, password)
    except auth_service.AuthError as exc:
        st.error(str(exc))
        return
    sign_out()
    st.session_state.pending_toast = "Il tuo account è stato eliminato."
    st.rerun()


def open_pending_dialogs() -> None:
    """Le finestre richieste dal menu o dai pulsanti si aprono qui, una alla volta."""
    if st.session_state.pop("open_restart", False):
        restart_dialog()
    elif st.session_state.pop("open_buy", False):
        buy_dialog()


def store_admin_api_key() -> None:
    st.session_state.admin_api_key = st.session_state.admin_api_key_input.strip()


def resolve_api_key(user: auth_service.User | None) -> str | None:
    """Chiave Anthropic: quella scritta dall'admin nel pannello, altrimenti la variabile d'ambiente."""
    if user is not None and user.is_admin and st.session_state.get("admin_api_key"):
        return st.session_state.admin_api_key
    return config.get_env_api_key()


def ai_button(label: str, busy_label: str, key: str, disabled: bool = False) -> bool:
    """Pulsante di un'operazione con l'AI. True nel giro in cui l'operazione va eseguita.

    Il clic imposta solo lo stato «in corso» e fa ripartire la pagina: in quel giro il pulsante
    appare già disattivato con `busy_label`, così si vede subito che il portale sta lavorando.
    """
    busy_key = f"{key}_busy"
    busy = st.session_state.pop(busy_key, False)
    st.button(busy_label if busy else label, type="primary", use_container_width=True, key=key,
              disabled=disabled or busy, on_click=lambda: st.session_state.update({busy_key: True}))
    return busy


# Un credito copre un giro di generazioni: lettura del CV, adattamento all'annuncio e testi LinkedIn, una volta
# ciascuno, con le modifiche fatte prima della consegna. Dopo la consegna (credito scalato) ogni altro uso
# dell'AI apre una nuova versione della candidatura, che userà un altro credito.
ROUND_ACTIONS = ("Lettura CV", "Adattamento all'annuncio", "Testi LinkedIn")


def starts_new_round(action: str) -> bool:
    state = st.session_state
    return bool(state.session_paid and (action not in ROUND_ACTIONS or action in state.session_done))


def ai_blocked(action: str) -> bool:
    """True se per `action` serve un credito che l'utente non ha."""
    return needs_credits() or (starts_new_round(action) and needs_credits(new_session=True))


def render_new_round_hint(action: str) -> None:
    if starts_new_round(action):
        when = "subito" if action == "Testi LinkedIn" else "quando scarichi o salvi i documenti"
        st.caption(f"🪙 Questa candidatura è già pagata: questa elaborazione con l'AI crea una nuova versione e "
                   f"userà 1 credito {when}.")


def begin_new_version() -> None:
    """Nuovo giro dopo una consegna: nuova candidatura da pagare; nell'archivio quella pagata resta com'è."""
    start_session()
    st.session_state.saved_app_id = None
    st.session_state.new_version = True
    st.session_state.reopened = None


def run_claude(api_key: str | None, action: str, spinner_text: str, call):
    """Esegue `call(service)` in un riquadro «in corso» ben visibile. None se fallisce.

    Le singole operazioni non scalano crediti (si paga la candidatura, vedi charge_session): si
    contano per la candidatura e si registrano come `action` nelle statistiche, con dei tetti
    che limitano i costi dell'AI.
    """
    user = current_user()
    if user is None:
        st.error("Accedi per usare l'app.")
        return None
    if needs_credits():
        st.error("Serve 1 credito per questa candidatura: acquistalo dal pulsante «✨ Acquista crediti» in alto.")
        return None
    new_round = starts_new_round(action)
    if new_round and needs_credits(new_session=True):
        st.error("Questa candidatura è già pagata: per un'altra elaborazione con l'AI serve 1 credito. "
                 "Acquistalo dal pulsante «✨ Acquista crediti» in alto.")
        return None
    if not user.is_admin:
        if st.session_state.session_ai_calls >= config.SESSION_AI_CALLS_LIMIT:
            st.error(f"Hai fatto {config.SESSION_AI_CALLS_LIMIT} operazioni con l'AI per questa candidatura, "
                     "il massimo previsto. Scarica i documenti, oppure inizia una «Nuova candidatura» dal menu ☰.")
            return None
        if auth_service.ai_calls_last_day(user.id) >= config.DAILY_AI_CALLS_LIMIT:
            st.error("Hai raggiunto il limite giornaliero di operazioni con l'AI. Riprova tra qualche ora.")
            return None
    if not api_key:
        st.error("Manca la API key Anthropic: inseriscila nel Pannello admin oppure impostala nel file .env.")
        return None
    with st.status(f"⏳ {spinner_text}", expanded=True) as status:
        st.caption("L'AI sta lavorando: di solito ci vuole meno di un minuto. Non chiudere e non ricaricare la pagina.")
        try:
            result = call(ClaudeCVService(api_key=api_key))
        except ClaudeServiceError as exc:
            status.update(label="Non è andata a buon fine", state="error")
            st.error(str(exc))
            return None
        status.update(label="✅ Fatto!", state="complete", expanded=False)
    if new_round:
        begin_new_version()
    st.session_state.session_done.append(action)
    st.session_state.session_ai_calls += 1
    auth_service.log_usage(user.id, action)
    return result


def render_stepper(current: int) -> None:
    # Senza crediti (e senza CV) i passi 2 e 3 hanno il lucchetto: si sbloccano con un credito.
    locked = st.session_state.cv is None and needs_credits(new_session=True)
    parts = []
    for i, title in enumerate(STEP_TITLES, start=1):
        if i == current:
            state, dot = "current", str(i)
        elif step_done(i):
            state, dot = "done", "✓"
        elif i == 2 and st.session_state.job_skipped:
            state, dot = "skipped", "–"
        elif locked and i > 1:
            state, dot = "", "🔒"
        else:
            state, dot = "", str(i)
        parts.append(f'<div class="cv-step {state}"><div class="cv-dot">{dot}</div>'
                     f'<div class="cv-label">{title}</div></div>')
        if i < len(STEP_TITLES):
            bar_done = step_done(i) or (i == 2 and st.session_state.job_skipped)
            parts.append(f'<div class="cv-bar {"done" if bar_done else ""}"></div>')
    st.markdown(f'<div class="cv-stepper">{"".join(parts)}</div>', unsafe_allow_html=True)


def render_step_header(step: int, title: str, instructions: str) -> None:
    st.markdown(f"### Passo {step} di 3 · {title}")
    st.markdown(instructions)


def action_bar(hint: str, back: tuple | None = None, forward: tuple | None = None,
               forward_primary: bool = True, disabled_reason: str | None = None) -> None:
    """Barra fissa in fondo alla pagina: cosa fare adesso, Indietro e Avanti sempre a portata di mano.

    `back` e `forward` sono tuple (etichetta, callback, *argomenti). Con `disabled_reason`
    il pulsante Avanti resta visibile ma disattivato, e la riga spiega perché.
    """
    with st.bottom, st.container(key="actionbar"):
        st.markdown(f"👉 **{disabled_reason or hint}**")
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            if back:
                label, callback, *args = back
                st.button(f"← {label}", key="bar_back", on_click=callback, args=tuple(args))
            else:
                st.space()
            if forward:
                label, callback, *args = forward
                st.button(f"{label} →", key="bar_next", on_click=callback, args=tuple(args),
                          type="primary" if forward_primary and not disabled_reason else "secondary",
                          disabled=bool(disabled_reason))


def render_resume_review(cv: ResumeData, key: str, on_save) -> None:
    """Anteprima del CV; con l'interruttore attivo, editor completo al suo posto."""
    toggle_key = f"{key}_edit_toggle"
    if edit_toggle("✏️ Voglio correggere o aggiungere qualcosa", toggle_key):
        edit_resume(cv, key=key, on_save=on_save, on_done=lambda: close_toggle(toggle_key))
    else:
        with st.container(border=True):
            st.markdown(CV_MARKDOWN_TEMPLATE.render(cv=cv))


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def pdf_file_name(prefix: str, cv: ResumeData) -> str:
    slug = "_".join(cv.full_name.split()) or "cv"
    return f"{prefix}_{slug}.pdf"


THUMB_WIDTH, PREVIEW_WIDTH = 260, 900


@st.cache_data(max_entries=64, show_spinner=False)
def cv_page_images(cv_json: str, template: str, language: str, width: int) -> list[bytes]:
    cv = ResumeData.model_validate_json(cv_json)
    return pdf_page_images(render_cv_pdf(cv, template, language), width)


@st.cache_data(max_entries=32, show_spinner=False)
def letter_page_images(cv_json: str, letter: str, template: str, language: str) -> list[bytes]:
    cv = ResumeData.model_validate_json(cv_json)
    return pdf_page_images(render_cover_letter_pdf(cv, letter, template, language), PREVIEW_WIDTH)


def choose_template(key: str, template: str) -> None:
    st.session_state[f"{key}_tpl"] = template


def render_template_gallery(cv: ResumeData, key: str, language: str) -> str:
    """Prima pagina del CV in ognuno degli stili: si sceglie lo stile guardandolo. Restituisce lo stile scelto."""
    selected = st.session_state.setdefault(f"{key}_tpl", "moderno")
    cv_json = cv.model_dump_json()
    with st.spinner("Preparo le anteprime..."), st.container(horizontal=True, gap="small"):
        for template, tpl in TEMPLATES.items():
            with st.container(border=True, width=170, gap="small"):
                st.image(cv_page_images(cv_json, template, language, THUMB_WIDTH)[0], width="stretch")
                st.button(f"✓ {tpl.label}" if template == selected else tpl.label, key=f"{key}_pick_{template}",
                          disabled=template == selected, use_container_width=True,
                          on_click=choose_template, args=(key, template))
    return selected


def render_pages(images: list[bytes]) -> None:
    for i, image in enumerate(images, start=1):
        st.image(image, caption=f"Pagina {i} di {len(images)}" if len(images) > 1 else None,
                 width="stretch")


LANGUAGE_NAMES = {"it": "italiano", "en": "inglese"}


def translation_key(cv: ResumeData, cover_letter: str | None, language: str) -> str:
    """Impronta di contenuto e lingua: se CV o lettera cambiano, la traduzione va rifatta."""
    raw = f"{language}\n{cv.model_dump_json()}\n{cover_letter or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def documents_in_language(cv: ResumeData, cover_letter: str | None, key: str, language: str,
                          translate: bool = True) -> tuple[ResumeData, str | None]:
    """CV e lettera nella lingua scelta: gli originali, la traduzione già fatta o la proposta di tradurli.

    Senza `translate` (archivio: candidatura già consegnata) niente traduzione con l'AI: cambiano solo i titoli.
    """
    source = guess_language(cv.professional_summary, *(h for e in cv.experiences for h in e.highlights),
                            cover_letter or "")
    if language == source:
        return cv, cover_letter
    translations = st.session_state.setdefault("translations", {})
    fingerprint = translation_key(cv, cover_letter, language)
    if fingerprint in translations:
        translated_cv, translated_letter = translations[fingerprint]
        st.caption(f"🌍 Testi tradotti in {LANGUAGE_NAMES[language]} con l'AI. Se correggi CV o lettera, "
                   "la traduzione va rifatta.")
        return ResumeData.model_validate_json(translated_cv), translated_letter

    what = "CV e lettera" if cover_letter else "il CV"
    if not translate:
        st.caption(f"Il testo è in {LANGUAGE_NAMES[source]}: in {LANGUAGE_NAMES[language]} cambiano solo i titoli. "
                   "Per tradurre anche il testo crea una nuova versione della candidatura (1 credito).")
        return cv, cover_letter
    with st.container(border=True):
        st.markdown(f"**Il testo è in {LANGUAGE_NAMES[source]}.** Per avere {what} in {LANGUAGE_NAMES[language]} "
                    "li traduciamo con l'AI; nel frattempo l'anteprima mostra i titoli nella nuova lingua "
                    "e il testo originale.")
        render_new_round_hint("Traduzione")
        if ai_blocked("Traduzione"):
            st.caption("La traduzione richiede crediti.")
            st.button("✨ Acquista crediti", key=f"{key}_translate_buy", on_click=open_buy_dialog)
        elif ai_button(f"🌍 Traduci {what} in {LANGUAGE_NAMES[language]}", "⏳ Traduzione in corso...",
                       key=f"{key}_translate"):
            result = run_claude(
                resolve_api_key(current_user()), "Traduzione",
                f"Sto traducendo {what} in {LANGUAGE_NAMES[language]}...",
                lambda s: s.translate_documents(cv, cover_letter, LANGUAGE_NAMES[language]),
            )
            if result is not None:
                translations[fingerprint] = (result.cv.model_dump_json(), result.cover_letter)
                st.rerun()
    return cv, cover_letter


def render_pdf_downloads(cv: ResumeData, key: str, cover_letter: str | None = None, preview: bool = True,
                         charge: bool = True) -> None:
    """Lingua, stile, download PDF e, con `preview`, anteprima dei documenti impaginati.

    Con `charge` il primo download scala il credito della candidatura in corso; senza (archivio)
    i documenti sono già stati pagati.
    """
    default_lang = guess_language(cv.professional_summary, *(h for e in cv.experiences for h in e.highlights))
    language = st.segmented_control(
        "Lingua del documento", list(LANGUAGES), default=default_lang, format_func=LANGUAGES.get,
        key=f"{key}_language", required=True,
        help="Lingua di titoli e testi. Se scegli una lingua diversa da quella del CV, puoi farlo tradurre.",
    )
    cv, cover_letter = documents_in_language(cv, cover_letter, key, language, translate=charge)
    if preview:
        st.markdown("**Scegli lo stile** guardando l'anteprima:")
        template = render_template_gallery(cv, key, language)
    else:
        template = st.selectbox("Stile del documento", list(TEMPLATES), key=f"{key}_tpl",
                                index=list(TEMPLATES).index("moderno"), format_func=lambda k: TEMPLATES[k].label)
    tpl = TEMPLATES[template]
    ats = ("Leggibile da tutti i sistemi ATS." if tpl.ats_friendly else
           "Stile grafico: alcuni sistemi ATS di selezione automatica lo leggono peggio di uno stile a colonna "
           "singola. Se ti candidi tramite portali, preferisci Classico, Moderno, Tecnico o Executive.")
    st.caption(f"**{tpl.label}**: {tpl.description} {ats}")

    # PDF generati solo al clic: l'archivio candidature mostra molti pulsanti per pagina.
    if charge and delivery_blocked():
        render_delivery_blocked(key)
    else:
        on_click = charge_session if charge else "rerun"
        with st.container(horizontal=True):
            st.download_button(
                "📥 Scarica il CV (PDF)",
                data=lambda: render_cv_pdf(cv, template, language),
                file_name=pdf_file_name("CV", cv),
                mime="application/pdf",
                key=f"{key}_cv_pdf",
                on_click=on_click,
            )
            if cover_letter:
                st.download_button(
                    "📥 Scarica la lettera (PDF)",
                    data=lambda: render_cover_letter_pdf(cv, cover_letter, template, language),
                    file_name=pdf_file_name("Lettera" if language == "it" else "Cover_letter", cv),
                    mime="application/pdf",
                    key=f"{key}_letter_pdf",
                    on_click=on_click,
                )

    if not preview:
        return
    st.markdown(f"**Anteprima · stile {tpl.label}**")
    cv_json = cv.model_dump_json()
    with st.spinner("Preparo l'anteprima..."):
        if cover_letter:
            cv_tab, letter_tab = st.tabs(["📄 CV", "✉️ Lettera"], key=f"{key}_preview_tab")
            with cv_tab:
                render_pages(cv_page_images(cv_json, template, language, PREVIEW_WIDTH))
            with letter_tab:
                render_pages(letter_page_images(cv_json, cover_letter, template, language))
        else:
            render_pages(cv_page_images(cv_json, template, language, PREVIEW_WIDTH))


def render_keywords(title: str, keywords: list[str], color: str) -> None:
    st.markdown(f"**{title}** ({len(keywords)})")
    if keywords:
        st.markdown(" ".join(f":{color}-badge[{kw}]" for kw in keywords))
    else:
        st.caption("Nessuna.")


def render_copy_block(title: str, text: str, limit: int | None = None) -> None:
    counter = f"{len(text)}/{limit} caratteri" if limit else f"{len(text)} caratteri"
    st.markdown(f"**{title}**")
    st.caption(counter)
    if limit and len(text) > limit:
        st.warning(f"Il testo supera il limite LinkedIn di {limit} caratteri: accorcialo con «Voglio correggere qualcosa».")
    # st.code espone un pulsante di copia negli appunti integrato.
    st.code(text, language=None, wrap_lines=True)


# --- Passo 1: CV -------------------------------------------------------------------------
def free_mode_enabled() -> bool:
    return auth_service.get_flag(auth_service.FLAG_FREE_MANUAL_MODE)


def set_new_cv(cv: ResumeData, source_text: str | None = None, from_ai: bool = False) -> None:
    """Sostituisce il CV di partenza; `source_text` è il testo originale per chi compila a mano.

    Se la candidatura in corso è già pagata, un altro CV ne inizia una nuova (`from_ai`: il CV
    l'ha appena letto l'AI, operazione che conta per la nuova candidatura).
    """
    if st.session_state.session_paid:
        start_session(ai_calls=1 if from_ai else 0)
    st.session_state.cv = cv
    st.session_state.cv_source_text = source_text
    # I risultati derivati dal CV precedente non sono più validi.
    for k in JOB_KEYS:
        st.session_state[k] = None
    st.session_state.linkedin = None
    st.session_state.job_skipped = False
    st.session_state.autosave_cv = True
    st.session_state.autosave_off = False
    st.session_state.new_version = False
    bump("base")
    request_scroll_top()


def draft_cv_from_text(text: str) -> ResumeData:
    """Bozza senza AI: solo i dati riconoscibili con certezza (nome in prima riga, email, LinkedIn)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = lines[0] if lines else ""
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
    linkedin = re.search(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", text, re.IGNORECASE)
    return ResumeData(
        full_name=first if len(first) <= 60 and not re.search(r"[@\d]", first) else "",
        email=email.group() if email else None,
        linkedin_url=linkedin.group() if linkedin else None,
        professional_summary="",
    )


def render_manual_cv_input(key: str) -> None:
    """Percorso senza AI: CV salvato in JSON, oppure editor da compilare con il testo del CV a fianco."""
    uploaded = st.file_uploader(
        "Il tuo CV (PDF, TXT o il file JSON salvato da questa app) — facoltativo",
        type=["pdf", "txt", "json"], key=f"{key}_manual_file",
    )
    st.caption(
        "Con un file JSON salvato in precedenza il CV si carica completo. Con PDF o TXT ti mostriamo il testo "
        "del CV da copiare nei campi del modulo. Senza file parti da un modulo vuoto."
    )
    if not st.button("Apri il modulo del CV", type="primary", use_container_width=True, key=f"{key}_manual_go"):
        return

    if uploaded is None:
        set_new_cv(ResumeData(full_name="", professional_summary=""), source_text="")
    elif uploaded.name.lower().endswith(".json"):
        try:
            set_new_cv(ResumeData.model_validate_json(uploaded.getvalue()))
        except ValueError:
            st.error("Questo file JSON non è un CV salvato da questa app.")
            return
    else:
        try:
            raw_text = read_uploaded_file(uploaded)
        except PyPdfError:
            st.error("Non riesco ad aprire questo PDF. Prova con un file TXT o parti dal modulo vuoto.")
            return
        set_new_cv(draft_cv_from_text(raw_text), source_text=raw_text)
    st.session_state["base_edit_toggle"] = True
    st.rerun()


def render_cv_input(api_key: str | None, key: str) -> None:
    free_mode = free_mode_enabled()
    if free_mode:
        mode = st.radio(
            "Come vuoi procedere?",
            ["✨ Con l'AI — consigliato: legge e riordina il CV per te (1 credito a candidatura)",
             "✍️ A mano, senza AI — gratis: compili tu i campi"],
            key=f"{key}_mode",
        )
        if mode.startswith("✍️"):
            render_manual_cv_input(key)
            return
    # Un altro CV inizia una nuova candidatura: serve un credito anche se quella attuale è pagata.
    if needs_credits(new_session=True):
        render_upsell(
            "Con l'AI il tuo CV è pronto in un minuto", key,
            free_hint="Oppure scegli «A mano, senza AI» qui sopra: è gratis, ma compili tu ogni campo."
            if free_mode else None,
        )
        return
    uploaded = st.file_uploader("Il tuo CV (PDF o TXT)", type=["pdf", "txt"], key=f"{key}_file")
    with st.expander("Non hai il file? Incolla qui il testo del CV"):
        pasted = st.text_area(
            "Testo del CV", height=250, key=f"{key}_text", label_visibility="collapsed",
            placeholder="Incolla qui il testo del tuo CV...",
        )
    clicked = ai_button("Leggi il mio CV", "⏳ Sto leggendo il tuo CV...", key=f"{key}_go")
    if not clicked:
        st.caption("Di solito ci vuole meno di un minuto.")
        return

    raw_text = pasted.strip()
    if uploaded is not None:
        try:
            raw_text = read_uploaded_file(uploaded)
        except PyPdfError:
            st.error("Non riesco ad aprire questo PDF. Prova a incollare il testo del CV.")
            return
        if not raw_text:
            st.error("Da questo PDF non si riesce a leggere il testo (forse è una scansione). Incolla il testo del CV.")
            return
    if not raw_text:
        st.warning("Carica il file del CV oppure incollane il testo.")
        return

    cv = run_claude(api_key, "Lettura CV", "Sto leggendo il tuo CV...", lambda s: s.parse_unstructured_cv(raw_text))
    if cv is not None:
        set_new_cv(cv, from_ai=True)
        st.rerun()


def render_steps_preview() -> None:
    """Le 3 fasi con uno screenshot d'esempio: cosa si ottiene con un credito, anche prima di comprarlo."""
    st.markdown("#### Cosa ottieni con 1 credito")
    for col, (file, caption) in zip(st.columns(3), (
            ("02_cv_letto.png", "① CV letto e riordinato, leggibile dagli ATS"),
            ("05_risultato.png", "② Compatibilità con l'annuncio (es. 68%), parole chiave mancanti, CV adattato "
                                 "e lettera"),
            ("11_linkedin_testi.png", "③ Titolo, Informazioni e post LinkedIn in più toni"))):
        with col:
            st.image(f"{SCREENSHOTS_DIR}/{file}", width="stretch")
            st.caption(caption)
    st.markdown(f"👀 {guide_link('Guarda tutto il percorso, passo per passo')} →")


def render_step_cv(api_key: str | None) -> None:
    cv: ResumeData | None = st.session_state.cv
    if cv is None:
        render_step_header(
            1, "Carica il tuo CV",
            "Carica il CV che hai già: lo riordiniamo in un formato pulito, adatto ai sistemi "
            "di selezione automatica dei candidati (ATS).",
        )
        render_cv_input(api_key, key="cv_input")
        if needs_credits(new_session=True):
            render_steps_preview()
        if needs_credits(new_session=True) and not free_mode_enabled():
            action_bar("Ti serve 1 credito per iniziare: copre CV, annuncio e LinkedIn",
                       forward=("✨ Acquista crediti", open_buy_dialog))
        else:
            action_bar("Carica il tuo CV per iniziare", forward=("Avanti: offerta di lavoro", go_to, 2),
                       disabled_reason="Carica il CV per andare avanti")
        return

    source_text = st.session_state.get("cv_source_text")
    if source_text is None:
        render_step_header(1, "Controlla il tuo CV", "Ecco cosa abbiamo letto. Se è tutto giusto, vai avanti.")
        skills = len(cv.hard_skills) + len(cv.soft_skills)
        with st.container(border=True):
            st.markdown(f"✅ **{cv.full_name or 'Il tuo CV'}** · "
                        f"{plural(len(cv.experiences), 'esperienza', 'esperienze')} · "
                        f"{plural(len(cv.education), 'titolo di studio', 'titoli di studio')} · "
                        f"{plural(skills, 'competenza', 'competenze')}")
    else:
        render_step_header(1, "Compila il tuo CV", "Completa i campi del modulo; quando hai finito vai avanti.")
        if source_text:
            with st.expander("📄 Testo del CV che hai caricato (da copiare nei campi)"):
                st.code(source_text, language=None, wrap_lines=True)
    with st.expander("👀 Vedi e correggi il CV", expanded=source_text is not None):
        st.caption("Le correzioni valgono anche per i passi successivi.")
        render_resume_review(cv, key="base", on_save=lambda edited: st.session_state.update(cv=edited))

    with st.container(border=True):
        st.markdown("#### 📄 Il tuo CV impaginato")
        render_pdf_downloads(cv, key="base")

    with st.container(horizontal=True, vertical_alignment="center"):
        if not delivery_blocked():
            st.download_button(
                "💾 Salva il CV per riprenderlo in seguito (JSON)", data=cv.model_dump_json(indent=2),
                file_name=pdf_file_name("CV", cv).replace(".pdf", ".json"), mime="application/json", key="base_json",
                type="tertiary", on_click=charge_session,
                help="Il file JSON si può ricaricare in questa app per riprendere il CV da dove l'hai lasciato.",
            )
        if st.button("🔄 Usa un altro CV", type="tertiary", key="cv_replace"):
            replace_cv_dialog(api_key)
    render_autosave_note()

    action_bar("Tutto giusto? Ora scegli l'annuncio di lavoro",
               forward=("Avanti: offerta di lavoro", go_to, 2))


@st.dialog("🔄 Usa un altro CV", width="large")
def replace_cv_dialog(api_key: str | None) -> None:
    st.caption("Il nuovo CV sostituisce quello attuale e tutto ciò che è stato preparato dopo."
               + (" Inizia una nuova candidatura, che userà 1 credito quando scaricherai i documenti."
                  if st.session_state.session_paid else ""))
    render_cv_input(api_key, key="cv_reupload")


# --- Passo 2: offerta di lavoro ------------------------------------------------------------
def render_job_input(api_key: str | None, cv: ResumeData, key: str) -> None:
    mode = st.radio(
        "Come vuoi indicare l'annuncio?",
        ["Ho il link dell'annuncio", "Incollo il testo dell'annuncio"],
        horizontal=True,
        key=f"{key}_mode",
    )
    use_url = mode.startswith("Ho il link")
    if use_url:
        job_url = st.text_input("Link dell'annuncio", placeholder="https://...", key=f"{key}_url")
        st.caption(
            "Alcuni siti, come LinkedIn, non si lasciano leggere in automatico: se ricevi un errore, "
            "scegli «Incollo il testo dell'annuncio» e copia lì il testo."
        )
    else:
        job_text = st.text_area(
            "Testo dell'annuncio", height=250, key=f"{key}_text", placeholder="Incolla qui il testo dell'annuncio..."
        )
    clicked = ai_button("Prepara CV e lettera", "⏳ Sto preparando CV e lettera...", key=f"{key}_go")
    if not clicked:
        st.caption("Di solito ci vogliono uno o due minuti.")
        return

    if use_url:
        if not job_url.strip():
            st.warning("Incolla il link dell'annuncio.")
            return
        try:
            with st.spinner("Sto aprendo l'annuncio..."):
                job_text = extract_job_description_from_url(job_url)
        except ScraperError as exc:
            st.error(f"{exc}\n\nScegli «Incollo il testo dell'annuncio» e copia lì il testo.")
            return
        problem = job_page_problem(api_key, job_text)
        if problem:
            st.error(
                f"Non sono riuscito a leggere l'annuncio da questo link: {problem}\n\n"
                "Non ti è stato scalato nessun credito. Scegli «Incollo il testo dell'annuncio» e copia lì il testo."
            )
            with st.expander("Testo che ho trovato nella pagina"):
                st.text(job_text[:3000])
            return
    job_text = job_text.strip()
    if not job_text:
        st.warning("Incolla il testo dell'annuncio.")
        return

    # Un altro annuncio dopo aver pagato la candidatura ne inizia una nuova.
    replacing = st.session_state.match is not None
    match = run_claude(
        api_key,
        "Adattamento all'annuncio",
        "Sto confrontando il tuo CV con l'annuncio e scrivendo la lettera...",
        lambda s: s.tailor_cv_for_job(cv, job_text),
    )
    if match is not None:
        if replacing and st.session_state.session_paid:
            start_session(ai_calls=1)
        if replacing:
            st.session_state.saved_app_id = None
        st.session_state.reopened = None
        st.session_state.autosave_off = False
        st.session_state.new_version = False
        st.session_state.match = match
        st.session_state.match_prev = None
        st.session_state.job_text = job_text
        st.session_state.job_skipped = False
        st.session_state.job_tab_reset = True
        bump("tailored")
        bump("letter")
        request_scroll_top()
        st.rerun()


def job_page_problem(api_key: str | None, page_text: str) -> str | None:
    """Controllo gratuito (non scala crediti) che la pagina scaricata contenga davvero un annuncio.

    Molti siti rispondono con una pagina di login, cookie o errore lunga abbastanza da superare
    i controlli dello scraper: senza questo controllo Claude adatterebbe il CV a quel testo.
    Restituisce il motivo se non è un annuncio; None se lo è o se il controllo non si può fare.
    """
    user = current_user()
    if not api_key or user is None or needs_credits():
        return None  # ci pensa run_claude a mostrare l'errore
    try:
        with st.spinner("Controllo che la pagina contenga l'annuncio..."):
            check = ClaudeCVService(api_key=api_key).check_job_posting(page_text)
    except ClaudeServiceError:
        return None
    if check.is_job_posting:
        return None
    return check.reason.strip() or "la pagina non contiene il testo di un annuncio di lavoro."


def score_comment(score: int) -> str:
    if score >= 80:
        return "Ottima corrispondenza."
    if score >= 60:
        return "Buona corrispondenza: guarda qui sotto cosa manca."
    return "Corrispondenza bassa: valuta se l'annuncio fa davvero per te."


def set_match(match: JobMatchResult) -> None:
    st.session_state.match = match
    bump("tailored")
    bump("letter")


def undo_revision() -> None:
    set_match(st.session_state.match_prev)
    st.session_state.match_prev = None


def render_ai_revision(api_key: str | None, cv: ResumeData, match: JobMatchResult) -> None:
    """Modifica di CV adattato e lettera descritta a parole e applicata da Claude."""
    st.caption(
        "Scrivi cosa non va o cosa vuoi cambiare, come lo diresti a una persona. Per esempio: "
        "«Non ho lavorato nel settore farmaceutico ma in quello chimico: non dire che ho esperienza diretta "
        "nel farmaceutico, evidenzia ciò che è simile» oppure «La lettera è troppo formale, rendila più diretta»."
    )
    render_new_round_hint("Modifica con l'AI")
    rev = st.session_state.get("revise_rev", 0)
    instructions = st.text_area(
        "Cosa vuoi cambiare?", height=130, key=f"revise_{rev}_text",
        placeholder="Descrivi la modifica...",
    )
    scope = st.radio(
        "Cosa modificare", list(REVISION_SCOPES), format_func=lambda k: REVISION_SCOPES[k][0],
        horizontal=True, key="revise_scope",
    )
    if not ai_button("Applica la modifica", "⏳ Sto applicando la modifica...", key="revise_go"):
        return
    if not instructions.strip():
        st.warning("Scrivi cosa vuoi cambiare.")
        return

    revision = run_claude(
        api_key,
        "Modifica con l'AI",
        "Sto applicando la modifica...",
        lambda s: s.revise_application(cv, match, st.session_state.job_text, instructions.strip(), scope),
    )
    if revision is None:
        return
    st.session_state.match_prev = match
    set_match(match.model_copy(update={
        "tailored_cv": revision.tailored_cv,
        "cover_letter": revision.cover_letter,
    }))
    st.session_state.revise_changes = revision.changes_summary
    bump("revise")
    st.rerun()


def render_step_job(api_key: str | None) -> None:
    cv: ResumeData = st.session_state.cv
    match: JobMatchResult | None = st.session_state.match
    if match is None:
        render_step_header(
            2, "Scegli l'offerta di lavoro",
            "Indica l'annuncio a cui vuoi candidarti: adattiamo il tuo CV all'annuncio e scriviamo una lettera "
            "di presentazione su misura.",
        )
        if needs_credits():
            render_upsell("Adatta il CV all'annuncio e ricevi la lettera", key="job")
            hint = "Acquista crediti per adattare il CV, oppure salta e vai al profilo LinkedIn"
        else:
            render_job_input(api_key, cv, key="job_input")
            hint = "Indica l'annuncio e premi «Prepara CV e lettera». Non ce l'hai ancora? Puoi saltare"
        action_bar(hint,
                   back=("Il tuo CV", go_to, 1), forward=("Salta: vai a LinkedIn", skip_job),
                   forward_primary=False)
        return

    render_step_header(
        2, "Il CV adattato e la lettera",
        "Ecco i documenti per questa candidatura: controllali, correggili se serve e scaricali.",
    )
    if reopened := st.session_state.get("reopened"):
        title, new_version = reopened
        if new_version and not st.session_state.session_paid:
            st.info(f"**Nuova versione di «{title}».** Correggi CV e lettera nelle schede qui sotto, oppure chiedi "
                    "all'AI cosa cambiare. È una nuova candidatura: userà 1 credito quando scarichi o salvi i "
                    "documenti. L'originale resta invariato in «Le mie candidature».")
        elif not new_version:
            st.info(f"**Bozza «{title}» ripresa dall'archivio.** Continua da qui: ogni modifica si salva nella "
                    "bozza.")

    with st.container(border=True):
        st.markdown(f"**Compatibilità con l'annuncio: {match.match_score}%** · {score_comment(match.match_score)}")
        st.progress(match.match_score / 100)
        with st.expander("🔑 Cosa chiede l'annuncio e cosa manca nel tuo CV"):
            render_keywords("Già presenti nel tuo CV", match.matching_keywords, "green")
            render_keywords("Mancanti nel tuo CV", match.missing_keywords, "red")
            st.caption(
                "Le competenze mancanti non vengono aggiunte in automatico. Se le hai davvero, aggiungile al CV "
                "adattato nella scheda «Correggi il CV»."
            )

    with st.container(border=True):
        st.markdown("#### 📥 I tuoi documenti")
        render_pdf_downloads(match.tailored_cv, key="tailored", cover_letter=match.cover_letter)
        if not delivery_blocked():
            with st.container(horizontal=True):
                with st.popover("Altri formati"):
                    st.download_button("Lettera (.txt)", data=match.cover_letter, file_name="lettera_presentazione.txt",
                                       on_click=charge_session)
                    st.download_button("CV (Markdown)", data=CV_MARKDOWN_TEMPLATE.render(cv=match.tailored_cv),
                                       file_name="cv_adattato.md", mime="text/markdown", on_click=charge_session)
                    st.download_button("CV (JSON)", data=match.tailored_cv.model_dump_json(indent=2),
                                       file_name="cv_adattato.json", mime="application/json", on_click=charge_session)

    # Scheda aperta gestita da session_state: dopo una nuova generazione si torna sulla lettera.
    if st.session_state.pop("job_tab_reset", False) or st.session_state.get("job_tab") not in JOB_TABS:
        st.session_state.job_tab = JOB_TABS[0]
    letter_tab, cv_tab, ai_tab = st.tabs(JOB_TABS, key="job_tab")
    with letter_tab:
        toggle_key = "letter_edit_toggle"
        if edit_toggle("✏️ Voglio correggere qualcosa", toggle_key):
            st.caption("Lascia una riga vuota tra un paragrafo e l'altro.")
            if (letter := edit_text(match.cover_letter, key="letter", label="Lettera")) is not None:
                st.session_state.match = match.model_copy(update={"cover_letter": letter})
                bump("letter")
                close_toggle(toggle_key)
                st.toast("Lettera aggiornata.")
                st.rerun()
        else:
            st.code(match.cover_letter, language=None, wrap_lines=True)
    with cv_tab:
        render_resume_review(
            match.tailored_cv, key="tailored",
            on_save=lambda edited: st.session_state.update(
                match=st.session_state.match.model_copy(update={"tailored_cv": edited})
            ),
        )
    with ai_tab:
        if changes := st.session_state.pop("revise_changes", None):
            st.success("**Modifica applicata.** Guarda il risultato nell’anteprima qui sopra.\n\n"
                       + "\n".join(f"- {c}" for c in changes))
        if st.session_state.match_prev is not None:
            st.button("↩️ Annulla l'ultima modifica dell'AI", key="revise_undo", on_click=undo_revision)
        render_ai_revision(api_key, cv, match)

    if st.button("🔄 Prova con un altro annuncio", type="tertiary", key="job_replace"):
        replace_job_dialog(api_key, cv)
    render_autosave_note()

    action_bar("Scarica i documenti, poi prepara il profilo LinkedIn",
               back=("Il tuo CV", go_to, 1), forward=("Avanti: profilo LinkedIn", go_to, 3))


JOB_TABS = ("✏️ Correggi la lettera", "✏️ Correggi il CV", "🤖 Modifica con l'AI")


@st.dialog("🔄 Prova con un altro annuncio", width="large")
def replace_job_dialog(api_key: str | None, cv: ResumeData) -> None:
    st.caption("Il nuovo annuncio sostituisce CV adattato e lettera attuali: scaricali prima se ti servono."
               + (" È una nuova candidatura: userà 1 credito quando scaricherai i documenti."
                  if st.session_state.session_paid else "")
               + (" In «Le mie candidature» si salva come candidatura a parte." if archive_enabled() else ""))
    if needs_credits(new_session=True):
        render_upsell("Adatta il CV all'annuncio e ricevi la lettera", key="job_dialog")
    else:
        render_job_input(api_key, cv, key="job_reinput")


# --- Passo 3: LinkedIn -------------------------------------------------------------------
def render_linkedin_input(api_key: str | None, cv: ResumeData, key: str) -> None:
    st.markdown("**Quali versioni vuoi?** Puoi sceglierne più di una e poi usare quella che preferisci.")
    tones = [
        tone
        for tone, (label, _) in LINKEDIN_TONES.items()
        if st.checkbox(f"**{label}**: {TONE_DESCRIPTIONS[tone]}", value=tone in ("risultati", "diplomatico"),
                       key=f"{key}_{tone}")
    ]
    render_new_round_hint("Testi LinkedIn")
    clicked = ai_button("Scrivi i testi LinkedIn", "⏳ Sto scrivendo i testi LinkedIn...", key=f"{key}_go",
                        disabled=not tones)
    if not clicked:
        st.caption("Le versioni vengono scritte in contemporanea: di solito ci vuole circa un minuto.")
        return

    versions = run_claude(
        api_key,
        "Testi LinkedIn",
        "Sto scrivendo i testi per LinkedIn..." if len(tones) == 1 else f"Sto scrivendo {len(tones)} versioni dei testi LinkedIn...",
        lambda s: s.generate_linkedin_versions(cv, tones),
    )
    if versions is not None:
        # I testi LinkedIn sono una consegna: completano la candidatura e ne scalano il credito.
        charge_session()
        st.session_state.linkedin = versions
        for tone in versions:
            bump(f"linkedin_{tone}")
        request_scroll_top()
        st.rerun()


def final_documents() -> tuple[ResumeData, str | None, str, str]:
    """CV (adattato, se c'è l'annuncio) e lettera con lo stile e la lingua scelti per scaricarli.

    Se in quella lingua c'è già la traduzione fatta dall'AI si usa quella; altrimenti i testi originali.
    """
    state = st.session_state
    match: JobMatchResult | None = state.match
    key = "tailored" if match else "base"
    cv, letter = (match.tailored_cv, match.cover_letter) if match else (state.cv, None)
    template = state.get(f"{key}_tpl", "moderno")
    source = guess_language(cv.professional_summary, *(h for e in cv.experiences for h in e.highlights),
                            letter or "")
    language = state.get(f"{key}_language") or source
    translated = state.get("translations", {}).get(translation_key(cv, letter, language))
    if translated:
        cv, letter = ResumeData.model_validate_json(translated[0]), translated[1]
    elif language != source:
        language = source  # traduzione non fatta: i titoli restano nella lingua del testo
    return cv, letter, template, language


def linkedin_text(versions: dict[str, LinkedInContent]) -> str:
    return "\n\n".join(
        f"=== Versione «{LINKEDIN_TONES[tone][0]}» ===\n\nHEADLINE\n{c.headline}\n\nINFORMAZIONI\n"
        f"{c.about_section}\n\nPOST\n{c.launch_post}"
        for tone, c in versions.items()
    )


def render_final_documents(versions: dict[str, LinkedInContent]) -> None:
    """Fine percorso: tutti i documenti della candidatura in un posto, senza tornare ai passi precedenti."""
    cv, letter, template, language = final_documents()
    letter_name = pdf_file_name("Lettera" if language == "it" else "Cover_letter", cv)
    texts = linkedin_text(versions)

    def zip_all() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(pdf_file_name("CV", cv), render_cv_pdf(cv, template, language))
            if letter:
                archive.writestr(letter_name, render_cover_letter_pdf(cv, letter, template, language))
            archive.writestr("Testi_LinkedIn.txt", texts)
        return buffer.getvalue()

    with st.container(border=True):
        st.markdown("#### 📦 La tua candidatura completa")
        st.caption(f"Stile {TEMPLATES[template].label}, in {LANGUAGE_NAMES[language]}: quelli scelti per "
                   "scaricare il CV. Per cambiarli torna al passo precedente.")
        if delivery_blocked():
            render_delivery_blocked("final")
            return
        st.download_button("📦 Scarica tutto (ZIP)", data=zip_all, file_name="Candidatura.zip",
                           mime="application/zip", type="primary", use_container_width=True,
                           key="final_zip", on_click=charge_session)
        with st.container(horizontal=True):
            st.download_button("📄 CV (PDF)", data=lambda: render_cv_pdf(cv, template, language),
                               file_name=pdf_file_name("CV", cv), mime="application/pdf", key="final_cv",
                               on_click=charge_session)
            if letter:
                st.download_button("✉️ Lettera (PDF)",
                                   data=lambda: render_cover_letter_pdf(cv, letter, template, language),
                                   file_name=letter_name, mime="application/pdf", key="final_letter",
                                   on_click=charge_session)
            st.download_button("💼 Testi LinkedIn (.txt)", data=texts, file_name="Testi_LinkedIn.txt",
                               key="final_linkedin", on_click=charge_session)


def render_step_linkedin(api_key: str | None) -> None:
    cv: ResumeData = st.session_state.cv
    versions: dict[str, LinkedInContent] | None = st.session_state.linkedin
    back = ("Offerta di lavoro", go_to, 2)
    if not versions:
        render_step_header(
            3, "Prepara il profilo LinkedIn",
            "Scriviamo il titolo del profilo (headline), la sezione «Informazioni» e un post per far sapere che "
            "sei disponibile. Partiamo dal tuo CV del passo 1: il profilo LinkedIn è uno solo e vale per "
            "tutte le candidature.",
        )
        if needs_credits():
            render_upsell("Fatti scrivere il profilo LinkedIn", key="li")
        else:
            render_linkedin_input(api_key, cv, key="li_input")
        action_bar("Acquista crediti per farti scrivere i testi LinkedIn" if needs_credits()
                   else "Scegli le versioni e premi «Scrivi i testi LinkedIn»", back=back)
        return

    render_step_header(
        3, "Il tuo profilo LinkedIn",
        "Copia ogni testo con l'icona 📋 in alto a destra del riquadro e incollalo nel tuo profilo LinkedIn.",
    )
    with st.container(border=True):
        done = ["✓ CV"] + (["✓ Candidatura"] if st.session_state.match is not None else []) + ["✓ LinkedIn"]
        st.markdown(f"🎉 **Hai finito!** {' · '.join(done)}")
        st.caption("Per un altro annuncio usa «Nuova candidatura» (1 credito quando scarichi i documenti): "
                   "il CV e i testi LinkedIn restano.")
        if (saved := current_saved_application()) is not None:
            st.caption(f"💾 Questi testi sono salvati anche nella candidatura «{saved.title}».")
    render_final_documents(versions)

    st.markdown("#### 💼 I testi per LinkedIn")
    tabs = st.tabs([f"Versione «{LINKEDIN_TONES[t][0]}»" for t in versions])
    for tab, (tone, content) in zip(tabs, versions.items()):
        with tab:
            st.caption(TONE_DESCRIPTIONS[tone].capitalize())
            toggle_key = f"linkedin_edit_toggle_{tone}"
            if edit_toggle("✏️ Voglio correggere qualcosa", toggle_key):
                if (edited := edit_linkedin(content, key=f"linkedin_{tone}")) is not None:
                    st.session_state.linkedin = {**versions, tone: edited}
                    bump(f"linkedin_{tone}")
                    close_toggle(toggle_key)
                    st.toast("Testi aggiornati.")
                    st.rerun()
                continue
            render_copy_block("Headline (titolo sotto il tuo nome)", content.headline, limit=220)
            render_copy_block("Informazioni (sezione «About»)", content.about_section, limit=2600)
            render_copy_block("Post da pubblicare", content.launch_post)

    with st.expander("🔄 Vuoi altre versioni dei testi LinkedIn?"):
        st.caption("Le nuove versioni sostituiscono quelle attuali.")
        render_linkedin_input(api_key, cv, key="li_reinput")
    action_bar("Hai tutto: candidati a un altro annuncio con lo stesso CV", back=back,
               forward=("➕ Nuova candidatura", new_application))


# --- Le mie candidature ----------------------------------------------------------------
def app_store() -> applications_service.ApplicationStore:
    """Archivio dell'utente collegato: ogni lettura e scrittura è limitata alle sue candidature."""
    return applications_service.ApplicationStore(st.session_state.user_id)


def archive_enabled() -> bool:
    """«Le mie candidature» è attivata dall'admin per singolo utente."""
    user = current_user()
    return bool(user and user.has_applications_archive)


def current_saved_application() -> applications_service.SavedApplication | None:
    app_id = st.session_state.get("saved_app_id")
    return app_store().get(app_id) if app_id and archive_enabled() else None


def application_paid() -> bool:
    """La candidatura in corso è pagata, o fatta tutta a mano (gratis): dall'archivio si può scaricare."""
    return bool(st.session_state.session_paid or not st.session_state.session_ai_calls)


def saved_fingerprint() -> tuple:
    state = st.session_state
    return (state.cv.model_dump_json(), state.match.model_dump_json() if state.match else None,
            tuple((tone, c.model_dump_json()) for tone, c in (state.linkedin or {}).items()),
            state.job_text, application_paid())


def autosave_application() -> None:
    """Salvataggio automatico in «Le mie candidature»: il CV, poi CV adattato e lettera, infine LinkedIn.

    Si aggiorna una sola candidatura per annuncio: un altro CV o un altro annuncio ne iniziano una nuova
    (saved_app_id azzerato). Le bozze non pagate si riaprono ma non si scaricano dall'archivio.
    """
    state = st.session_state
    if state.cv is None or state.autosave_off or not archive_enabled():
        return
    if state.saved_app_id is None and state.match is None and not (state.autosave_cv and state.cv.full_name.strip()):
        return
    fingerprint = saved_fingerprint()
    # Nessuna modifica: niente da salvare (vale anche per una nuova versione appena riaperta, non ancora cambiata).
    if fingerprint == state.get("saved_app_fingerprint"):
        return
    store = app_store()
    match = state.match
    if state.new_version and match is not None:
        job_title = match.job_title.removesuffix(NEW_VERSION_SUFFIX)
        match = match.model_copy(update={"job_title": job_title + NEW_VERSION_SUFFIX})
    values = (state.cv, match, state.job_text, state.linkedin, application_paid())
    if state.saved_app_id is None:
        state.saved_app_id = store.save(*values)
    elif not store.update(state.saved_app_id, *values):
        # Eliminata dall'archivio (es. da un'altra scheda): non la si ricrea.
        state.saved_app_id = None
        state.autosave_off = True
        return
    state.saved_app_fingerprint = fingerprint


def render_autosave_note() -> None:
    if archive_enabled() and st.session_state.get("saved_app_id"):
        st.caption("💾 Salvata in automatico in «📁 Le mie candidature» (menu ☰): puoi interrompere e riprendere "
                   "da lì quando vuoi.")


def format_date(timestamp: str) -> str:
    year, month, day = timestamp[:10].split("-")
    return f"{day}/{month}/{year}"


def delete_application(app_id: int) -> None:
    if app_store().delete(app_id):
        if st.session_state.get("saved_app_id") == app_id:
            st.session_state.saved_app_id = None
            st.session_state.autosave_off = True
        st.session_state.pending_toast = "Candidatura eliminata."


def reopen_application(app_id: int) -> None:
    """Riporta la candidatura nel percorso guidato (passo 2).

    Una bozza non pagata si riprende così com'è e si continua a salvare lì. Una candidatura pagata resta
    invariata nell'archivio: riaprirla crea una nuova versione, che è una nuova candidatura (1 credito alla
    consegna) e si salva a parte appena la si modifica.
    """
    if (app := app_store().get(app_id)) is None:
        return
    reset_session()
    new_version = app.paid
    st.session_state.autosave_cv = app.match is None
    st.session_state.cv = app.base_cv or app.cv
    st.session_state.match = app.match
    if app.match is not None:
        # Titolo come nell'archivio (le candidature meno recenti non hanno azienda e ruolo nel risultato).
        st.session_state.match = app.match.model_copy(update={
            "company_name": app.match.company_name or app.company_name,
            "job_title": app.match.job_title or app.job_title})
    st.session_state.new_version = new_version and app.match is not None
    # Le candidature salvate prima di questa funzione non hanno il testo dell'annuncio.
    if app.match is not None:
        st.session_state.job_text = (app.job_text or
                                     f"Candidatura per il ruolo {app.job_title} presso {app.company_name}.")
    st.session_state.linkedin = app.linkedin or None
    st.session_state.saved_app_id = None if new_version else app.id
    # Una generazione, un credito: la nuova versione di una candidatura con annuncio si paga alla consegna
    # anche se la si corregge solo a mano. La bozza ha già usato l'AI: si paga alla prima consegna.
    st.session_state.session_paid = False
    st.session_state.session_ai_calls = 0 if new_version and app.match is None else 1
    st.session_state.saved_app_fingerprint = saved_fingerprint()
    st.session_state.reopened = (app.title, new_version)
    st.session_state.step = 2
    st.session_state.view = GUIDED_VIEW
    st.session_state.job_tab = JOB_TABS[2]
    request_scroll_top()
    for key in ("base", "tailored", "letter", "revise", *(f"linkedin_{tone}" for tone in app.linkedin)):
        bump(key)
    st.session_state.pending_toast = (f"Nuova versione di «{app.title}»." if new_version
                                      else f"Bozza «{app.title}» ripresa.")


NEW_VERSION_SUFFIX = " (nuova versione)"


def render_saved_application(app: applications_service.SavedApplication) -> None:
    if app.paid:
        st.markdown("**✅ Completata.** I documenti restano così come li hai generati: scaricali quando vuoi, "
                    "senza usare crediti.")
        render_pdf_downloads(app.cv, key=f"saved_{app.id}", cover_letter=app.cover_letter or None, preview=False,
                             charge=False)
    with st.container(border=True):
        if not app.paid:
            st.markdown("**📝 Bozza.** Riprendila nel percorso guidato dal punto in cui ti eri "
                        "fermato. I documenti si scaricano quando la completi: userà 1 credito, come sempre.")
        elif app.match is None:
            st.markdown("**➕ Vuoi candidarti a un annuncio con questo CV?** Riprendilo nel percorso guidato: "
                        "è una nuova candidatura e userà 1 credito quando scarichi i documenti.")
        else:
            st.markdown("**✏️ Vuoi cambiarla?** Crea una nuova versione: la correggi a mano o con l'AI e si "
                        "salva come candidatura a parte. È una nuova candidatura e userà 1 credito quando scarichi "
                        "i documenti; questa resta invariata.")
        if st.session_state.cv is not None and st.session_state.get("saved_app_id") != app.id:
            st.caption("Sostituisce il lavoro aperto ora nel percorso guidato.")
        label = "Riprendi da qui" if not app.paid else "Usa per un nuovo annuncio" if app.match is None \
            else "✏️ Crea una nuova versione"
        st.button(label, type="primary" if not app.paid else "secondary", key=f"saved_{app.id}_reopen",
                  on_click=reopen_application, args=(app.id,))
    if app.match is None:
        with st.container(border=True):
            st.markdown(CV_MARKDOWN_TEMPLATE.render(cv=app.cv))
    else:
        render_saved_tabs(app)
    with st.popover("🗑️ Elimina candidatura"):
        st.markdown("Vuoi eliminare questa candidatura? L'operazione non si può annullare.")
        st.button("Sì, elimina", type="primary", key=f"saved_{app.id}_delete",
                  on_click=delete_application, args=(app.id,))


def render_saved_tabs(app: applications_service.SavedApplication) -> None:
    cv_tab, letter_tab, linkedin_tab = st.tabs(["📄 CV adattato", "✉️ Lettera", "💼 LinkedIn"])
    with cv_tab, st.container(border=True):
        st.markdown(CV_MARKDOWN_TEMPLATE.render(cv=app.cv))
    with letter_tab:
        st.code(app.cover_letter, language=None, wrap_lines=True)
    with linkedin_tab:
        if not app.linkedin:
            st.caption("Nessun testo LinkedIn: si aggiungono se li generi al passo 3 dopo aver salvato la candidatura.")
        for tone, content in app.linkedin.items():
            st.markdown(f"##### Versione «{LINKEDIN_TONES.get(tone, (tone,))[0]}»")
            render_copy_block("Headline (titolo sotto il tuo nome)", content.headline, limit=220)
            render_copy_block("Informazioni (sezione «About»)", content.about_section, limit=2600)
            render_copy_block("Post da pubblicare", content.launch_post)


def render_applications() -> None:
    st.markdown("### 📁 Le mie candidature")
    applications = app_store().list()
    if not applications:
        st.info("Non hai ancora candidature salvate. Si salvano da sole mentre lavori nel percorso guidato: "
                "prima il CV, poi CV adattato e lettera, infine i testi LinkedIn.")
        return
    st.caption(f"{len(applications)} candidature salvate. Si salvano da sole mentre lavori: le bozze si "
               "riprendono quando vuoi; quelle completate si scaricano senza usare crediti e restano invariate "
               "(per cambiarle se ne crea una nuova versione, con 1 credito).")
    for app in applications:
        score = f" · {app.match_score}%" if app.match_score is not None else ""
        badge = (" · 📝 bozza" + (f" ({app.stage})" if app.stage != "completa" else "")
                 if not app.paid or app.match is None else "")
        with st.expander(f"🏢 **{app.title}** · {format_date(app.created_at)}{score}{badge}"):
            render_saved_application(app)


# --- Pannello Admin --------------------------------------------------------------------
ARCHIVE_COLUMN = "📁 Candidature"


def euro(cents: int) -> str:
    return f"{cents / 100:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def render_credit_form() -> None:
    st.markdown("#### Aggiungi o togli crediti")
    with st.form("admin_credits_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        email = c1.text_input("Email dell'utente")
        delta = c2.number_input("Crediti", value=1, step=1, help="Numero negativo per togliere crediti.")
        if st.form_submit_button("Applica", type="primary"):
            if not delta:
                st.warning("Indica un numero di crediti diverso da zero.")
            else:
                try:
                    balance = auth_service.add_credits(email, int(delta))
                    verb = "aggiunti" if delta > 0 else "tolti"
                    st.success(f"{abs(int(delta))} crediti {verb} a {email.strip().lower()}. Nuovo saldo: {balance}.")
                except auth_service.AuthError as exc:
                    st.error(str(exc))


def render_account_admin(current: auth_service.User) -> None:
    """Password forzata ed eliminazione di un account."""
    st.markdown("#### Gestione account")
    password_tab, delete_tab = st.tabs(["Imposta una nuova password", "Elimina un account"])

    with password_tab, st.form("admin_password_form", clear_on_submit=True):
        st.caption("La password dell'utente viene sostituita e gli accessi aperti sui suoi dispositivi vengono chiusi. "
                   "Comunicagli tu la nuova password.")
        email = st.text_input("Email dell'utente", key="admin_pw_email")
        password = st.text_input("Nuova password", type="password", key="admin_pw_new",
                                 help=f"Almeno {auth_service.MIN_PASSWORD_LENGTH} caratteri.")
        if st.form_submit_button("Imposta la password", type="primary"):
            try:
                auth_service.set_password(email, password)
                st.success(f"Password di {email.strip().lower()} aggiornata.")
            except auth_service.AuthError as exc:
                st.error(str(exc))

    with delete_tab, st.form("admin_delete_form", clear_on_submit=True):
        st.caption("Elimina l'account con i crediti residui e le candidature salvate. Non si può annullare. "
                   "Pagamenti e statistiche restano nei totali.")
        email = st.text_input("Email dell'utente", key="admin_del_email")
        confirm = st.text_input("Riscrivi l'email per confermare", key="admin_del_confirm")
        if st.form_submit_button("Elimina l'account", type="primary"):
            email, confirm = email.strip().lower(), confirm.strip().lower()
            if not email or email != confirm:
                st.error("Le due email non coincidono.")
            elif email == current.email:
                st.error("Non puoi eliminare il tuo stesso account.")
            else:
                try:
                    auth_service.delete_user(email)
                    st.success(f"Account {email} eliminato.")
                except auth_service.AuthError as exc:
                    st.error(str(exc))


def render_price_list_admin() -> None:
    """Listino dei pacchetti di crediti: candidature e prezzo, modificabili dall'admin."""
    st.markdown("#### Pacchetti e prezzi")
    packs = payment_service.credit_packs()
    table = pd.DataFrame([{"Candidature": p.credits, "Prezzo (€)": p.amount_cents / 100} for p in packs.values()])
    with st.form("admin_price_list", border=False):
        edited = st.data_editor(
            table, hide_index=True, num_rows="dynamic", key="admin_price_editor", use_container_width=True,
            column_config={
                "Candidature": st.column_config.NumberColumn(min_value=1, max_value=1000, step=1, required=True),
                "Prezzo (€)": st.column_config.NumberColumn(
                    min_value=payment_service.MIN_AMOUNT_CENTS / 100, max_value=1000, step=0.01, format="%.2f",
                    required=True),
            },
        )
        saved = st.form_submit_button("Salva prezzi", type="primary")
    if saved:
        rows = edited.dropna()
        try:
            payment_service.save_credit_packs(
                [(int(r["Candidature"]), round(float(r["Prezzo (€)"]) * 100)) for _, r in rows.iterrows()])
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("admin_price_editor", None)
            st.toast("Prezzi aggiornati.")
            st.rerun()
    st.caption(
        " · ".join(f"{p.label}: {p.price_label}" + (f" ({euro(p.unit_cents)} l'una)" if p.credits > 1 else "")
                   for p in packs.values())
        + f". In home page: **da {seo.price_from(packs)} a candidatura** (il prezzo più basso, arrotondato per "
        "difetto). I pagamenti già avviati restano al prezzo di quel momento.")


def render_admin_panel() -> None:
    st.markdown("### Pannello Admin")
    usage = auth_service.usage_stats()
    sales = auth_service.sales_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Chiamate a Claude", usage["total"], f"{usage['today']} oggi", delta_color="off")
    c2.metric("Vendite", sales["count"])
    c3.metric("Incasso", euro(sales["revenue_cents"]))
    c4.metric("Crediti venduti", sales["credits"])
    if usage["by_action"]:
        st.caption("Chiamate per tipo: " + " · ".join(f"{a} {n}" for a, n in usage["by_action"].items()))
    st.caption(f"**Stripe:** modalità {payment_service.stripe_mode_label()}. Vendite e incasso contano solo "
               "i pagamenti di questa modalità.")

    st.divider()
    st.markdown("#### Impostazioni")
    free_mode = st.toggle(
        "Uso gratuito senza AI",
        value=free_mode_enabled(),
        key="admin_free_mode",
        help="Gli utenti registrati possono compilare il CV a mano, correggerlo, cambiare stile e scaricare "
             "il PDF senza consumare crediti. Le funzioni con l'AI restano a pagamento.",
    )
    if free_mode != free_mode_enabled():
        auth_service.set_flag(auth_service.FLAG_FREE_MANUAL_MODE, free_mode)
        st.toast("Uso gratuito senza AI " + ("attivato." if free_mode else "disattivato."))

    # Chiave e modello riguardano chi gestisce l'app, non gli utenti.
    st.text_input(
        "Anthropic API Key (facoltativa)", type="password", placeholder="sk-ant-...",
        value=st.session_state.get("admin_api_key", ""), key="admin_api_key_input", on_change=store_admin_api_key,
        help="Vale solo per la tua sessione. Se vuota viene usata la variabile d'ambiente ANTHROPIC_API_KEY.",
    )
    if st.session_state.get("admin_api_key"):
        st.caption(f"Chiave inserita manualmente · modello `{config.CLAUDE_MODEL}`")
    elif config.get_env_api_key():
        st.caption(f"Chiave letta da ANTHROPIC_API_KEY · modello `{config.CLAUDE_MODEL}`")
    else:
        st.warning("Nessuna API key configurata.")

    st.divider()
    render_price_list_admin()

    st.divider()
    # Il form va prima della tabella, così dopo una modifica la tabella mostra già il nuovo saldo.
    render_credit_form()
    render_account_admin(current_user())

    st.markdown("#### Utenti registrati")
    users = auth_service.users_overview()
    table = pd.DataFrame([{
        "Email": u["email"],
        "Nome": " ".join(filter(None, (u["first_name"], u["last_name"]))),
        "Crediti": u["credits"],
        "Admin": bool(u["is_admin"]),
        "Email confermata": bool(u["email_verified"]),
        ARCHIVE_COLUMN: bool(u["can_save_applications"]),
        "Chiamate a Claude": u["claude_calls"],
        "Acquisti": u["purchases"],
        "Speso": euro(u["spent_cents"]),
        "Registrato il": u["created_at"],
    } for u in users])
    edited = st.data_editor(
        table,
        hide_index=True,
        use_container_width=True,
        key="admin_users_editor",
        disabled=[c for c in table.columns if c != ARCHIVE_COLUMN],
        column_config={ARCHIVE_COLUMN: st.column_config.CheckboxColumn(
            help="Attiva per questo utente la sezione «📁 Le mie candidature». Gli admin l'hanno sempre.",
        )},
    )
    # Solo la colonna dell'archivio è modificabile: si salvano le righe in cui è cambiata.
    for (_, before), (_, after) in zip(table.iterrows(), edited.iterrows()):
        if before[ARCHIVE_COLUMN] != after[ARCHIVE_COLUMN]:
            auth_service.set_can_save_applications(before["Email"], bool(after[ARCHIVE_COLUMN]))
            st.toast(f"«Le mie candidature» {'attivata' if after[ARCHIVE_COLUMN] else 'disattivata'} "
                     f"per {before['Email']}.")
    st.caption(f"{len(users)} utenti. Gli admin non consumano crediti. "
               "Le chiamate contano le operazioni con l'AI andate a buon fine. "
               f"Spunta «{ARCHIVE_COLUMN}» per attivare l'archivio delle candidature a un utente.")


# --- Avvio ----------------------------------------------------------------------------
STEP_RENDERERS = (render_step_cv, render_step_job, render_step_linkedin)


def render_payment_return(payment: str) -> bool:
    """Ritorno da Stripe, che si apre in una nuova scheda. True se la pagina deve fermarsi qui.

    In una scheda nuova non c'è il lavoro in corso: si invita a tornare alla scheda di partenza
    invece di ripartire da zero. Se la sessione ha già un CV basta un avviso.
    """
    if payment == "success":
        text = ("**Pagamento completato.** I crediti sono già sul tuo account: torna alla scheda di "
                f"{config.APP_TITLE} in cui stavi lavorando, oppure continua da qui.")
    else:
        text = "**Pagamento annullato:** non ti è stato addebitato nulla."
    if st.session_state.cv is not None:
        st.toast(text.replace("**", ""), icon="✅" if payment == "success" else "ℹ️")
        return False
    with st.container(border=True):
        st.markdown(text)
        st.button("Continua da qui", type="primary", key="payment_continue")
    return True


def main() -> None:
    auth_service.init_db()
    applications_service.init_db()
    init_state()
    restore_login()
    user = current_user()

    st.markdown(STEPPER_CSS, unsafe_allow_html=True)
    if config.STRIPE_TEST_MODE:
        st.markdown('<div class="cv-test-banner">🧪 <b>Modalità test</b> · ambiente di prova: i pagamenti sono '
                    'finti e non addebitano nulla</div>', unsafe_allow_html=True)
    render_topbar(user)
    emit_cookie_script()
    emit_scroll_script()
    if toast := st.session_state.pop("pending_toast", None):
        st.toast(toast, icon="✅")
    api_key = resolve_api_key(user)
    # Il modulo «Contattaci» si apre in ogni pagina, anche prima dell'accesso.
    if st.session_state.pop("open_contact", False):
        contact_dialog()
    elif st.session_state.pop("open_delete_account", False):
        delete_account_dialog()
    # Termini e privacy si aprono anche senza accesso, con un link del tipo ?doc=privacy.
    if (doc := st.query_params.get("doc")) in LEGAL_DOCS:
        render_legal_page(doc)
        return
    if doc == GUIDE_DOC:
        render_guide_page()
        return

    payment = st.query_params.pop("payment", None)
    if payment in ("success", "cancel") and user is not None and render_payment_return(payment):
        return
    if user is None:
        render_welcome_page()
        return
    if not user.email_verified:
        render_verify_email(user)
        return

    open_pending_dialogs()
    # CV, annuncio e LinkedIn finiscono nell'archivio a ogni passo, prima di mostrarlo.
    autosave_application()
    view = st.session_state.view
    if view == ARCHIVE_VIEW and user.has_applications_archive:
        render_applications()
        return
    if view == ADMIN_VIEW and user.is_admin:
        render_admin_panel()
        return
    st.session_state.view = GUIDED_VIEW

    # Se lo stato cambia (es. nuovo CV caricato) non si resta su un passo non più raggiungibile.
    while not step_unlocked(st.session_state.step):
        st.session_state.step -= 1

    step = st.session_state.step
    render_stepper(step)
    render_session_note()
    STEP_RENDERERS[step - 1](api_key)


if __name__ == "__main__":
    main()
