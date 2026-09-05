"""Phase 9 verification — the AI RCA agent runs end to end.

Phase 9 is the blueprint's number for the RCA agent that the implementation built
as Phase 4. Nothing new is added here: this script *drives the real pieces* and
reports real numbers.

* **in-process (default)** — runs the mock RCA scenario and the full-chain
  ``incident.opened`` -> consumer -> RCA -> API scenario against in-memory fakes
  (no LLM key, no network, no DB, no Kafka), then checks the structured
  ``RCAReport`` each produced.
* **live API check** — ``--url http://localhost:8004`` also confirms a running
  ``rca-agent`` Investigation API is reachable and its routes are wired.

    python scripts/phase9_verify.py
    python scripts/phase9_verify.py --url http://localhost:8004
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Force mock mode regardless of the caller's environment — Phase 9 verification
# must never need an API key. (Set before importing rca_agent config.)
os.environ["RCA_MODE"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"

# The check marks are UTF-8; Windows consoles default to cp1252.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8")

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

for _name in ("httpx", "httpx2", "httpcore", "opentelemetry", "rca_agent", "aiokafka"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class _Checker:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, msg: str) -> None:
        print(f"   ✅ {msg}")

    def bad(self, msg: str) -> None:
        print(f"   ❌ {msg}")
        self.failures += 1

    def check(self, cond: bool, ok_msg: str, bad_msg: str) -> None:
        self.ok(ok_msg) if cond else self.bad(bad_msg)


def _cited_ok(report: Any) -> bool:
    known = {e.id for e in report.evidence}
    cited = {i for f in report.findings for i in f.evidence_ids}
    return cited <= known


# --- 1. mock RCA scenario -------------------------------------------
async def _mock_scenario(chk: _Checker) -> None:
    import httpx

    import rca_scenario as scn  # scripts/rca_scenario.py
    from rca_agent.config import LlmSettings, RcaSettings, Settings
    from rca_agent.domain import InvestigationStatus, InvestigationTrigger
    from rca_agent.engine import InvestigationService
    from rca_agent.llm import build_llm_client
    from rca_agent.repository import InMemoryInvestigationRepository
    from rca_agent.tools import build_registry

    print("1. Mock RCA Scenario")

    settings = Settings(rca=RcaSettings(mode="mock"), llm=LlmSettings(provider="mock"))
    registry = build_registry(
        settings,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(scn._handler)),
    )
    service = InvestigationService(
        repository=InMemoryInvestigationRepository(),
        registry=registry,
        llm_client=build_llm_client(settings),
        settings=settings,
    )
    outcome = await service.investigate(scn._INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    inv, report = outcome.investigation, outcome.report

    chk.check(
        inv.status in {InvestigationStatus.COMPLETED, InvestigationStatus.INSUFFICIENT_EVIDENCE},
        f"rca_scenario completed (status={inv.status})",
        f"rca_scenario ended in {inv.status}",
    )
    if report is None:
        chk.bad("no RCA report was produced")
        return

    rc = report.root_cause
    chk.ok(f"root cause: {rc.statement if rc else 'UNDETERMINED (insufficient evidence)'}")
    chk.ok(f"confidence: {report.overall_confidence}")
    chk.ok(
        f"evidence: {len(report.evidence)} collected, "
        f"{len(rc.evidence_ids) if rc else 0} cited by root cause"
    )
    chk.ok(
        f"tool calls: {inv.tool_call_count} · steps: {inv.step_count} · "
        f"hypotheses: {[(h.id, str(h.verdict)) for h in report.hypotheses]}"
    )
    chk.check(
        _cited_ok(report),
        "every cited evidence id was collected this investigation",
        "report cites an evidence id that was never collected",
    )
    chk.check(
        report.recommended_action.requires_human_approval is True,
        f"recommendation {report.recommended_action.action_type} requires human approval",
        "recommendation is not human-approval gated",
    )
    unavail = [s.split(":")[0] for s in report.unavailable_evidence_sources]
    chk.ok(f"unavailable sources surfaced honestly: {unavail}")


# --- 2. full-chain RCA scenario -------------------------------------
async def _e2e_scenario(chk: _Checker) -> None:
    import httpx
    from fastapi.testclient import TestClient

    import rca_e2e_scenario as e2e  # scripts/rca_e2e_scenario.py
    from rca_agent.app import create_app
    from rca_agent.config import LlmSettings, RcaSettings, Settings

    print("\n2. End-to-End RCA Scenario (incident.opened -> consumer -> RCA -> API)")

    e2e._quiet()
    # _run() runs the consumer twice (asserts idempotency internally) and prints
    # its own progress — swallow that so this section's output stays clean.
    with contextlib.redirect_stdout(io.StringIO()):
        repo = await e2e._run()
    inv = await repo.get_latest_investigation(e2e._INCIDENT_ID)
    assert inv is not None
    chk.ok(f"incident.opened -> idempotent consumer -> {inv.id} (status={inv.status})")

    settings = Settings(rca=RcaSettings(mode="mock"), llm=LlmSettings(provider="mock"))
    app = create_app(
        settings,
        repository=repo,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(e2e._incident_api)),
        run_consumer=False,
    )
    with TestClient(app) as client:
        by_incident = client.get(f"/incidents/{e2e._INCIDENT_ID}/investigation")
        chk.check(
            by_incident.status_code == 200,
            "GET /incidents/{id}/investigation works",
            f"GET /incidents/{{id}}/investigation -> {by_incident.status_code}",
        )
        detail = by_incident.json()
        rpt = detail.get("report")
        rpt_status = rpt["status"] if rpt else None
        chk.check(
            rpt is not None,
            f"GET /investigations/{{id}} returns an RCA report (status={rpt_status})",
            "investigation detail carries no RCA report",
        )
        by_id = client.get(f"/investigations/{detail['investigation']['id']}")
        chk.check(
            by_id.status_code == 200,
            "GET /investigations/{id} works",
            f"GET /investigations/{{id}} -> {by_id.status_code}",
        )
        if rpt is not None:
            chk.check(
                rpt["recommended_action"]["requires_human_approval"] is True,
                "RCA recommendation requires human approval (Phase 5 owns execution)",
                "RCA recommendation is not human-approval gated",
            )
            known = {e["id"] for e in rpt["evidence"]}
            cited = {i for f in rpt["findings"] for i in f["evidence_ids"]}
            chk.check(
                cited <= known,
                f"RCA is evidence-grounded ({len(known)} evidence item(s))",
                "RCA cites evidence it never collected",
            )


# --- 3. live Investigation API (optional) --------------------------
def _live_api(chk: _Checker, url: str) -> None:
    import httpx

    url = url.rstrip("/")
    print(f"\n3. Investigation API ({url})")
    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            health = client.get("/health")
            chk.check(
                health.status_code == 200,
                f"GET /health -> {health.status_code}",
                f"GET /health -> {health.status_code}",
            )
            missing_inv = client.get("/investigations/rca_deadbeefdeadbeef")
            chk.check(
                missing_inv.status_code == 404,
                "GET /investigations/{id} route wired (404 for unknown id)",
                f"GET /investigations/{{id}} -> {missing_inv.status_code} (expected 404)",
            )
            missing_for_incident = client.get("/incidents/inc_deadbeefdeadbeef/investigation")
            chk.check(
                missing_for_incident.status_code == 404,
                "GET /incidents/{id}/investigation route wired (404 for unknown id)",
                f"GET /incidents/{{id}}/investigation -> {missing_for_incident.status_code}",
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        chk.bad(f"rca-agent not reachable at {url} ({type(exc).__name__})")


def _summary(chk: _Checker) -> int:
    print()
    if chk.failures == 0:
        print("Phase 9 complete! ✅")
        return 0
    print(f"Phase 9 verification: {chk.failures} check(s) FAILED ❌")
    return 1


async def _run(url: str | None) -> int:
    print("=== Phase 9 Verification ===\n")
    chk = _Checker()
    await _mock_scenario(chk)
    await _e2e_scenario(chk)
    if url:
        _live_api(chk, url)
    else:
        print("\n3. Investigation API — skipped (pass --url http://localhost:8004 to check)")
    return _summary(chk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="live rca-agent base URL (e.g. http://localhost:8004)")
    args = parser.parse_args()
    return asyncio.run(_run(args.url))


if __name__ == "__main__":
    sys.exit(main())
