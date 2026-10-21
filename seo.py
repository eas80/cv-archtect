"""SEO: pagina iniziale con meta tag e testo leggibile dai motori di ricerca, robots.txt e sitemap.xml.

Streamlit costruisce la pagina via JavaScript e il suo index.html è vuoto («Streamlit», nessuna
descrizione). Qui lo si serve con titolo, descrizione, anteprime social (Open Graph), dati
strutturati (schema.org) e, dentro #root, un testo statico sul servizio: chi apre il sito lo vede
per un istante, poi l'app lo sostituisce; i motori di ricerca lo leggono subito.
"""

import functools
import html
import json
from pathlib import Path

import streamlit
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

import config
from services.payment_service import CreditPack, credit_packs, format_euro, lowest_unit_cents

TITLE = "CV Architect – CV su misura con l'AI per ogni annuncio di lavoro"
KEYWORDS = (
    "curriculum vitae, CV, creare CV, CV con intelligenza artificiale, CV ATS, CV ottimizzato, "
    "lettera di presentazione, lettera motivazionale, profilo LinkedIn, candidatura, offerte di lavoro"
)

# Pagine dei documenti legali (?doc=...): titolo e descrizione propri.
DOC_PAGES = {
    "termini": ("Termini e condizioni – CV Architect",
                "Termini e condizioni di servizio di CV Architect: crediti, pagamenti, recesso e responsabilità."),
    "privacy": ("Privacy e cookie – CV Architect",
                "Informativa privacy e cookie di CV Architect: quali dati trattiamo, perché, per quanto tempo e i tuoi diritti."),
    "come-funziona": ("Come funziona – CV Architect",
                      "Come funziona CV Architect passo per passo: CV letto dall'AI, compatibilità con l'annuncio, "
                      "lettera e LinkedIn."),
}


def price_from(packs: dict[str, CreditPack] | None = None) -> str:
    """«1,99 €»: il prezzo più basso a candidatura del listino attuale."""
    return format_euro(lowest_unit_cents(packs))


def description(packs: dict[str, CreditPack] | None = None) -> str:
    # 120-160 caratteri: oltre, Google la taglia nei risultati di ricerca.
    return ("Crea con l'AI un CV su misura per ogni annuncio, ottimizzato per gli ATS, con lettera di "
            f"presentazione e profilo LinkedIn. Da {price_from(packs)} a candidatura.")


def faq(packs: dict[str, CreditPack] | None = None) -> list[tuple[str, str]]:
    """Domande frequenti: mostrate anche nella pagina di benvenuto dell'app."""
    return [
        ("Come funziona CV Architect?",
         "Carichi il tuo CV in PDF o incolli il testo: l'AI lo riordina in un formato pulito. Poi incolli "
         "l'annuncio (o il suo link) e ottieni un CV adattato all'offerta, un indice di compatibilità e la "
         "lettera di presentazione. Infine puoi generare titolo, sezione Informazioni e post per LinkedIn."),
        ("Che cos'è un CV ottimizzato per gli ATS?",
         "Molte aziende usano software di selezione (ATS) che leggono il CV in automatico. CV Architect crea "
         "documenti con struttura semplice e le parole chiave dell'annuncio, così il CV viene letto "
         "correttamente e risulta pertinente."),
        ("Quanto costa?",
         "Non c'è abbonamento: compri crediti quando ti servono. Un credito vale una candidatura completa "
         "(CV adattato, indice di compatibilità, lettera di presentazione e testi LinkedIn). Si parte da "
         f"{price_from(packs)} a candidatura e i crediti valgono 24 mesi."),
        ("Quando si usa il credito? Posso modificare la candidatura dopo averla scaricata?",
         "Il credito si scala solo alla consegna: quando scarichi o salvi i documenti, o generi i testi LinkedIn. "
         "Fino ad allora puoi correggere, chiedere modifiche all'AI e tradurre in inglese quanto vuoi. Dopo la "
         "consegna scaricare i documenti e correggerli a mano è sempre gratis; ogni altra elaborazione con l'AI "
         "(nuove modifiche, traduzioni, altri testi LinkedIn) crea una nuova versione della candidatura e usa un "
         "nuovo credito. L'app ti avvisa sempre prima."),
        ("In che formati posso scaricare il CV?",
         "Il CV e la lettera di presentazione si scaricano in PDF, in italiano o in inglese, con diversi modelli "
         "grafici tra cui scegliere. Alla fine del percorso trovi tutti i documenti insieme, anche in un unico "
         "file ZIP. Sono disponibili anche i formati testo, Markdown e JSON."),
        ("I miei dati sono al sicuro?",
         "I dati sono conservati su server nell'Unione Europea e non vengono usati per addestrare modelli di "
         "Intelligenza Artificiale. Puoi eliminare l'account e tutti i dati in qualsiasi momento dal menu."),
    ]


_STATIC_DIR = Path(streamlit.__file__).parent / "static"
_ASSETS_DIR = Path(__file__).parent / "assets"


def _structured_data(base: str, packs: dict[str, CreditPack]) -> str:
    prices = [p.amount_cents for p in packs.values()]
    data = [
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": config.APP_TITLE,
            "url": base + "/",
            "description": description(packs),
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web",
            "inLanguage": "it",
            "image": base + "/og-image.png",
            "offers": {
                "@type": "AggregateOffer",
                "priceCurrency": "EUR",
                "lowPrice": f"{min(prices) / 100:.2f}",
                "highPrice": f"{max(prices) / 100:.2f}",
                "offerCount": len(prices),
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in faq(packs)
            ],
        },
    ]
    # «</» dentro uno <script> chiuderebbe il tag.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _head(base: str, title: str, description: str, url: str, packs: dict[str, CreditPack]) -> str:
    def e(text: str) -> str:  # apostrofi in chiaro: alcuni strumenti SEO leggono il sorgente così com'è
        return html.escape(text, quote=False).replace('"', "&quot;")
    robots = "noindex, nofollow" if config.SEO_NOINDEX else "index, follow"
    verification = (f'<meta name="google-site-verification" content="{e(config.GOOGLE_SITE_VERIFICATION)}" />'
                    if config.GOOGLE_SITE_VERIFICATION else "")
    return f"""<title>{e(title)}</title>
    <meta name="description" content="{e(description)}" />
    <meta name="keywords" content="{e(KEYWORDS)}" />
    <meta name="robots" content="{robots}" />
    <meta name="theme-color" content="#2563EB" />
    {verification}
    <link rel="canonical" href="{e(url)}" />
    <link rel="icon" href="{base}/favicon.ico" sizes="16x16 32x32 48x48" />
    <link rel="icon" type="image/png" sizes="48x48" href="{base}/icon-48.png" />
    <link rel="icon" type="image/png" sizes="512x512" href="{base}/icon-512.png" />
    <link rel="apple-touch-icon" sizes="180x180" href="{base}/icon-180.png" />
    <meta property="og:type" content="website" />
    <meta property="og:locale" content="it_IT" />
    <meta property="og:site_name" content="{e(config.APP_TITLE)}" />
    <meta property="og:title" content="{e(title)}" />
    <meta property="og:description" content="{e(description)}" />
    <meta property="og:url" content="{e(url)}" />
    <meta property="og:image" content="{base}/og-image.png" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta property="og:image:alt" content="CV Architect: il tuo CV su misura per ogni annuncio di lavoro" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{e(title)}" />
    <meta name="twitter:description" content="{e(description)}" />
    <meta name="twitter:image" content="{base}/og-image.png" />
    <script type="application/ld+json">{_structured_data(base, packs)}</script>
    <style>
      .seo-landing {{ max-width: 720px; margin: 0 auto; padding: 48px 16px; color: #0F172A;
        font-family: "Source Sans", system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.6; }}
      .seo-landing h1 {{ font-size: 2rem; line-height: 1.25; margin: 0 0 12px; }}
      .seo-landing h2 {{ font-size: 1.3rem; margin: 32px 0 8px; }}
      .seo-landing h3 {{ font-size: 1.05rem; margin: 16px 0 4px; }}
      .seo-landing a {{ color: #2563EB; }}
      @media (prefers-color-scheme: dark) {{ .seo-landing {{ color: #F1F5F9; }} }}
    </style>"""


def _landing(base: str, packs: dict[str, CreditPack]) -> str:
    e = html.escape
    price_list = "".join(
        f"<li>{e(p.label)}: {e(p.price_label)}"
        + (f" ({e(format_euro(p.unit_cents))} a candidatura)" if p.credits > 1 else "") + "</li>"
        for p in packs.values()
    )
    questions = "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in faq(packs))
    return f"""<main class="seo-landing">
      <h1>Il tuo CV su misura per ogni annuncio di lavoro</h1>
      <p>{e(config.APP_TITLE)} usa l'Intelligenza Artificiale per creare un curriculum vitae ottimizzato per
      ogni offerta: carichi il CV una volta, incolli l'annuncio e ottieni CV adattato, lettera di
      presentazione e testi per il profilo LinkedIn.</p>
      <h2>Come funziona</h2>
      <ol>
        <li><strong>Carica il CV</strong> in PDF o come testo: lo riordiniamo in un formato chiaro e leggibile dagli ATS.</li>
        <li><strong>Scegli l'annuncio</strong>: ottieni il CV adattato all'offerta, l'indice di compatibilità e la lettera di presentazione.</li>
        <li><strong>LinkedIn</strong>: titolo, sezione Informazioni e post pronti da pubblicare.</li>
      </ol>
      <h2>Prezzi</h2>
      <p>Nessun abbonamento: da {e(price_from(packs))} a candidatura. Un credito vale una candidatura
      completa e resta valido 24 mesi.</p>
      <ul>{price_list}</ul>
      <h2>Domande frequenti</h2>
      {questions}
      <p><a href="{base}/?doc=termini">Termini e condizioni</a> · <a href="{base}/?doc=privacy">Privacy e cookie</a></p>
    </main>"""


@functools.cache
def _streamlit_index() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=16)
def _page(doc: str | None, pack_list: tuple[CreditPack, ...]) -> str:
    """Pagina per il documento e il listino dati (cambia listino → nuova pagina in cache)."""
    base = config.APP_BASE_URL
    packs = {f"cv{p.credits}": p for p in pack_list}
    title, desc = DOC_PAGES.get(doc, (TITLE, description(packs)))
    url = f"{base}/?doc={doc}" if doc in DOC_PAGES else f"{base}/"
    page = _streamlit_index()
    page = page.replace('<html lang="en">', '<html lang="it">', 1)
    page = page.replace("<title>Streamlit</title>", _head(base, title, desc, url, packs), 1)
    # Le icone del sito sono già nell'head: si toglie il logo di Streamlit.
    page = page.replace('<link rel="shortcut icon" href="./favicon.png" />', "", 1)
    page = page.replace("<noscript>You need to enable JavaScript to run this app.</noscript>",
                        "<noscript>Per usare CV Architect attiva JavaScript nel browser.</noscript>", 1)
    # Streamlit monta l'app in #root e ne sostituisce il contenuto.
    return page.replace('<div id="root"></div>', f'<div id="root">{_landing(base, packs)}</div>', 1)


async def index(request: Request) -> Response:
    doc = request.query_params.get("doc")
    packs = await run_in_threadpool(credit_packs)  # legge il database (al più ogni 30 secondi)
    return HTMLResponse(_page(doc if doc in DOC_PAGES else None, tuple(packs.values())),
                        headers={"Cache-Control": "no-cache"})


async def robots_txt(request: Request) -> Response:
    if config.SEO_NOINDEX:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(
        "User-agent: *\nAllow: /\nDisallow: /_stcore/\nDisallow: /stripe/\n\n"
        f"Sitemap: {config.APP_BASE_URL}/sitemap.xml\n"
    )


async def sitemap_xml(request: Request) -> Response:
    base = config.APP_BASE_URL
    urls = [f"{base}/"] + [f"{base}/?doc={doc}" for doc in DOC_PAGES]
    body = "".join(f"<url><loc>{html.escape(u)}</loc></url>" for u in urls)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        media_type="application/xml",
    )


def _asset(name: str):
    async def handler(request: Request) -> Response:
        return FileResponse(_ASSETS_DIR / name, headers={"Cache-Control": "public, max-age=86400"})
    return handler


ROUTES = [
    Route("/", index),
    Route("/robots.txt", robots_txt),
    Route("/sitemap.xml", sitemap_xml),
    Route("/og-image.png", _asset("og-image.png")),
    Route("/icon-180.png", _asset("icon-180.png")),
    Route("/icon-512.png", _asset("icon-512.png")),
    # Al posto del logo di Streamlit (lo usano i browser e Google nei risultati di ricerca).
    Route("/icon-48.png", _asset("icon-48.png")),
    Route("/favicon.png", _asset("icon-48.png")),
    Route("/favicon.ico", _asset("favicon.ico")),
]
