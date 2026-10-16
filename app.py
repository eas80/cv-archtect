"""CV Architect — percorso guidato: CV, offerta di lavoro, profilo LinkedIn."""

import io
import re

import pandas as pd
import streamlit as st
from jinja2 import Template
from pypdf import PdfReader
from pypdf.errors import PyPdfError

import config
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
    payment_service,
    ClaudeServiceError,
    ScraperError,
    extract_job_description_from_url,
    guess_language,
    render_cover_letter_pdf,
    render_cv_pdf,
)

st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.APP_ICON,
    layout="centered",
    # Accesso e crediti stanno nella sidebar: va mostrata subito.
    initial_sidebar_state="expanded",
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
</style>
"""


# --- Stato ------------------------------------------------------------------------
# Stato legato all'annuncio: va azzerato insieme al risultato del passo 2.
# saved_app_id: candidatura dell'archivio che corrisponde all'annuncio attuale.
JOB_KEYS = ("match", "match_prev", "job_text", "saved_app_id")


def init_state() -> None:
    for key in ("cv", "cv_source_text", "linkedin", *JOB_KEYS):
        st.session_state.setdefault(key, None)
    st.session_state.setdefault("step", 1)
    st.session_state.setdefault("job_skipped", False)


def reset_session() -> None:
    for key in ("cv", "cv_source_text", "linkedin", *JOB_KEYS):
        st.session_state[key] = None
    st.session_state.step = 1
    st.session_state.job_skipped = False


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


def skip_job() -> None:
    st.session_state.job_skipped = True
    st.session_state.step = 3


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


def set_session_cookie(token: str | None) -> None:
    """Prenota la scrittura (o, con None, la cancellazione) del cookie: la fa emit_cookie_script."""
    secure = "; Secure" if config.APP_BASE_URL.startswith("https://") else ""
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
    for key in ("user_id", "session_token", "recovery_code", "checkout", "known_credits"):
        st.session_state[key] = None


def show_recovery_code() -> None:
    """Codice di recupero appena generato: visibile finché l'utente non conferma di averlo salvato."""
    code = st.session_state.get("recovery_code")
    if not code:
        return
    st.warning(
        "**Salva questo codice di recupero.** Serve per reimpostare la password se la dimentichi "
        "e non verrà più mostrato."
    )
    st.code(code, language=None)
    if st.button("L'ho salvato", key="recovery_ack", use_container_width=True):
        st.session_state.recovery_code = None
        st.rerun()


def render_auth_forms() -> None:
    login_tab, register_tab, reset_tab = st.tabs(["Accedi", "Registrati", "Password dimenticata"])

    with login_tab, st.form("login_form"):
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.form_submit_button("Accedi", type="primary", use_container_width=True):
            try:
                sign_in(auth_service.login(email, password))
                st.rerun()
            except auth_service.AuthError as exc:
                st.error(str(exc))

    with register_tab, st.form("register_form"):
        if config.INITIAL_CREDITS:
            st.caption(f"Ogni nuovo account riceve {config.INITIAL_CREDITS} crediti.")
        elif free_mode_enabled():
            st.caption("La registrazione è gratuita: puoi sistemare il CV a mano e acquistare crediti per l'AI.")
        email = st.text_input("Email", key="register_email")
        password = st.text_input(
            "Password", type="password", key="register_password",
            help=f"Almeno {auth_service.MIN_PASSWORD_LENGTH} caratteri.",
        )
        confirm = st.text_input("Ripeti la password", type="password", key="register_confirm")
        if st.form_submit_button("Crea account", type="primary", use_container_width=True):
            if password != confirm:
                st.error("Le due password non coincidono.")
            else:
                try:
                    user, code = auth_service.register(email, password)
                    sign_in(user)
                    st.session_state.recovery_code = code
                    st.rerun()
                except auth_service.AuthError as exc:
                    st.error(str(exc))

    with reset_tab, st.form("reset_form"):
        st.caption("Usa il codice di recupero ricevuto alla registrazione (o all'ultimo reset).")
        email = st.text_input("Email", key="reset_email")
        code = st.text_input("Codice di recupero", placeholder="XXXX-XXXX-XXXX", key="reset_code")
        password = st.text_input("Nuova password", type="password", key="reset_password")
        confirm = st.text_input("Ripeti la nuova password", type="password", key="reset_confirm")
        if st.form_submit_button("Reimposta la password", type="primary", use_container_width=True):
            if password != confirm:
                st.error("Le due password non coincidono.")
            else:
                try:
                    new_code = auth_service.reset_password(email, code, password)
                    sign_in(auth_service.login(email, password))
                    st.session_state.recovery_code = new_code
                    st.rerun()
                except auth_service.AuthError as exc:
                    st.error(str(exc))


AI_BENEFITS = (
    "**Legge e riordina il tuo CV in un minuto**, invece di compilarlo campo per campo",
    "**Lo adatta all'annuncio** che ti interessa e ti dice quanto sei compatibile",
    "**Scrive la lettera di presentazione** su misura per l'annuncio",
    "**Prepara il profilo LinkedIn**: titolo, sezione Informazioni e post",
)


def needs_credits() -> bool:
    """True per chi non è admin e non ha crediti: le funzioni con l'AI sono bloccate."""
    user = current_user()
    return bool(user and not user.is_admin and user.credits < 1)


def price_hook() -> str:
    packs = payment_service.CREDIT_PACKS.values()
    cheapest = min(packs, key=lambda p: p.amount_cents)
    best = min(packs, key=lambda p: p.amount_cents / p.credits)
    return (f"Da {cheapest.price_label} per {cheapest.label}, oppure solo "
            f"{euro(round(best.amount_cents / best.credits))} a CV con il pacchetto da {best.label}.")


def render_buy_credits(user: auth_service.User, key: str = "buy") -> None:
    if not payment_service.payments_enabled():
        st.caption("Gli acquisti non sono ancora attivi.")
        return
    packs = payment_service.CREDIT_PACKS
    pack_id = st.radio(
        "Pacchetto", list(packs), key=f"{key}_pack", label_visibility="collapsed",
        index=len(packs) - 1,
        format_func=lambda k: f"{packs[k].label} — {packs[k].price_label}"
        + (f" ({euro(round(packs[k].amount_cents / packs[k].credits))} a CV)" if packs[k].credits > 1 else ""),
    )
    if st.button("Procedi al pagamento", key=f"{key}_go", type="primary", use_container_width=True):
        try:
            st.session_state.checkout = (pack_id, *payment_service.create_checkout(user, pack_id))
        except payment_service.PaymentError as exc:
            st.error(str(exc))
    checkout = st.session_state.get("checkout")
    if checkout and checkout[0] == pack_id:
        st.link_button("💳 Paga con Stripe", checkout[2], type="primary", use_container_width=True)
        st.caption("Il pagamento si apre in una nuova scheda: i crediti si attivano qui appena è confermato.")


@st.dialog("✨ Sblocca le funzioni AI")
def buy_dialog() -> None:
    if (user := current_user()) is None:
        return
    st.markdown("\n".join(f"- {b}" for b in AI_BENEFITS))
    st.caption("Ogni analisi o generazione con l'AI usa 1 credito.")
    render_buy_credits(user, key="dialog")


def render_upsell(title: str, key: str, free_hint: str | None = None) -> None:
    """Riquadro che invita all'acquisto, al posto di una funzione AI bloccata."""
    with st.container(border=True):
        st.markdown(f"#### ✨ {title}")
        st.markdown("\n".join(f"- {b}" for b in AI_BENEFITS))
        if payment_service.payments_enabled():
            st.caption(price_hook())
            if st.button("Acquista crediti", type="primary", use_container_width=True, key=f"{key}_upsell"):
                buy_dialog()
        else:
            st.caption("Gli acquisti non sono ancora attivi.")
        if free_hint:
            st.caption(free_hint)


@st.fragment(run_every=config.CREDITS_REFRESH_SECONDS)
def render_balance() -> None:
    """Saldo e acquisto crediti, riletti dal database a intervalli regolari."""
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
        st.info(f"Crediti disponibili: **{user.credits}**")
        st.caption("Ogni analisi o generazione con l'AI consuma 1 credito.")
        st.markdown("**Ricarica crediti**")
    else:
        if free_mode_enabled():
            st.warning("**Piano gratuito.** Puoi compilare e sistemare il CV a mano, scegliere lo stile e "
                       "scaricare il PDF. Le funzioni AI richiedono crediti.")
        else:
            st.warning("**Non hai crediti.** Acquistane per usare l'app.")
        st.markdown("**✨ Sblocca le funzioni AI**")
        st.caption(price_hook())
    render_buy_credits(user)


def render_account(user: auth_service.User) -> None:
    st.markdown(f"**{user.email}**")
    if user.is_admin:
        st.success("Admin · utilizzo illimitato")
    else:
        render_balance()
    show_recovery_code()
    st.button("Esci", use_container_width=True, on_click=sign_out, key="logout")


def render_sidebar(user: auth_service.User | None) -> str | None:
    with st.sidebar:
        st.header("Account")
        if user is None:
            render_auth_forms()
            return None
        render_account(user)
        st.divider()
        st.button(
            "Ricomincia da capo",
            use_container_width=True,
            on_click=reset_session,
            help="Cancella CV, documenti e testi LinkedIn e riparte dal passo 1.",
        )
        if not user.is_admin:
            return config.get_env_api_key()

        # La chiave e il modello riguardano chi gestisce l'app, non gli utenti.
        st.divider()
        st.header("Impostazioni")
        typed_key = st.text_input(
            "Anthropic API Key (facoltativa)",
            type="password",
            placeholder="sk-ant-...",
            help="Se vuota viene usata la variabile d'ambiente ANTHROPIC_API_KEY.",
        ).strip()
        api_key = typed_key or config.get_env_api_key()

        if typed_key:
            st.success("Chiave inserita manualmente.")
        elif api_key:
            st.info("Chiave letta da ANTHROPIC_API_KEY.")
        else:
            st.warning("Nessuna API key configurata.")

        st.caption(f"Modello: `{config.CLAUDE_MODEL}`")
    return api_key


def run_claude(api_key: str | None, action: str, spinner_text: str, call):
    """Esegue `call(service)` mostrando spinner ed eventuale errore. None se fallisce.

    Ogni chiamata riuscita scala 1 credito all'utente (non agli admin) e viene registrata
    come `action` nelle statistiche; se fallisce non costa nulla.
    """
    user = current_user()
    if user is None:
        st.error("Accedi dalla barra laterale per usare l'app.")
        return None
    if not user.is_admin and user.credits < 1:
        st.error("Hai esaurito i crediti: non puoi avviare nuove analisi o generazioni.")
        buy_dialog()
        return None
    if not api_key:
        st.error("Manca la API key Anthropic: aprila nella barra laterale a sinistra (») oppure impostala nel file .env.")
        return None
    try:
        service = ClaudeCVService(api_key=api_key)
        with st.spinner(spinner_text):
            result = call(service)
    except ClaudeServiceError as exc:
        st.error(str(exc))
        return None
    auth_service.consume_credit(user.id)
    auth_service.log_usage(user.id, action)
    return result


def render_stepper(current: int) -> None:
    parts = []
    for i, title in enumerate(STEP_TITLES, start=1):
        if i == current:
            state, dot = "current", str(i)
        elif step_done(i):
            state, dot = "done", "✓"
        elif i == 2 and st.session_state.job_skipped:
            state, dot = "skipped", "–"
        else:
            state, dot = "", str(i)
        parts.append(f'<div class="cv-step {state}"><div class="cv-dot">{dot}</div>'
                     f'<div class="cv-label">{title}</div></div>')
        if i < len(STEP_TITLES):
            bar_done = step_done(i) or (i == 2 and st.session_state.job_skipped)
            parts.append(f'<div class="cv-bar {"done" if bar_done else ""}"></div>')
    st.markdown(f'<div class="cv-stepper">{"".join(parts)}</div>', unsafe_allow_html=True)


def render_step_header(step: int, title: str, instructions: str) -> None:
    if step > 1:
        st.button(f"← Torna a «{STEP_TITLES[step - 2]}»", key=f"back_{step}", type="tertiary",
                  on_click=go_to, args=(step - 1,))
    st.markdown(f"### Passo {step} di 3 · {title}")
    st.markdown(instructions)


def next_button(label: str, step: int, key: str) -> None:
    st.button(f"{label} →", key=key, type="primary", use_container_width=True, on_click=go_to, args=(step,))


def render_resume_review(cv: ResumeData, key: str, on_save) -> None:
    """Anteprima del CV; con l'interruttore attivo, editor completo al suo posto."""
    toggle_key = f"{key}_edit_toggle"
    if edit_toggle("✏️ Voglio correggere o aggiungere qualcosa", toggle_key):
        edit_resume(cv, key=key, on_save=on_save, on_done=lambda: close_toggle(toggle_key))
    else:
        with st.container(border=True):
            st.markdown(CV_MARKDOWN_TEMPLATE.render(cv=cv))


def pdf_file_name(prefix: str, cv: ResumeData) -> str:
    slug = "_".join(cv.full_name.split()) or "cv"
    return f"{prefix}_{slug}.pdf"


def render_pdf_downloads(cv: ResumeData, key: str, cover_letter: str | None = None) -> None:
    """Scelta di stile e lingua dei titoli, poi pulsanti di download PDF."""
    c1, c2 = st.columns([3, 2])
    template = c1.selectbox(
        "Stile del documento",
        list(TEMPLATES),
        index=list(TEMPLATES).index("moderno"),
        format_func=lambda k: TEMPLATES[k].label,
        key=f"{key}_template",
    )
    default_lang = guess_language(cv.professional_summary, *(h for e in cv.experiences for h in e.highlights))
    language = c2.selectbox(
        "Lingua dei titoli",
        list(LANGUAGES),
        index=list(LANGUAGES).index(default_lang),
        format_func=LANGUAGES.get,
        key=f"{key}_language",
        help="Lingua dei titoli delle sezioni («Esperienza» / «Experience»). Usa la stessa lingua del CV.",
    )
    st.caption(f"{TEMPLATES[template].description} Tutti gli stili sono leggibili dai sistemi ATS.")

    # PDF generati solo al clic: l'archivio candidature mostra molti pulsanti per pagina.
    d1, d2 = st.columns(2)
    d1.download_button(
        "📥 Scarica il CV (PDF)",
        data=lambda: render_cv_pdf(cv, template, language),
        file_name=pdf_file_name("CV", cv),
        mime="application/pdf",
        type="primary",
        use_container_width=True,
        key=f"{key}_cv_pdf",
    )
    if cover_letter:
        d2.download_button(
            "📥 Scarica la lettera (PDF)",
            data=lambda: render_cover_letter_pdf(cv, cover_letter, template, language),
            file_name=pdf_file_name("Lettera", cv),
            mime="application/pdf",
            type="primary",
            use_container_width=True,
            key=f"{key}_letter_pdf",
        )


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


def set_new_cv(cv: ResumeData, source_text: str | None = None) -> None:
    """Sostituisce il CV di partenza; `source_text` è il testo originale per chi compila a mano."""
    st.session_state.cv = cv
    st.session_state.cv_source_text = source_text
    # I risultati derivati dal CV precedente non sono più validi.
    for k in JOB_KEYS:
        st.session_state[k] = None
    st.session_state.linkedin = None
    st.session_state.job_skipped = False
    bump("base")


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
            ["✨ Con l'AI — consigliato: legge e riordina il CV per te (1 credito)",
             "✍️ A mano, senza AI — gratis: compili tu i campi"],
            key=f"{key}_mode",
        )
        if mode.startswith("✍️"):
            render_manual_cv_input(key)
            return
    if needs_credits():
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
    clicked = st.button("Leggi il mio CV", type="primary", use_container_width=True, key=f"{key}_go")
    st.caption("Di solito ci vuole meno di un minuto.")
    if not clicked:
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

    cv = run_claude(api_key, "Lettura CV", "Sto leggendo il tuo CV...",lambda s: s.parse_unstructured_cv(raw_text))
    if cv is not None:
        set_new_cv(cv)
        st.rerun()


def render_step_cv(api_key: str | None) -> None:
    cv: ResumeData | None = st.session_state.cv
    if cv is None:
        render_step_header(
            1, "Carica il tuo CV",
            "Carica il CV che hai già: lo riordiniamo in un formato pulito, adatto ai sistemi "
            "di selezione automatica dei candidati (ATS).",
        )
        render_cv_input(api_key, key="cv_input")
        return

    source_text = st.session_state.get("cv_source_text")
    if source_text is None:
        render_step_header(1, "Controlla il tuo CV", "Ecco cosa abbiamo letto. Controlla che sia tutto giusto, poi vai avanti.")
        skills = len(cv.hard_skills) + len(cv.soft_skills)
        st.success(
            f"**CV di {cv.full_name} letto correttamente**: {len(cv.experiences)} esperienze, "
            f"{len(cv.education)} titoli di studio, {skills} competenze."
        )
    else:
        render_step_header(1, "Compila il tuo CV", "Completa i campi del modulo, poi scegli lo stile e scarica il PDF.")
        if source_text:
            with st.expander("📄 Testo del CV che hai caricato (da copiare nei campi)"):
                st.code(source_text, language=None, wrap_lines=True)
    with st.expander("👀 Guarda e correggi il CV", expanded=source_text is not None):
        st.caption("Le correzioni valgono anche per i passi successivi.")
        render_resume_review(cv, key="base", on_save=lambda edited: st.session_state.update(cv=edited))
    with st.expander("📥 Scarica questo CV in PDF", expanded=source_text is not None):
        render_pdf_downloads(cv, key="base")
        st.download_button(
            "💾 Salva il CV per modificarlo in seguito (JSON)", data=cv.model_dump_json(indent=2),
            file_name=pdf_file_name("CV", cv).replace(".pdf", ".json"), mime="application/json", key="base_json",
        )
        st.caption("Il file JSON si può ricaricare in questa app per riprendere il CV da dove l'hai lasciato.")

    if needs_credits():
        render_upsell("Il CV è pronto: ora candidati con l'AI", key="cv_next")

    next_button("Avanti: scegli l'offerta di lavoro", 2, key="next_1")

    st.write("")
    with st.expander("🔄 Vuoi usare un altro CV?"):
        st.caption("Il nuovo CV sostituisce quello attuale e tutto ciò che è stato preparato dopo.")
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
    clicked = st.button("Prepara CV e lettera per questo annuncio", type="primary", use_container_width=True,
                        key=f"{key}_go")
    st.caption("Di solito ci vogliono uno o due minuti.")
    if not clicked:
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
    job_text = job_text.strip()
    if not job_text:
        st.warning("Incolla il testo dell'annuncio.")
        return

    match = run_claude(
        api_key,
        "Adattamento all'annuncio",
        "Sto confrontando il tuo CV con l'annuncio e scrivendo la lettera...",
        lambda s: s.tailor_cv_for_job(cv, job_text),
    )
    if match is not None:
        st.session_state.match = match
        st.session_state.match_prev = None
        st.session_state.job_text = job_text
        st.session_state.job_skipped = False
        bump("tailored")
        bump("letter")
        st.rerun()


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
    rev = st.session_state.get("revise_rev", 0)
    instructions = st.text_area(
        "Cosa vuoi cambiare?", height=130, key=f"revise_{rev}_text",
        placeholder="Descrivi la modifica...",
    )
    scope = st.radio(
        "Cosa modificare", list(REVISION_SCOPES), format_func=lambda k: REVISION_SCOPES[k][0],
        horizontal=True, key="revise_scope",
    )
    if not st.button("Applica la modifica", type="primary", use_container_width=True, key="revise_go"):
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
        else:
            render_job_input(api_key, cv, key="job_input")
        st.divider()
        st.markdown("**Non hai ancora un annuncio?** Puoi saltare questo passo e preparare subito il profilo LinkedIn.")
        st.button("Salta e vai al profilo LinkedIn", key="skip_job", on_click=skip_job)
        return

    render_step_header(
        2, "Il CV adattato e la lettera",
        "Ecco i documenti per questa candidatura. Controllali, correggi se serve e scaricali.",
    )
    st.success(f"**Il tuo CV corrisponde all'annuncio al {match.match_score}%.** {score_comment(match.match_score)}")
    st.progress(match.match_score / 100)

    with st.expander("🔑 Cosa chiede l'annuncio e cosa manca nel tuo CV"):
        render_keywords("Già presenti nel tuo CV", match.matching_keywords, "green")
        render_keywords("Mancanti nel tuo CV", match.missing_keywords, "red")
        st.caption(
            "Le competenze mancanti non vengono aggiunte in automatico. Se le hai davvero, aggiungile al CV "
            "adattato con «Voglio correggere o aggiungere qualcosa»."
        )

    with st.expander("✉️ Lettera di presentazione"):
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

    with st.expander("📄 CV adattato all'annuncio"):
        render_resume_review(
            match.tailored_cv, key="tailored",
            on_save=lambda edited: st.session_state.update(
                match=st.session_state.match.model_copy(update={"tailored_cv": edited})
            ),
        )

    if changes := st.session_state.pop("revise_changes", None):
        st.success("**Modifica applicata.** Riapri CV e lettera per controllarli.\n\n"
                   + "\n".join(f"- {c}" for c in changes))
    if st.session_state.match_prev is not None:
        st.button("↩️ Annulla l'ultima modifica dell'AI", key="revise_undo", on_click=undo_revision)
    # Valore stabile tra un rerun e l'altro: se `expanded` cambiasse, l'expander si richiuderebbe mentre si scrive.
    reopened = st.session_state.get("saved_app_id") is not None and         st.session_state.get("reopened_app_id") == st.session_state.get("saved_app_id")
    if reopened:
        st.info("**Candidatura riaperta dall'archivio.** Correggi CV e lettera qui sopra, oppure scrivi all'AI "
                "cosa cambiare qui sotto. Ogni modifica viene salvata nella candidatura.")
    with st.expander("🤖 Chiedi all'AI di modificare CV e lettera", expanded=reopened):
        render_ai_revision(api_key, cv, match)

    if archive_enabled():
        st.markdown("#### 💾 Salva la candidatura")
        render_save_application(match)

    st.markdown("#### 📥 Scarica i documenti")
    render_pdf_downloads(match.tailored_cv, key="tailored", cover_letter=match.cover_letter)
    with st.expander("Altri formati (testo, Markdown, JSON)"):
        st.download_button("Lettera (.txt)", data=match.cover_letter, file_name="lettera_presentazione.txt")
        st.download_button("CV (Markdown)", data=CV_MARKDOWN_TEMPLATE.render(cv=match.tailored_cv),
                           file_name="cv_adattato.md", mime="text/markdown")
        st.download_button("CV (JSON)", data=match.tailored_cv.model_dump_json(indent=2),
                           file_name="cv_adattato.json", mime="application/json")

    st.divider()
    next_button("Avanti: prepara il profilo LinkedIn", 3, key="next_2")

    st.write("")
    with st.expander("🔄 Vuoi provare con un altro annuncio?"):
        st.caption("Il nuovo annuncio sostituisce CV adattato e lettera attuali: scaricali prima se ti servono.")
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
    clicked = st.button("Scrivi i testi LinkedIn", type="primary", use_container_width=True,
                        disabled=not tones, key=f"{key}_go")
    st.caption("Le versioni vengono scritte in contemporanea: di solito ci vuole circa un minuto.")
    if not clicked:
        return

    versions = run_claude(
        api_key,
        "Testi LinkedIn",
        "Sto scrivendo i testi per LinkedIn..." if len(tones) == 1 else f"Sto scrivendo {len(tones)} versioni dei testi LinkedIn...",
        lambda s: s.generate_linkedin_versions(cv, tones),
    )
    if versions is not None:
        st.session_state.linkedin = versions
        for tone in versions:
            bump(f"linkedin_{tone}")
        st.rerun()


def render_step_linkedin(api_key: str | None) -> None:
    cv: ResumeData = st.session_state.cv
    versions: dict[str, LinkedInContent] | None = st.session_state.linkedin
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
        return

    render_step_header(
        3, "Il tuo profilo LinkedIn",
        "Copia ogni testo con l'icona 📋 in alto a destra del riquadro e incollalo nel tuo profilo LinkedIn.",
    )
    if (saved := current_saved_application()) is not None:
        st.caption(f"💾 Questi testi sono salvati anche nella candidatura «{saved.title}».")
    if len(versions) > 1:
        st.info("Hai più versioni: passa dall'una all'altra con le schede qui sotto.")

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

    st.divider()
    st.success("🎉 **Hai finito!** Hai il CV, i documenti per la candidatura e i testi per LinkedIn.")
    with st.expander("🔄 Vuoi altre versioni dei testi LinkedIn?"):
        st.caption("Le nuove versioni sostituiscono quelle attuali.")
        render_linkedin_input(api_key, cv, key="li_reinput")
    st.button("Ricomincia con un nuovo CV", key="restart", on_click=reset_session)


# --- Le mie candidature ----------------------------------------------------------------
GUIDED_VIEW, ARCHIVE_VIEW, ADMIN_VIEW = "🧭 Percorso guidato", "📁 Le mie candidature", "🛠️ Pannello Admin"


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


def saved_fingerprint() -> tuple:
    match: JobMatchResult = st.session_state.match
    linkedin = st.session_state.linkedin or {}
    return (match.model_dump_json(), st.session_state.cv.model_dump_json(),
            tuple((tone, c.model_dump_json()) for tone, c in linkedin.items()))


def sync_saved_application() -> None:
    """Riporta nella candidatura salvata le modifiche fatte dopo il salvataggio (CV, lettera, LinkedIn)."""
    app_id = st.session_state.get("saved_app_id")
    if not app_id or st.session_state.match is None or not archive_enabled():
        return
    fingerprint = saved_fingerprint()
    if fingerprint == st.session_state.get("saved_app_fingerprint"):
        return
    match: JobMatchResult = st.session_state.match
    store = app_store()
    if store.update_documents(app_id, match, st.session_state.cv):
        store.update_linkedin(app_id, st.session_state.linkedin)
        st.session_state.saved_app_fingerprint = fingerprint
    else:
        # Eliminata dall'archivio nel frattempo.
        st.session_state.saved_app_id = None


def render_save_application(match: JobMatchResult) -> None:
    if (saved := current_saved_application()) is not None:
        st.success(f"Salvata in «📁 Le mie candidature» come **{saved.title}**. Le modifiche che fai qui e i "
                   "testi LinkedIn del passo 3 vengono aggiunti in automatico.")
        return
    st.caption("Ritroverai CV, lettera e testi LinkedIn in «📁 Le mie candidature», senza usare di nuovo l'AI.")
    with st.form("save_application_form"):
        c1, c2 = st.columns(2)
        company = c1.text_input("Azienda", placeholder="es. Generali")
        role = c2.text_input("Ruolo", placeholder="es. QA Specialist")
        if st.form_submit_button("💾 Salva nelle mie candidature", type="primary", use_container_width=True):
            if not (company.strip() and role.strip()):
                st.warning("Indica sia l'azienda sia il ruolo.")
                return
            st.session_state.saved_app_id = app_store().save(
                company, role, match, st.session_state.cv, st.session_state.job_text, st.session_state.linkedin,
            )
            st.session_state.saved_app_fingerprint = saved_fingerprint()
            st.session_state.pending_toast = f"Candidatura «{company.strip()} - {role.strip()}» salvata."
            st.rerun()


def format_date(timestamp: str) -> str:
    year, month, day = timestamp[:10].split("-")
    return f"{day}/{month}/{year}"


def delete_application(app_id: int) -> None:
    if app_store().delete(app_id):
        if st.session_state.get("saved_app_id") == app_id:
            st.session_state.saved_app_id = None
        st.session_state.pending_toast = "Candidatura eliminata."


def reopen_application(app_id: int) -> None:
    """Riporta la candidatura nel percorso guidato (passo 2), collegata all'archivio."""
    if (app := app_store().get(app_id)) is None:
        return
    reset_session()
    st.session_state.cv = app.base_cv or app.cv
    st.session_state.match = app.match
    # Le candidature salvate prima di questa funzione non hanno il testo dell'annuncio.
    st.session_state.job_text = app.job_text or f"Candidatura per il ruolo {app.job_title} presso {app.company_name}."
    st.session_state.linkedin = app.linkedin or None
    st.session_state.saved_app_id = app.id
    st.session_state.saved_app_fingerprint = saved_fingerprint()
    st.session_state.step = 2
    st.session_state.main_view = GUIDED_VIEW
    for key in ("base", "tailored", "letter", "revise", *(f"linkedin_{tone}" for tone in app.linkedin)):
        bump(key)
    st.session_state.pending_toast = f"Candidatura «{app.title}» riaperta."
    st.session_state.reopened_app_id = app.id


def render_saved_application(app: applications_service.SavedApplication) -> None:
    with st.container(border=True):
        st.markdown("**✏️ Vuoi cambiarla?** Riaprila nel percorso guidato: puoi correggerla a mano oppure "
                    "scrivere all'AI cosa modificare (1 credito). Le modifiche si salvano qui in automatico.")
        if st.session_state.cv is not None and st.session_state.get("saved_app_id") != app.id:
            st.caption("Sostituisce il lavoro aperto ora nel percorso guidato.")
        st.button("Riapri e modifica", type="primary", key=f"saved_{app.id}_reopen",
                  on_click=reopen_application, args=(app.id,))
    render_pdf_downloads(app.cv, key=f"saved_{app.id}", cover_letter=app.cover_letter)
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
    with st.popover("🗑️ Elimina candidatura"):
        st.markdown("Vuoi eliminare questa candidatura? L'operazione non si può annullare.")
        st.button("Sì, elimina", type="primary", key=f"saved_{app.id}_delete",
                  on_click=delete_application, args=(app.id,))


def render_applications() -> None:
    st.markdown("### 📁 Le mie candidature")
    applications = app_store().list()
    if not applications:
        st.info("Non hai ancora candidature salvate. Al passo 2 del percorso guidato, dopo aver preparato CV "
                "e lettera, usa «Salva nelle mie candidature».")
        return
    st.caption(f"{len(applications)} candidature salvate. Riaprirle e scaricarle non consuma crediti.")
    for app in applications:
        score = f" · {app.match_score}%" if app.match_score is not None else ""
        with st.expander(f"🏢 **{app.title}** · {format_date(app.created_at)}{score}"):
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

    st.divider()
    # Il form va prima della tabella, così dopo una modifica la tabella mostra già il nuovo saldo.
    render_credit_form()

    st.markdown("#### Utenti registrati")
    users = auth_service.users_overview()
    table = pd.DataFrame([{
        "Email": u["email"],
        "Crediti": u["credits"],
        "Admin": bool(u["is_admin"]),
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


def main() -> None:
    auth_service.init_db()
    applications_service.init_db()
    init_state()
    restore_login()
    emit_cookie_script()
    if toast := st.session_state.pop("pending_toast", None):
        st.toast(toast, icon="✅")
    user = current_user()
    api_key = render_sidebar(user)

    st.markdown(STEPPER_CSS, unsafe_allow_html=True)
    st.title(f"{config.APP_ICON} {config.APP_TITLE}")
    st.caption("In tre passi: carica il CV, scegli un'offerta di lavoro, prepara il profilo LinkedIn.")
    # Ritorno dalla pagina di pagamento Stripe (si apre in una nuova scheda).
    payment = st.query_params.pop("payment", None)
    if payment == "success":
        st.success("**Pagamento completato.** I crediti compaiono nella barra laterale della scheda in cui hai "
                   "acquistato entro pochi secondi: puoi chiudere questa scheda.")
    elif payment == "cancel":
        st.info("Pagamento annullato: non ti è stato addebitato nulla.")
    if user is None:
        st.info("👈 **Accedi o crea un account** dalla barra laterale per iniziare.")
        return

    sections = ([GUIDED_VIEW] + ([ARCHIVE_VIEW] if user.has_applications_archive else [])
                + ([ADMIN_VIEW] if user.is_admin else []))
    # Valore iniziale via session_state (non `default`): la riapertura di una candidatura cambia sezione da codice.
    if st.session_state.get("main_view") not in sections:
        st.session_state.main_view = GUIDED_VIEW
    view = st.segmented_control("Sezione", sections, key="main_view", label_visibility="collapsed")
    # Le modifiche fatte dopo il salvataggio finiscono nell'archivio prima di mostrarlo.
    sync_saved_application()
    if view == ARCHIVE_VIEW and user.has_applications_archive:
        render_applications()
        return
    if view == ADMIN_VIEW and user.is_admin:
        render_admin_panel()
        return

    # Se lo stato cambia (es. nuovo CV caricato) non si resta su un passo non più raggiungibile.
    while not step_unlocked(st.session_state.step):
        st.session_state.step -= 1

    step = st.session_state.step
    render_stepper(step)
    STEP_RENDERERS[step - 1](api_key)


if __name__ == "__main__":
    main()
