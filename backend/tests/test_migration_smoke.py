"""F01 smoke: the Alembic history is a single linear chain rooted at the baseline.

No database required — this inspects the migration scripts only. Applying migrations
against a real Postgres+pgvector is exercised by the Testcontainers suite in F04.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script() -> ScriptDirectory:
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_single_head() -> None:
    assert len(_script().get_heads()) == 1


def test_baseline_is_root() -> None:
    script = _script()
    assert script.get_base() == "0001"
    assert script.get_revision("0001").down_revision is None
