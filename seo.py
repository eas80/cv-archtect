"""SEO: pagina iniziale con meta tag e testo leggibile dai motori di ricerca, robots.txt e sitemap.xml.

Streamlit costruisce la pagina via JavaScript e il suo index.html è vuoto («Streamlit», nessuna
descrizione). Qui lo si serve con titolo, descrizione, anteprime social (Open Graph), dati
strutturati (schema.org) e, dentro #root, un testo statico sul servizio: chi apre il sito lo vede
per un istante, poi l'app lo sostituisce; i motori di ricerca lo leggono subito.
"""

import functools
import html
import json
import os
from pathlib import Path

import streamlit
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

import config
from services.payment_service import CREDIT_PACKS

TITLE = "CV Architect – CV su misura con l'AI per ogni annuncio di lavoro"
DESCRIPTION = (
    "Carica il tuo curriculum: l'Intelligenza Artificiale lo adatta a ogni offerta di lavoro, "
    "lo ottimizza per gli ATS, scrive la lettera di presentazione e prepara il profilo LinkedIn. "
    "Da 3,99 € a candidatura, senza abbonamento."
)
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
}

# Domande frequenti: mostrate anche nella pagina di benvenuto dell'app.
FAQ = [
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
     "(CV adattato, lettera di presentazione e testi LinkedIn). I pacchetti partono da 3,99 € e i crediti "
     "valgono 24 mesi."),
    ("In che formati posso scaricare il CV?",
     "Il CV e la lettera di presentazione si scaricano in PDF, con diversi modelli grafici tra cui "
     "scegliere. Sono disponibili anche i formati testo, Markdown e JSON."),
    ("I miei dati sono al sicuro?",
     "I dati sono conservati su server nell'Unione Europea e non vengono usati per addestrare modelli di "
     "Intelligenza Artificiale. Puoi eliminare l'account e tutti i dati in qualsiasi momento dal menu."),
]

_STATIC_DIR = Path(streamlit.__file__).parent / "static"
_ASSETS_DIR = Path(__file__).parent / "assets"


def _price(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",")


def _structured_data(base: str) -> str:
    prices = [p.amount_cents for p in CREDIT_PACKS.values()]
    data = [
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": config.APP_TITLE,
            "url": base + "/",
            "description": DESCRIPTION,
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
                for q, a in FAQ
            ],
        },
    ]
    # «</» dentro uno <script> chiuderebbe il tag.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _head(base: str, title: str, description: str, url: str) -> str:
    e = html.escape
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
    <link rel="apple-touch-icon" href="{base}/icon-180.png" />
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
    <script type="application/ld+json">{_structured_data(base)}</script>
    <style>
      .seo-landing {{ max-width: 720px; margin: 0 auto; padding: 48px 16px; color: #0F172A;
        font-family: "Source Sans", system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.6; }}
      .seo-landing h1 {{ font-size: 2rem; line-height: 1.25; margin: 0 0 12px; }}
      .seo-landing h2 {{ font-size: 1.3rem; margin: 32px 0 8px; }}
      .seo-landing h3 {{ font-size: 1.05rem; margin: 16px 0 4px; }}
      .seo-landing a {{ color: #2563EB; }}
      @media (prefers-color-scheme: dark) {{ .seo-landing {{ color: #F1F5F9; }} }}
    </style>"""


def _landing(base: str) -> str:
    e = html.escape
    packs = "".join(
        f"<li>{e(p.label)}: {_price(p.amount_cents)} €</li>" for p in CREDIT_PACKS.values()
    )
    faq = "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in FAQ)
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
      <p>Nessun abbonamento. Un credito vale una candidatura completa e resta valido 24 mesi.</p>
      <ul>{packs}</ul>
      <h2>Domande frequenti</h2>
      {faq}
      <p><a href="{base}/?doc=termini">Termini e condizioni</a> · <a href="{base}/?doc=privacy">Privacy e cookie</a></p>
    </main>"""


@functools.cache
def _streamlit_index() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@functools.cache
def _page(doc: str | None) -> str:
    base = config.APP_BASE_URL
    title, description = DOC_PAGES.get(doc, (TITLE, DESCRIPTION))
    url = f"{base}/?doc={doc}" if doc in DOC_PAGES else f"{base}/"
    page = _streamlit_index()
    page = page.replace('<html lang="en">', '<html lang="it">', 1)
    page = page.replace("<title>Streamlit</title>", _head(base, title, description, url), 1)
    page = page.replace('<link rel="shortcut icon" href="./favicon.png" />',
                        '<link rel="icon" type="image/png" href="./favicon.png" />', 1)
    page = page.replace("<noscript>You need to enable JavaScript to run this app.</noscript>",
                        "<noscript>Per usare CV Architect attiva JavaScript nel browser.</noscript>", 1)
    # Streamlit monta l'app in #root e ne sostituisce il contenuto.
    return page.replace('<div id="root"></div>', f'<div id="root">{_landing(base)}</div>', 1)


async def index(request: Request) -> Response:
    doc = request.query_params.get("doc")
    return HTMLResponse(_page(doc if doc in DOC_PAGES else None), headers={"Cache-Control": "no-cache"})


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
    Route("/favicon.png", _asset("icon-32.png")),
    Route("/favicon.ico", _asset("icon-32.png")),
]
