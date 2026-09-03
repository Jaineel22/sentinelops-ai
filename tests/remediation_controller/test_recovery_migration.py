"""Migration 0004 (remediation_verifications + recovery audit events) — Phase 5F."""

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


def test_0004_revision_chain() -> None:
    mod = _load("0004_remediation_verifications")
    assert mod.revision == "0004"  # type: ignore[attr-defined]
    assert mod.down_revision == "0003"  # type: ignore[attr-defined]
    assert callable(mod.upgrade) and callable(mod.downgrade)  # type: ignore[attr-defined]


def test_single_alembic_head() -> None:
    revisions: dict[str, str | None] = {}
    downs: set[str | None] = set()
    for path in _MIGRATIONS.glob("[0-9]*.py"):
        mod = _load(path.stem)
        revisions[mod.revision] = mod.down_revision  # type: ignore[attr-defined]
        downs.add(mod.down_revision)  # type: ignore[attr-defined]
    heads = [r for r in revisions if r not in downs]
    assert heads == ["0004"], heads


def test_0004_widens_the_audit_event_type_check() -> None:
    src = (_MIGRATIONS / "0004_remediation_verifications.py").read_text(encoding="utf-8")
    assert "VERIFICATION_STARTED" in src
    assert "VERIFICATION_SUCCEEDED" in src
    assert "VERIFICATION_FAILED" in src
    assert "ck_audit_event_type" in src
    assert 'dialect.name == "postgresql"' in src


def test_verification_table_registered_with_expected_shape() -> None:
    from sqlalchemy import UniqueConstraint

    table = Base.metadata.tables["remediation_verifications"]
    cols = set(table.c.keys())
    assert {"remediation_id", "execution_id", "status", "verifier_type", "checks"} <= cols
    uniques = {
        tuple(sorted(c.name for c in con.columns))
        for con in table.constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("remediation_id",) in uniques
    index_names = {ix.name for ix in table.indexes}
    assert {"ix_verifications_remediation", "ix_verifications_execution"} <= index_names


def test_audit_table_has_verification_id_column() -> None:
    assert "verification_id" in Base.metadata.tables["remediation_audit_events"].c
