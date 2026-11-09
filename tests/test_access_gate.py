"""NCAA access pages must be recognized and answered before a box score is parsed."""

from pathlib import Path

from college_sports_scraper.ncaa.access_gate import (
    akamai_interstitial_payload,
    is_access_gate,
    terms_form_payload,
)
from college_sports_scraper.ncaa.live_state import extract_faye_auth

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wbb_live_box_score_6656891.html"

CHALLENGE = """
<script> var i = 10; var j = i + Number("12" + "34"); </script>
<script>
function triggerInterstitialChallenge() {
  xhr.send(JSON.stringify({"bm-verify": "abc", "pow": j}));
}
</script>
"""

TERMS = """
<form action="/stats_terms" method="post">
  <input name="utf8" type="hidden" value="✓" />
  <input type="hidden" name="authenticity_token" value="token+value" />
  <input type="hidden" name="nonce" value="abc123" />
  <input type="checkbox" name="terms_accepted" value="def456" />
</form>
"""


def test_interstitial_pow_matches_the_page_script():
    payload = akamai_interstitial_payload(CHALLENGE)
    assert payload == {"bm-verify": "abc", "pow": 10 + 1234}


def test_terms_form_includes_the_checkbox_value():
    payload = terms_form_payload(TERMS)
    assert payload is not None
    assert payload["terms_accepted"] == "def456"
    assert payload["nonce"] == "abc123"
    assert payload["authenticity_token"] == "token+value"
    assert payload["utf8"] == "✓"


def test_challenge_and_terms_are_gates_and_not_a_box_score():
    assert is_access_gate(CHALLENGE)
    assert is_access_gate(TERMS)
    assert is_access_gate("<html><title>Access Denied</title></html>")
    box = FIXTURE.read_text()
    assert not is_access_gate(box)
    assert extract_faye_auth(box).user_id


def test_challenge_page_has_no_faye_user():
    try:
        extract_faye_auth(CHALLENGE)
    except ValueError as exc:
        assert "userId" in str(exc)
    else:
        raise AssertionError("challenge HTML must not be treated as a box score")
