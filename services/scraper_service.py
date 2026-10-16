"""Estrazione del testo di un annuncio di lavoro da una pagina web."""

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config

_NOISE_TAGS = ["script", "style", "nav", "header", "footer", "noscript", "aside", "form", "iframe", "svg"]


class ScraperError(Exception):
    """Errore leggibile dall'utente durante il recupero dell'annuncio."""


def extract_job_description_from_url(url: str) -> str:
    """Scarica la pagina e restituisce il testo pulito dell'annuncio.

    Solleva ScraperError se l'URL non è valido, la pagina non è raggiungibile
    o il contenuto estratto è troppo scarso per essere un annuncio.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ScraperError("URL non valido: deve iniziare con http:// o https://.")

    try:
        response = requests.get(
            url,
            headers={"User-Agent": config.SCRAPER_USER_AGENT, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
            timeout=config.SCRAPER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ScraperError(
            f"Il sito ha risposto con errore HTTP {exc.response.status_code}. "
            "Incolla il testo dell'annuncio manualmente."
        ) from exc
    except requests.RequestException as exc:
        raise ScraperError(f"Impossibile raggiungere la pagina: {exc}") from exc

    soup = BeautifulSoup(response.content, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    # Il contenuto principale, quando marcato, esclude menu e sidebar residui.
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = _clean_text(root.get_text(separator="\n"))

    if len(text) < config.SCRAPER_MIN_TEXT_LENGTH:
        raise ScraperError(
            "Testo estratto insufficiente: la pagina richiede probabilmente login o JavaScript. "
            "Incolla il testo dell'annuncio manualmente."
        )
    return text


def _clean_text(raw: str) -> str:
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in raw.splitlines())
    return "\n".join(line for line in lines if line)
