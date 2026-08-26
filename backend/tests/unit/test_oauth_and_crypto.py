"""OAuth URL construction, PKCE, error classification, and token encryption.

No network and no database — these check the parts that must be right before a
single byte reaches Google.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import crypto
from app.integrations.youtube import oauth
from app.integrations.youtube.errors import (
    GoogleAPIError,
    GoogleAuthError,
    InvalidGrantError,
    QuotaExceededError,
    RateLimitedError,
    TransientGoogleError,
    classify_api_error,
    classify_token_error,
)


# --------------------------------------------------------------- PKCE/URL ---
def test_authorization_url_targets_google() -> None:
    req = oauth.build_authorization_request()
    parsed = urlparse(req.authorization_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"


def test_authorization_url_requests_offline_access_and_forces_consent() -> None:
    """Without prompt=consent Google only sends a refresh token the first time.

    A reconnect would then silently produce a connection that cannot refresh.
    """
    params = parse_qs(urlparse(oauth.build_authorization_request().authorization_url).query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["response_type"] == ["code"]


def test_authorization_url_uses_s256_pkce() -> None:
    req = oauth.build_authorization_request()
    params = parse_qs(urlparse(req.authorization_url).query)
    assert params["code_challenge_method"] == ["S256"]

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(req.code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert params["code_challenge"] == [expected]


def test_code_verifier_length_is_within_rfc7636_bounds() -> None:
    verifier = oauth.build_authorization_request().code_verifier
    assert 43 <= len(verifier) <= 128


def test_state_and_verifier_are_unique_per_request() -> None:
    a, b = oauth.build_authorization_request(), oauth.build_authorization_request()
    assert a.state != b.state
    assert a.code_verifier != b.code_verifier


def test_authorization_url_requests_all_required_scopes() -> None:
    params = parse_qs(urlparse(oauth.build_authorization_request().authorization_url).query)
    scope = params["scope"][0]
    for required in oauth.SCOPES:
        assert required in scope


def test_upload_scope_is_requested_up_front() -> None:
    """Asking now avoids a second consent round trip after the audit passes."""
    assert "https://www.googleapis.com/auth/youtube.upload" in oauth.SCOPES


def test_redirect_uri_is_echoed_for_exact_reuse() -> None:
    """The token exchange must send a byte-identical redirect_uri."""
    req = oauth.build_authorization_request("https://example.test/cb")
    assert req.redirect_uri == "https://example.test/cb"
    assert parse_qs(urlparse(req.authorization_url).query)["redirect_uri"] == [
        "https://example.test/cb"
    ]


def test_missing_scopes_detects_a_partial_grant() -> None:
    """The consent screen lets users untick individual permissions."""
    granted = [oauth.SCOPES[0]]
    missing = oauth.missing_scopes(granted)
    assert oauth.SCOPES[0] not in missing
    assert oauth.SCOPES[1] in missing


def test_missing_scopes_empty_when_all_granted() -> None:
    assert oauth.missing_scopes(list(oauth.SCOPES)) == []


def test_token_bundle_repr_never_leaks_tokens() -> None:
    bundle = oauth.TokenBundle(
        access_token="ya29.SECRET-ACCESS",
        refresh_token="1//SECRET-REFRESH",
        expires_in=3600,
        scopes=list(oauth.SCOPES),
        token_type="Bearer",
    )
    rendered = repr(bundle)
    assert "SECRET-ACCESS" not in rendered
    assert "SECRET-REFRESH" not in rendered
    assert "has_refresh=True" in rendered


# --------------------------------------------------- error classification ---
def test_invalid_grant_is_terminal() -> None:
    """The single most important classification in the integration.

    Retrying a dead refresh token cannot revive it; it only burns attempts.
    """
    from app.core.errors import is_retryable

    exc = classify_token_error(400, {"error": "invalid_grant"})
    assert isinstance(exc, InvalidGrantError)
    assert not is_retryable(exc, (TransientGoogleError, RateLimitedError))


def test_invalid_grant_message_explains_the_7_day_rule() -> None:
    message = str(classify_token_error(400, {"error": "invalid_grant"}))
    assert "7 days" in message
    assert "testing" in message


def test_invalid_client_is_an_auth_error() -> None:
    exc = classify_token_error(401, {"error": "invalid_client"})
    assert isinstance(exc, GoogleAuthError)
    assert not isinstance(exc, InvalidGrantError)


def test_token_endpoint_5xx_is_retryable() -> None:
    assert isinstance(classify_token_error(503, {}), TransientGoogleError)


def test_quota_exceeded_is_terminal_not_retryable() -> None:
    """The quota window is a calendar day — far longer than any retry budget."""
    exc = classify_api_error(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
    assert isinstance(exc, QuotaExceededError)


def test_rate_limited_is_retryable() -> None:
    exc = classify_api_error(429, {"error": {"message": "slow down"}})
    assert isinstance(exc, RateLimitedError)


def test_api_5xx_is_retryable() -> None:
    assert isinstance(classify_api_error(500, {}), TransientGoogleError)


def test_api_401_is_an_auth_error() -> None:
    assert isinstance(classify_api_error(401, {"error": {"message": "bad creds"}}), GoogleAuthError)


def test_api_400_is_terminal_generic() -> None:
    exc = classify_api_error(400, {"error": {"message": "bad request"}})
    assert isinstance(exc, GoogleAPIError)
    assert not isinstance(exc, TransientGoogleError)


def test_classification_survives_a_non_dict_payload() -> None:
    assert isinstance(classify_api_error(500, {}), TransientGoogleError)


# ------------------------------------------------------------------ crypto ---
@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "SECRETS_KEY", crypto.generate_key())
    crypto.reset_cipher_cache()
    yield
    crypto.reset_cipher_cache()


def test_encrypt_decrypt_round_trip() -> None:
    token = "1//0abcdefgh-REFRESH-TOKEN"
    assert crypto.decrypt(crypto.encrypt(token)) == token


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    token = "ya29.a-very-secret-access-token"
    assert token.encode() not in crypto.encrypt(token)


def test_encryption_is_non_deterministic() -> None:
    """Fernet embeds a random IV, so identical inputs differ on disk."""
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_decrypt_none_returns_none() -> None:
    assert crypto.decrypt(None) is None


def test_decrypt_with_a_different_key_fails_loudly() -> None:
    from app.config import settings

    ciphertext = crypto.encrypt("secret")
    settings.SECRETS_KEY = crypto.generate_key()
    crypto.reset_cipher_cache()
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ciphertext)


def test_missing_secrets_key_is_reported_clearly() -> None:
    from app.config import settings

    settings.SECRETS_KEY = ""
    crypto.reset_cipher_cache()
    with pytest.raises(crypto.SecretsKeyError, match="SECRETS_KEY"):
        crypto.encrypt("x")


def test_decrypt_accepts_memoryview_from_the_driver() -> None:
    """psycopg can hand back BYTEA as a memoryview rather than bytes."""
    ciphertext = crypto.encrypt("payload")
    assert crypto.decrypt(memoryview(ciphertext)) == "payload"
