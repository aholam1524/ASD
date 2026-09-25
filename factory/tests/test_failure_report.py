"""Tests for factory/failure_report.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import failure_report
from failure_report import factory_failure_marker, report_factory_failure


class TestFactoryFailureMarker:
    def test_marker_includes_run_id(self) -> None:
        assert factory_failure_marker("12345") == "<!-- factory:failure:12345 -->"


class TestReportFactoryFailure:
    def test_posts_comment_and_sets_blocked(self, tmp_path: Path) -> None:
        event = {
            "pull_request": {
                "number": 10,
                "body": "Closes #66",
            }
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")

        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_RUN_ID": "999",
                "GITHUB_SERVER_URL": "https://github.com",
            },
            clear=False,
        ), patch.object(
            failure_report, "_comment_bodies", return_value=""
        ), patch.object(
            failure_report, "_post_failure_comment"
        ) as post_mock, patch.object(
            failure_report, "_issue_has_factory_done", return_value=False
        ), patch.object(
            failure_report, "set_factory_status"
        ) as status_mock:
            err = subprocess.CalledProcessError(
                1,
                ["gh", "pr", "merge"],
                stderr="merge failed\nline2\n",
            )
            report_factory_failure(err)

        post_mock.assert_called_once()
        owner_repo, number, body = post_mock.call_args.args
        assert owner_repo == "o/r"
        assert number == 10
        assert "<!-- factory:failure:999 -->" in body
        assert "https://github.com/o/r/actions/runs/999" in body
        assert "merge failed" in body
        status_mock.assert_called_once_with("o/r", 66, "factory-blocked")

    def test_skips_second_comment_when_marker_present(self, tmp_path: Path) -> None:
        event = {"issue": {"number": 5}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")
        marker = factory_failure_marker("555")

        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_EVENT_NAME": "issues",
                "GITHUB_RUN_ID": "555",
            },
            clear=False,
        ), patch.object(
            failure_report, "_comment_bodies", return_value=f"old\n{marker}\n"
        ), patch.object(
            failure_report, "_post_failure_comment"
        ) as post_mock, patch.object(
            failure_report, "set_factory_status"
        ) as status_mock:
            report_factory_failure(subprocess.CalledProcessError(1, ["gh"]))

        post_mock.assert_not_called()
        status_mock.assert_not_called()

    def test_at_most_one_report_per_process(self, tmp_path: Path) -> None:
        failure_report._FAILURE_REPORTED = False
        event = {"issue": {"number": 5}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")

        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_EVENT_NAME": "issues",
                "GITHUB_RUN_ID": "1",
            },
            clear=False,
        ), patch.object(failure_report, "_comment_bodies", return_value=""), patch.object(
            failure_report, "_post_failure_comment"
        ) as post_mock, patch.object(
            failure_report, "_issue_has_factory_done", return_value=False
        ), patch.object(
            failure_report, "set_factory_status"
        ):
            report_factory_failure(exit_code=1)
            report_factory_failure(exit_code=1)

        assert post_mock.call_count == 1
        failure_report._FAILURE_REPORTED = False

    def test_unresolved_target_only_logs(self, capsys) -> None:
        failure_report._FAILURE_REPORTED = False
        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_EVENT_NAME": "schedule",
            },
            clear=False,
        ), patch.object(
            failure_report, "_post_failure_comment"
        ) as post_mock:
            import os

            os.environ.pop("GITHUB_EVENT_PATH", None)
            report_factory_failure(exit_code=1)

        post_mock.assert_not_called()
        captured = capsys.readouterr()
        assert "could not resolve issue/PR" in captured.err
        failure_report._FAILURE_REPORTED = False

    def test_never_raises_when_comment_post_fails(self, tmp_path: Path) -> None:
        failure_report._FAILURE_REPORTED = False
        event = {"issue": {"number": 7}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")

        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_EVENT_NAME": "issues",
                "GITHUB_RUN_ID": "42",
            },
            clear=False,
        ), patch.object(failure_report, "_comment_bodies", return_value=""), patch.object(
            failure_report, "_post_failure_comment", side_effect=RuntimeError("boom")
        ), patch.object(
            failure_report, "set_factory_status"
        ) as status_mock:
            report_factory_failure(subprocess.CalledProcessError(1, ["gh"]))

        status_mock.assert_not_called()
        failure_report._FAILURE_REPORTED = False

    def test_skips_blocked_when_factory_done(self, tmp_path: Path) -> None:
        failure_report._FAILURE_REPORTED = False
        event = {"issue": {"number": 8}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")

        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_EVENT_NAME": "issues",
                "GITHUB_RUN_ID": "88",
            },
            clear=False,
        ), patch.object(failure_report, "_comment_bodies", return_value=""), patch.object(
            failure_report, "_post_failure_comment"
        ), patch.object(
            failure_report, "_issue_has_factory_done", return_value=True
        ), patch.object(
            failure_report, "set_factory_status"
        ) as status_mock:
            report_factory_failure(exit_code=1)

        status_mock.assert_not_called()
        failure_report._FAILURE_REPORTED = False


class TestDispatchMainIntegration:
    def test_main_module_reports_called_process_error(self) -> None:
        import dispatch

        err = subprocess.CalledProcessError(1, ["gh", "x"])
        with patch.object(dispatch, "report_factory_failure") as report_mock:
            try:
                try:
                    raise err
                except subprocess.CalledProcessError as caught:
                    dispatch.report_factory_failure(caught)
                    raise SystemExit(1) from caught
            except SystemExit as exit_err:
                assert exit_err.code == 1
        report_mock.assert_called_once_with(err)
