"""Tests for factory/run_ci.sh behavior in app vs factory workspaces."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

RUN_CI = Path(__file__).resolve().parent.parent / "run_ci.sh"


def _fake_python_bin(log_path: Path) -> Path:
    bin_dir = log_path.parent / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "python"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_path!s}
            exit 0
            """
        )
    )
    wrapper.chmod(0o755)
    return bin_dir


def _run_ci(cwd: Path, log_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{_fake_python_bin(log_path)!s}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(RUN_CI)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestRunCiSh:
    def test_no_test_files_exits_zero_with_noop_message(self, tmp_path: Path) -> None:
        log_path = tmp_path / "python.log"
        result = _run_ci(tmp_path, log_path)

        assert result.returncode == 0
        assert "No pytest files found; CI no-op pass" in result.stdout
        assert not log_path.exists() or log_path.read_text() == ""

    def test_app_repo_installs_root_requirements_before_pytest(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("pytest\n")
        (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n")
        log_path = tmp_path / "python.log"

        result = _run_ci(tmp_path, log_path)

        assert result.returncode == 0
        log = log_path.read_text()
        assert "-m pip install pytest -q" in log
        assert "-m pip install -r requirements.txt -q" in log
        assert "-m pytest --ignore=.asd-factory" in log

    def test_factory_repo_skips_root_requirements_install(
        self, tmp_path: Path
    ) -> None:
        factory_dir = tmp_path / "factory"
        factory_dir.mkdir()
        (factory_dir / "dispatch.py").write_text("# factory\n")
        (tmp_path / "requirements.txt").write_text("cursor-sdk\n")
        (tmp_path / "test_factory.py").write_text("def test_ok():\n    assert True\n")
        log_path = tmp_path / "python.log"

        result = _run_ci(tmp_path, log_path)

        assert result.returncode == 0
        log = log_path.read_text()
        assert "-m pip install pytest -q" in log
        assert "requirements.txt" not in log
        assert "-m pytest --ignore=.asd-factory" in log
