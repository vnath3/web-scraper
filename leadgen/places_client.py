"""Thin REST client for the Google Places API (New)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from leadgen import storage

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location"
)
PLACE_DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,nationalPhoneNumber,websiteUri,"
    "businessStatus,rating,userRatingCount"
)

MAX_TEXT_SEARCH_PAGES = 3
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class PlacesAPIError(RuntimeError):
    """Raised when the Places API returns a non-retryable error."""


@dataclass
class ApiCallCounter:
    """Simple mutable counter for tracking API calls made during a run."""

    count: int = 0

    def increment(self) -> None:
        self.count += 1


def _get_api_key() -> str:
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise PlacesAPIError(
            "GOOGLE_PLACES_API_KEY environment variable is not set. "
            "Copy .env.example to .env and fill in your key."
        )
    return api_key


def _request_with_retry(
    method: str,
    url: str,
    headers: Dict[str, str],
    json_body: Optional[Dict[str, Any]] = None,
    counter: Optional[ApiCallCounter] = None,
    endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    last_exception: Optional[Exception] = None

    for attempt in range(RETRY_ATTEMPTS):
        if counter is not None:
            counter.increment()
        try:
            response = requests.request(
                method, url, headers=headers, json=json_body, timeout=30
            )
        except requests.RequestException as exc:
            last_exception = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))
            continue

        if endpoint is not None:
            storage.log_api_call(endpoint)

        if response.status_code in RETRYABLE_STATUS_CODES:
            last_exception = PlacesAPIError(
                f"Places API returned {response.status_code}: {response.text}"
            )
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))
            continue

        if not response.ok:
            raise PlacesAPIError(
                f"Places API returned {response.status_code}: {response.text}"
            )

        return response.json()

    raise PlacesAPIError(
        f"Places API request failed after {RETRY_ATTEMPTS} attempts: {last_exception}"
    )


def text_search(
    query: str,
    location_bias: Optional[Dict[str, Any]] = None,
    counter: Optional[ApiCallCounter] = None,
) -> List[Dict[str, Any]]:
    """Run a Text Search (New) query, paginating up to MAX_TEXT_SEARCH_PAGES pages.

    Returns a list of dicts with keys: place_id, name, address, location.
    """
    api_key = _get_api_key()
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": TEXT_SEARCH_FIELD_MASK,
    }

    body: Dict[str, Any] = {"textQuery": query}
    if location_bias is not None:
        body["locationBias"] = location_bias

    results: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    for _ in range(MAX_TEXT_SEARCH_PAGES):
        page_body = dict(body)
        if page_token:
            page_body["pageToken"] = page_token

        data = _request_with_retry(
            "POST", TEXT_SEARCH_URL, headers, page_body, counter, endpoint="text_search"
        )

        for place in data.get("places", []):
            results.append(
                {
                    "place_id": place.get("id"),
                    "name": place.get("displayName", {}).get("text"),
                    "address": place.get("formattedAddress"),
                    "location": place.get("location"),
                }
            )

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        # nextPageToken needs a short delay before it becomes valid.
        time.sleep(2)

    return results


def get_place_details(
    place_id: str, counter: Optional[ApiCallCounter] = None
) -> Dict[str, Any]:
    """Fetch Place Details (New) for a single place_id."""
    api_key = _get_api_key()
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": PLACE_DETAILS_FIELD_MASK,
    }
    url = PLACE_DETAILS_URL.format(place_id=place_id)

    data = _request_with_retry(
        "GET", url, headers, counter=counter, endpoint="get_place_details"
    )

    return {
        "place_id": data.get("id"),
        "name": data.get("displayName", {}).get("text"),
        "address": data.get("formattedAddress"),
        "phone": data.get("nationalPhoneNumber"),
        "website": data.get("websiteUri"),
        "business_status": data.get("businessStatus"),
        "rating": data.get("rating"),
        "rating_count": data.get("userRatingCount"),
    }
