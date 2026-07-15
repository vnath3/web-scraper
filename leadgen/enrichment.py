"""Directory enrichment for leads with no digital presence.

Scrapes public JustDial / IndiaMART listing pages (no official API exists
for either). This is inherently fragile: selectors are best-effort against
each site's current HTML and will need updating if the site's markup
changes. Treat parser breakage as expected maintenance, not a bug.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 2  # one initial attempt + at most one retry
MIN_DELAY_SECONDS = 3
MAX_DELAY_SECONDS = 7

JUSTDIAL_SEARCH_URL = "https://www.justdial.com/{location}/search/{query}"
INDIAMART_SEARCH_URL = "https://dir.indiamart.com/search.mp?ss={query}"


def _rate_limit_delay() -> None:
    time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))


def _fetch(url: str) -> Optional[requests.Response]:
    """Fetch a URL with a realistic UA, at most one retry, never raises."""
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning(
                "Enrichment request failed (attempt %d/%d) for %s: %s",
                attempt, MAX_ATTEMPTS, url, exc,
            )
            continue

        if response.ok:
            return response

        logger.warning(
            "Enrichment request returned status %d (attempt %d/%d) for %s",
            response.status_code, attempt, MAX_ATTEMPTS, url,
        )

    return None


def _empty_result(source: str, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "listed": False,
        "phone": None,
        "website": None,
        "directory_category": None,
        "source": source,
        "error": error,
    }


def parse_justdial_html(html: str, business_name: str) -> Dict[str, Any]:
    """Parse a JustDial search-results page. Pure function, network-free."""
    result = _empty_result("justdial")
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.resultbox")
        if not cards:
            return result

        card = None
        name_lower = business_name.lower()
        for candidate in cards:
            name_el = candidate.select_one(".store-name")
            if name_el and name_lower in name_el.get_text(strip=True).lower():
                card = candidate
                break
        if card is None:
            card = cards[0]

        result["listed"] = True

        phone_el = card.select_one(".contact-info .tel")
        result["phone"] = phone_el.get_text(strip=True) if phone_el else None

        website_el = card.select_one("a.website-link")
        result["website"] = website_el.get("href") if website_el else None

        category_el = card.select_one(".category")
        result["directory_category"] = (
            category_el.get_text(strip=True) if category_el else None
        )
    except Exception as exc:  # noqa: BLE001 - defensive: never let a bad page crash the batch
        logger.warning("Failed to parse JustDial HTML for '%s': %s", business_name, exc)
        return _empty_result("justdial", error=str(exc))

    return result


def parse_indiamart_html(html: str, business_name: str) -> Dict[str, Any]:
    """Parse an IndiaMART search-results page. Pure function, network-free."""
    result = _empty_result("indiamart")
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.listing-card")
        if not cards:
            return result

        card = None
        name_lower = business_name.lower()
        for candidate in cards:
            name_el = candidate.select_one(".company-name")
            if name_el and name_lower in name_el.get_text(strip=True).lower():
                card = candidate
                break
        if card is None:
            card = cards[0]

        result["listed"] = True

        phone_el = card.select_one(".mobile-number")
        result["phone"] = phone_el.get_text(strip=True) if phone_el else None

        website_el = card.select_one("a.company-website")
        result["website"] = website_el.get("href") if website_el else None

        category_el = card.select_one(".cat-name")
        result["directory_category"] = (
            category_el.get_text(strip=True) if category_el else None
        )
    except Exception as exc:  # noqa: BLE001 - defensive: never let a bad page crash the batch
        logger.warning("Failed to parse IndiaMART HTML for '%s': %s", business_name, exc)
        return _empty_result("indiamart", error=str(exc))

    return result


def search_justdial(business_name: str, location: str) -> Dict[str, Any]:
    """Look up a business on JustDial. Rate-limited, never raises."""
    url = JUSTDIAL_SEARCH_URL.format(
        location=quote_plus(location), query=quote_plus(business_name)
    )

    _rate_limit_delay()

    response = _fetch(url)
    if response is None:
        return _empty_result("justdial", error="request failed")

    return parse_justdial_html(response.text, business_name)


def search_indiamart(business_name: str, location: str) -> Dict[str, Any]:
    """Look up a business on IndiaMART. Rate-limited, never raises."""
    query = f"{business_name} {location}"
    url = INDIAMART_SEARCH_URL.format(query=quote_plus(query))

    _rate_limit_delay()

    response = _fetch(url)
    if response is None:
        return _empty_result("indiamart", error="request failed")

    return parse_indiamart_html(response.text, business_name)


ENRICHMENT_SOURCES = {
    "justdial": search_justdial,
    "indiamart": search_indiamart,
}
