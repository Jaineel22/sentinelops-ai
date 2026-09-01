"""Minimal Prometheus text-exposition parser.

Just enough to pull the current value(s) of a named metric family out of a
``/metrics`` scrape. It deliberately does **not** reconstruct rate windows or
snapshots — that is the anomaly-detector's job (ADR-011). This is a
point-in-time read.

Kept dependency-free (no ``prometheus_client`` / ``ml`` import) so the rca-agent
stays lightweight.
"""

from __future__ import annotations

from dataclasses import dataclass

_MAX_LINE_LEN = 4000
_MAX_SAMPLES_PER_METRIC = 50


@dataclass(frozen=True)
class Sample:
    labels: dict[str, str]
    value: float


def _parse_labels(blob: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in _split_top_level_commas(blob):
        if "=" not in part:
            continue
        key, _, raw = part.partition("=")
        labels[key.strip()] = raw.strip().strip('"')
    return labels


def _split_on_close_brace(blob: str) -> tuple[str, str, str]:
    """Split ``label1="a b",label2="c"} 3.0`` into (labels, "}", "3.0"),
    honouring quoted braces. Returns ``("", "", "")`` if no closing brace."""

    in_quotes = False
    for i, ch in enumerate(blob):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "}" and not in_quotes:
            return blob[:i], "}", blob[i + 1 :]
    return "", "", ""


def _split_top_level_commas(blob: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in blob:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def parse_exposition(text: str, wanted: set[str]) -> dict[str, list[Sample]]:
    """Return ``{metric_name: [Sample, ...]}`` for every ``metric_name`` in
    ``wanted`` that appears in ``text``. Names not present are simply absent
    from the result. Malformed lines are skipped, never raised."""

    result: dict[str, list[Sample]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or len(line) > _MAX_LINE_LEN:
            continue

        if "{" in line:
            metric, _, after_brace = line.partition("{")
            label_blob, sep, value_part = _split_on_close_brace(after_brace)
            if not sep:
                continue
            labels = _parse_labels(label_blob)
        else:
            metric, _, value_part = line.partition(" ")
            labels = {}
        value_part = value_part.strip()
        if not value_part:
            continue

        base = metric.split("_bucket")[0].split("_sum")[0].split("_count")[0]
        if metric not in wanted and base not in wanted:
            continue

        try:
            value = float(value_part.split()[0])
        except (ValueError, IndexError):
            continue

        bucket = result.setdefault(metric if metric in wanted else base, [])
        if len(bucket) < _MAX_SAMPLES_PER_METRIC:
            bucket.append(Sample(labels=labels, value=value))
    return result
