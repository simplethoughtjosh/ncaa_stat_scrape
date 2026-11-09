"""Detect and answer the stats.ncaa.org access pages that sit in front of a box score.

Akamai first serves a short proof-of-work page. After that, NCAA asks the visitor to
open the public terms of service and submit the terms form. A real box score is the
only page that contains the Faye ``userId`` script.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

TERMS_OF_SERVICE_URL = "https://www.ncaa.org/terms-of-service/"
TERMS_FORM_URL = "https://stats.ncaa.org/stats_terms"

_POW_RE = re.compile(
    r"var i = (\d+);\s*var j = i \+ Number\(\"(\d+)\" \+ \"(\d+)\"\);",
    re.S,
)
_BM_VERIFY_RE = re.compile(r'"bm-verify": "([^"]+)"')


def is_access_gate(html: str) -> bool:
    """True when this document is an interstitial, the terms form, or a deny page."""
    if "message.ext.userId" in html:
        return False
    head = html[:1200]
    return (
        "triggerInterstitialChallenge" in html
        or 'name="terms_accepted"' in html
        or "Access Denied" in head
    )


def akamai_interstitial_payload(html: str) -> dict[str, str | int] | None:
    """Return the JSON body Akamai's interstitial script POSTs, or None."""
    if "triggerInterstitialChallenge" not in html:
        return None
    pow_match = _POW_RE.search(html)
    token_match = _BM_VERIFY_RE.search(html)
    if not pow_match or not token_match:
        return None
    seed, left, right = pow_match.groups()
    return {"bm-verify": token_match.group(1), "pow": int(seed) + int(left + right)}


def terms_form_payload(html: str) -> dict[str, str] | None:
    """Hidden fields plus the terms checkbox, as the Continue button submits them."""
    if 'name="terms_accepted"' not in html:
        return None
    form = BeautifulSoup(html, "lxml").find("form", attrs={"action": "/stats_terms"})
    if form is None:
        return None
    payload: dict[str, str] = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if not name:
            continue
        payload[name] = field.get("value") or ""
    if "terms_accepted" not in payload or "nonce" not in payload:
        return None
    return payload
