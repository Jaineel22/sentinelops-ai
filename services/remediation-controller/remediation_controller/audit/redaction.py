"""The audit trail's secret / sensitive-data redaction boundary (Phase 5E).

The audit trail records **what happened**, not **secrets**. Every free-text and
structured value that flows into a :class:`~remediation_controller.audit.model.
RemediationAuditEvent` goes through this module first.

Two independent filters, both deterministic:

* **key-based** — a metadata key whose name looks like a credential
  (``api_key``, ``authorization``, ``password``, …) has its value replaced with
  :data:`REDACTED`, regardless of the value;
* **value-based** — a substring that matches a known credential shape (a PEM
  private key block, an AWS access-key id, a GitHub / Slack token, a JWT, a
  ``Bearer`` header, …) is replaced with :data:`REDACTED` wherever it appears,
  in any string.

Values are also length-capped. The remediation domain has *no* field that can
hold a command or an arbitrary secret (closed catalogue, bounded parameters), so
this is defence in depth — but it is a hard, tested boundary, not a convention.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

REDACTED = "[REDACTED]"

_MAX_TEXT = 1000
_MAX_METADATA_VALUE = 500
_MAX_METADATA_KEYS = 40

_SENSITIVE_KEY = re.compile(
    r"(secret|token|password|passwd|pwd|api[_-]?key|access[_-]?key|secret[_-]?key|"
    r"credential|authorization|auth[_-]?header|private[_-]?key|passphrase|"
    r"bearer|session[_-]?id|cookie|ssh[_-]?key)",
    re.IGNORECASE,
)

_SENSITIVE_VALUE = re.compile(
    r"""(
        -----BEGIN\s[A-Z0-9 ]*PRIVATE\sKEY-----   # PEM private key block
        | \b(?:AKIA|ASIA|AROA|AIDA)[0-9A-Z]{16}\b # AWS access key id
        | \bgh[posru]_[A-Za-z0-9]{20,}\b          # GitHub token
        | \bxox[baprs]-[A-Za-z0-9-]{10,}\b        # Slack token
        | \beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b  # JWT
        | \bBearer\s+[A-Za-z0-9._~+/-]{8,}={0,2}  # Authorization: Bearer ...
        | \bAIza[0-9A-Za-z_-]{20,}\b              # Google API key
    )""",
    re.VERBOSE,
)


def redact_text(value: str) -> str:
    """Redact any credential-shaped substring, then length-cap.

    Deterministic and idempotent: ``redact_text(redact_text(x)) == redact_text(x)``.
    """

    cleaned = _SENSITIVE_VALUE.sub(REDACTED, value)
    if len(cleaned) > _MAX_TEXT:
        cleaned = cleaned[: _MAX_TEXT - 1] + "…"
    return cleaned


def redact_metadata(data: Mapping[str, object]) -> dict[str, str | int | bool]:
    """Return a new, bounded, scalar-only, redacted copy of ``data``.

    * a sensitive-looking key -> value becomes :data:`REDACTED`;
    * ``str`` values are value-redacted and capped at 500 chars;
    * ``bool`` / ``int`` values pass through unchanged;
    * anything else (nested dict/list, ``None``, float, bytes) is dropped;
    * at most 40 keys are kept (sorted) so a hostile caller cannot bloat a row.
    """

    out: dict[str, str | int | bool] = {}
    for key in sorted(data)[:_MAX_METADATA_KEYS]:
        raw = data[key]
        if _SENSITIVE_KEY.search(key):
            out[key] = REDACTED
            continue
        if isinstance(raw, bool | int):  # bool is a subclass of int; both pass through
            out[key] = raw
        elif isinstance(raw, str):
            cleaned = _SENSITIVE_VALUE.sub(REDACTED, raw)
            out[key] = cleaned[:_MAX_METADATA_VALUE]
        # every other type is intentionally omitted
    return out


def redact_identity(value: str) -> str:
    """Sanitise a human-supplied approver identity for storage as ``actor_id``.

    Kept for traceability (an auditor needs to know *who*), but value-redacted
    and hard-capped at 128 chars so an adversarial identity string cannot carry
    a secret or an oversized payload into the trail.
    """

    return redact_text(value)[:128] or REDACTED


__all__ = ["REDACTED", "redact_identity", "redact_metadata", "redact_text"]
