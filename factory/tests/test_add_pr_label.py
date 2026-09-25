"""Tests for add_pr_label REST API behavior and Claude-only fatal failures."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

import dispatch
from dispatch import add_pr_label, ensure_agent_role_label, handle_feature_push, handle_label


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class TestAddPrLabel:
    def test_uses_factory_token_env(self) -> None:
        with patch.object(dispatch.subprocess, "run") as run_mock, patch.dict(
            "os.environ",
            {"FACTORY_GITHUB_TOKEN": "pat-secret"},
            clear=False,
        ):
            run_mock.return_value = _completed(0, "agent-review\n")
            assert add_pr_label("o/r", 10, "agent-review") is True

        env = run_mock.call_args.kwargs["env"]
        assert env["GH_TOKEN"] == "pat-secret"
        assert env["GITHUB_TOKEN"] == "pat-secret"

    def test_already_present_skips_post(self) -> None:
        with patch.object(dispatch, "_gh_run") as run_mock:
            run_mock.side_effect = [
                _completed(0, "agent-review\nother\n"),
            ]
            assert add_pr_label("o/r", 10, "agent-review") is True

        assert len(run_mock.call_args_list) == 1
        assert run_mock.call_args_list[0].args[0][:2] == [
            "api",
            "repos/o/r/issues/10",
        ]

    def test_creates_label_then_posts(self) -> None:
        with patch.object(dispatch, "_gh_run") as run_mock:
            run_mock.side_effect = [
                _completed(0, ""),
                _completed(0, ""),
                _completed(0, '{"labels":[{"name":"agent-review"}]}'),
            ]
            assert add_pr_label("o/r", 10, "agent-review") is True

        calls = [call.args[0] for call in run_mock.call_args_list]
        assert calls[0] == [
            "api",
            "repos/o/r/issues/10",
            "--jq",
            ".labels[].name",
        ]
        assert calls[1][:4] == ["label", "create", "agent-review", "--repo"]
        assert calls[1][4] == "o/r"
        assert "--color" in calls[1]
        assert calls[1][calls[1].index("--color") + 1] == "5319E7"
        assert calls[2] == [
            "api",
            "-X",
            "POST",
            "repos/o/r/issues/10/labels",
            "-f",
            "labels[]=agent-review",
        ]

    def test_failure_returns_false_and_prints_output(self) -> None:
        stderr_capture = io.StringIO()
        with patch.object(dispatch, "_gh_run") as run_mock, patch.object(
            sys, "stderr", stderr_capture
        ):
            run_mock.side_effect = [
                _completed(0, ""),
                _completed(0, ""),
                _completed(1, "", "HTTP 403: forbidden\n"),
            ]
            assert add_pr_label("o/r", 10, "agent-review") is False

        assert "HTTP 403: forbidden" in stderr_capture.getvalue()

    def test_success_returns_true(self) -> None:
        with patch.object(dispatch, "_gh_run") as run_mock:
            run_mock.side_effect = [
                _completed(0, "other\n"),
                _completed(0, ""),
                _completed(0, ""),
            ]
            assert add_pr_label("o/r", 10, "agent-review") is True


class TestEnsureAgentRoleLabel:
    def test_ignores_unknown_label(self) -> None:
        with patch.object(dispatch, "_gh_run") as run_mock:
            ensure_agent_role_label("o/r", "agent-unknown")
        run_mock.assert_not_called()


class TestClaudeFatalOnLabelFailure:
    def test_feature_push_exits_nonzero_for_claude(self) -> None:
        event = {"ref": "refs/heads/feature/66-my-feature"}
        with patch.object(dispatch, "gh_json", return_value=[{"number": 10}]), patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(dispatch, "add_pr_label", return_value=False), patch.object(
            dispatch, "launch_role_on_pr"
        ) as launch_mock, patch.dict(
            "os.environ",
            {"REVIEW_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 1
        launch_mock.assert_not_called()

    def test_feature_push_cursor_continues_on_label_failure(self) -> None:
        event = {"ref": "refs/heads/feature/66-my-feature"}
        with patch.object(dispatch, "gh_json", return_value=[{"number": 10}]), patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(dispatch, "add_pr_label", return_value=False), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock, patch.dict(
            "os.environ",
            {"GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            import os

            os.environ.pop("REVIEW_PROVIDER", None)
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        launch_mock.assert_called_once()

    def test_handle_label_exits_nonzero_for_claude(self) -> None:
        event = {
            "label": {"name": "agent-review"},
            "pull_request": {
                "number": 10,
                "title": "PR",
                "body": "Closes #66",
                "html_url": "https://github.com/o/r/pull/10",
            },
        }
        with patch.object(dispatch, "add_pr_label", return_value=False), patch.object(
            dispatch, "launch_role"
        ) as launch_mock, patch.dict(
            "os.environ",
            {"REVIEW_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = handle_label("o/r", "https://github.com/o/r", event)

        assert result == 1
        launch_mock.assert_not_called()

    def test_handle_label_cursor_continues_on_label_failure(self) -> None:
        event = {
            "label": {"name": "agent-review"},
            "pull_request": {
                "number": 10,
                "title": "PR",
                "body": "Closes #66",
                "html_url": "https://github.com/o/r/pull/10",
            },
        }
        with patch.object(dispatch, "add_pr_label", return_value=False), patch.object(
            dispatch, "launch_role", return_value=0
        ) as launch_mock, patch.dict(
            "os.environ",
            {"GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            import os

            os.environ.pop("REVIEW_PROVIDER", None)
            result = handle_label("o/r", "https://github.com/o/r", event)

        assert result == 0
        launch_mock.assert_called_once()
