"""Tests for AGENT_PROVIDER=claude delegation of dev/test/fix/conflict roles."""

from __future__ import annotations

from unittest.mock import patch

import dispatch
from dispatch import launch_role, role_marker


class TestAgentProviderClaude:
    def test_dev_delegates_without_cursor_api_key(self) -> None:
        marker = role_marker("dev", 66)
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent"
        ) as launch_mock, patch.object(dispatch, "add_pr_label", return_value=True) as label_mock, patch.object(
            dispatch, "post_comment"
        ) as comment_mock, patch.object(
            dispatch, "apply_factory_status_for_launch"
        ) as status_mock, patch.dict(
            "os.environ",
            {"AGENT_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = launch_role(
                "o/r",
                "https://github.com/o/r",
                "dev",
                number=66,
                title="Title",
                body="Body",
                html_url="https://github.com/o/r/issues/66",
                is_pr=False,
            )

        assert result == 0
        launch_mock.assert_not_called()
        label_mock.assert_called_once_with("o/r", 66, "agent-dev")
        comment_mock.assert_called_once()
        body = comment_mock.call_args[0][2]
        assert marker in body
        assert "Claude Code workflow" in body
        status_mock.assert_called_once()

    def test_test_role_delegates_and_labels_pr(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent"
        ) as launch_mock, patch.object(dispatch, "add_pr_label", return_value=True) as label_mock, patch.object(
            dispatch, "post_comment"
        ), patch.object(dispatch, "apply_factory_status_for_launch"), patch.dict(
            "os.environ",
            {"AGENT_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = launch_role(
                "o/r",
                "https://github.com/o/r",
                "test",
                number=20,
                title="Promote",
                body="",
                html_url="https://github.com/o/r/pull/20",
                is_pr=True,
            )

        assert result == 0
        launch_mock.assert_not_called()
        label_mock.assert_called_once_with("o/r", 20, "agent-test")

    def test_label_failure_is_fatal(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "add_pr_label", return_value=False
        ), patch.object(dispatch, "post_comment") as comment_mock, patch.dict(
            "os.environ",
            {"AGENT_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = launch_role(
                "o/r",
                "https://github.com/o/r",
                "fix",
                number=20,
                title="Promote",
                body="",
                html_url="https://github.com/o/r/pull/20",
                is_pr=True,
            )

        assert result == 1
        comment_mock.assert_not_called()

    def test_idempotent_skip_still_sets_status(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=True), patch.object(
            dispatch, "add_pr_label"
        ) as label_mock, patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            dispatch, "apply_factory_status_for_launch"
        ) as status_mock, patch.dict(
            "os.environ",
            {"AGENT_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = launch_role(
                "o/r",
                "https://github.com/o/r",
                "conflict",
                number=20,
                title="Promote",
                body="",
                html_url="https://github.com/o/r/pull/20",
                is_pr=True,
            )

        assert result == 0
        label_mock.assert_not_called()
        comment_mock.assert_not_called()
        status_mock.assert_called_once()

    def test_review_role_is_unaffected_by_agent_provider(self) -> None:
        """AGENT_PROVIDER=claude also turns on Claude review (no separate
        REVIEW_PROVIDER needed), reusing the existing review delegation path."""
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent"
        ) as launch_mock, patch.object(dispatch, "post_comment"), patch.object(
            dispatch, "apply_factory_status_for_launch"
        ), patch.dict(
            "os.environ",
            {"AGENT_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            import os

            os.environ.pop("REVIEW_PROVIDER", None)
            result = launch_role(
                "o/r",
                "https://github.com/o/r",
                "review",
                number=10,
                title="Title",
                body="Body",
                html_url="https://github.com/o/r/pull/10",
                is_pr=True,
            )

        assert result == 0
        launch_mock.assert_not_called()


class TestAgentProviderCursorDefault(object):
    def test_dev_stays_on_cursor_when_unset(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent", return_value=("agent-1", "run-1")
        ) as launch_mock, patch.object(dispatch, "post_comment"), patch.object(
            dispatch, "apply_factory_status_for_launch"
        ), patch.dict(
            "os.environ",
            {"CURSOR_API_KEY": "key", "GITHUB_REPOSITORY": "o/r"},
            clear=True,
        ):
            result = launch_role(
                "o/r",
                "https://github.com/o/r",
                "dev",
                number=66,
                title="Title",
                body="Body",
                html_url="https://github.com/o/r/issues/66",
                is_pr=False,
            )

        assert result == 0
        launch_mock.assert_called_once()
