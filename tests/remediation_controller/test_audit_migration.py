"""Migration 0003 (remediation_audit_events) is wired into the lineage (Phase 5E)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import remediation_controller
from remediation_controller.db.models import Base

_MIGRATIONS = Path(remediation_controller.__file__).parent.parent / "migrations" / "versions"


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0003_revision_chain() -> None:
    mod = _load("0003_remediation_audit_events")
    assert mod.revision == "0003"  # type: ignore[attr-defined]
    assert mod.down_revision == "0002"  # type: ignore[attr-defined]
    assert callable(mod.upgrade) and callable(mod.downgrade)  # type: ignore[attr-defined]


def test_0003_declares_the_postgres_append_only_trigger() -> None:
    src = (_MIGRATIONS / "0003_remediation_audit_events.py").read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE ON remediation_audit_events" in src
    assert "append-only" in src
    assert 'dialect.name == "postgresql"' in src


def test_audit_table_is_registered_in_metadata_with_expected_indexes() -> None:
    table = Base.metadata.tables["remediation_audit_events"]
    index_names = {ix.name for ix in table.indexes}
    assert {
        "ix_audit_remediation_seq",
        "ix_audit_incident_seq",
        "ix_audit_execution",
    } <= index_names
    assert "remediation_id" in table.c
    assert table.c.reason.nullable is False
