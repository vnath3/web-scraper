"""Smoke-test the enrichment parsers against saved sample HTML.

Does not hit live JustDial/IndiaMART. Mocks requests.get so we can prove
the parsing logic (and the "not listed" / "malformed page" fallbacks) works
without touching the network. Run with: python scripts/smoke_test_enrichment.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadgen import enrichment  # noqa: E402

JUSTDIAL_SAMPLE_HTML = """
<html><body>
<div class="resultbox">
  <h2 class="store-name">ABC Family Clinic</h2>
  <span class="category">Clinic</span>
  <span class="contact-info"><a class="tel">+91 9999999999</a></span>
  <a class="website-link" href="http://abcclinic.example.com">Visit Website</a>
</div>
</body></html>
"""

INDIAMART_SAMPLE_HTML = """
<html><body>
<div class="listing-card">
  <h2 class="company-name">XYZ Wholesalers</h2>
  <span class="mobile-number">+91 8888888888</span>
  <a class="company-website" href="http://xyzwholesale.example.com">Website</a>
  <span class="cat-name">Wholesaler</span>
</div>
</body></html>
"""

EMPTY_RESULTS_HTML = "<html><body><div class=\"no-results\">No listings found</div></body></html>"

MALFORMED_HTML = "<html><body><div class=\"resultbox\">   "  # unclosed tags, missing fields


def _mock_response(text: str, status_code: int = 200, ok: bool = True) -> Mock:
    resp = Mock()
    resp.text = text
    resp.status_code = status_code
    resp.ok = ok
    return resp


def test_justdial_happy_path() -> None:
    with patch("leadgen.enrichment.requests.get", return_value=_mock_response(JUSTDIAL_SAMPLE_HTML)), \
         patch("leadgen.enrichment.time.sleep"):  # skip the real rate-limit delay
        result = enrichment.search_justdial("ABC Family Clinic", "Garkheda")

    assert result["listed"] is True
    assert result["phone"] == "+91 9999999999"
    assert result["website"] == "http://abcclinic.example.com"
    assert result["directory_category"] == "Clinic"
    assert result["error"] is None
    print("PASS: justdial happy path ->", result)


def test_indiamart_happy_path() -> None:
    with patch("leadgen.enrichment.requests.get", return_value=_mock_response(INDIAMART_SAMPLE_HTML)), \
         patch("leadgen.enrichment.time.sleep"):
        result = enrichment.search_indiamart("XYZ Wholesalers", "Waluj")

    assert result["listed"] is True
    assert result["phone"] == "+91 8888888888"
    assert result["website"] == "http://xyzwholesale.example.com"
    assert result["directory_category"] == "Wholesaler"
    assert result["error"] is None
    print("PASS: indiamart happy path ->", result)


def test_no_listing_found() -> None:
    with patch("leadgen.enrichment.requests.get", return_value=_mock_response(EMPTY_RESULTS_HTML)), \
         patch("leadgen.enrichment.time.sleep"):
        result = enrichment.search_justdial("Nobody Ever Heard Of This Place", "Garkheda")

    assert result["listed"] is False
    assert result["phone"] is None
    assert result["error"] is None
    print("PASS: no listing found ->", result)


def test_malformed_page_does_not_crash() -> None:
    with patch("leadgen.enrichment.requests.get", return_value=_mock_response(MALFORMED_HTML)), \
         patch("leadgen.enrichment.time.sleep"):
        result = enrichment.search_justdial("ABC Family Clinic", "Garkheda")

    # Malformed/unexpected structure should degrade gracefully, not raise.
    assert isinstance(result, dict)
    print("PASS: malformed page handled without crashing ->", result)


def test_http_failure_does_not_crash() -> None:
    with patch(
        "leadgen.enrichment.requests.get",
        return_value=_mock_response("", status_code=503, ok=False),
    ), patch("leadgen.enrichment.time.sleep"):
        result = enrichment.search_justdial("ABC Family Clinic", "Garkheda")

    assert result["listed"] is False
    assert result["error"] == "request failed"
    print("PASS: HTTP failure handled without crashing ->", result)


def main() -> None:
    test_justdial_happy_path()
    test_indiamart_happy_path()
    test_no_listing_found()
    test_malformed_page_does_not_crash()
    test_http_failure_does_not_crash()
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
