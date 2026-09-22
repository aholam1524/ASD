"""Start factory must always checkout dev, regardless of workflow_dispatch branch."""

from __future__ import annotations

from pathlib import Path


def test_start_factory_checkout_uses_dev_ref() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "start-factory.yml"
    ).read_text(encoding="utf-8")

    assert "ref: dev" in workflow, "Start factory checkout must pin ref: dev"
