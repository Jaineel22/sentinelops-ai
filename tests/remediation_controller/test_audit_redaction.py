"""The audit secret-redaction boundary (Phase 5E)."""

from __future__ import annotations

import pytest

from remediation_controller.audit.redaction import (
    REDACTED,
    redact_identity,
    redact_metadata,
    redact_text,
)

_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "raw",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "aws key ASIAIOSFODNN7EXAMPLE embedded in text",
        "Authorization: Bearer abcdef0123456789ABCDEF",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "x" + "oxb-" + "123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
        "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij",
        _PEM,
        "AIzaSyA1234567890abcdefghijklmnopqrstuvw",
    ],
)
def test_redact_text_scrubs_known_credential_shapes(raw: str) -> None:
    out = redact_text(raw)
    assert REDACTED in out
    for secret in ("AKIA", "ghp_", "xoxb-", "BEGIN RSA PRIVATE KEY", "AIzaSy"):
        if secret in raw:
            assert secret not in out


def test_redact_text_is_idempotent_and_length_capped() -> None:
    assert redact_text(redact_text("AKIAIOSFODNN7EXAMPLE")) == redact_text("AKIAIOSFODNN7EXAMPLE")
    assert len(redact_text("x" * 5000)) <= 1000


def test_redact_text_leaves_ordinary_prose_untouched() -> None:
    prose = "orders-service p95 latency rose for 4 windows; a restart clears the pool."
    assert redact_text(prose) == prose


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "apiKey",
        "authorization",
        "db_password",
        "aws_secret_access_key",
        "session_id",
        "private_key",
        "x-auth-header",
        "bearer_token",
    ],
)
def test_redact_metadata_redacts_sensitive_keys(key: str) -> None:
    assert redact_metadata({key: "whatever-value"}) == {key: REDACTED}


def test_redact_metadata_scrubs_sensitive_values_under_innocuous_keys() -> None:
    out = redact_metadata({"note": "deploy used AKIAIOSFODNN7EXAMPLE"})
    note = out["note"]
    assert isinstance(note, str)
    assert "AKIA" not in note
    assert REDACTED in note


def test_redact_metadata_keeps_scalars_and_drops_structure() -> None:
    out = redact_metadata(
        {
            "replicas": 3,
            "enabled": True,
            "revision": "v42",
            "nested": {"a": 1},
            "listy": [1, 2, 3],
            "nothing": None,
            "ratio": 1.5,
        }
    )
    assert out == {"replicas": 3, "enabled": True, "revision": "v42"}


def test_redact_metadata_caps_key_count_and_value_length() -> None:
    big = {f"k{i}": "v" for i in range(200)}
    big["long"] = "z" * 5000
    out = redact_metadata(big)
    assert len(out) <= 40
    long = out.get("long")
    if isinstance(long, str):
        assert len(long) <= 500


def test_redact_identity_never_empty_and_capped() -> None:
    assert redact_identity("") == REDACTED
    assert len(redact_identity("a" * 500)) <= 128
    assert redact_identity("alice@example.com") == "alice@example.com"
