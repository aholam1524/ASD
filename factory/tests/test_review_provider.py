"""Tests for REVIEW_PROVIDER=claude vs Cursor review launch."""

from __future__ import annotations

from unittest.mock import patch

import dispatch
import status_labels
from dispatch import (
    handle_feature_push,
    handle_label,
    launch_role,
    role_marker,
)


class TestReviewProviderClaude:
    def test_delegates_without_cursor_api_key(self) -> None:
        marker = role_marker("review", 10)
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent"
        ) as launch_mock, patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            status_labels, "resolve_issue_number_for_pr", return_value=66
        ), patch.object(status_labels, "set_factory_status") as status_mock, patch.dict(
            "os.environ",
            {"REVIEW_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
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
        comment_mock.assert_called_once()
        body = comment_mock.call_args[0][2]
        assert marker in body
        assert "Claude review workflow" in body
        status_mock.assert_called_once_with("o/r", 66, "factory-waiting-dev")

    def test_idempotency_skips_second_comment_but_sets_status(self) -> None:
        marker = role_marker("review", 10)
        with patch.object(dispatch, "comment_has_marker", return_value=True), patch.object(
            dispatch, "launch_agent"
        ) as launch_mock, patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            dispatch, "apply_factory_status_for_launch"
        ) as status_mock, patch.dict(
            "os.environ",
            {"REVIEW_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
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
        comment_mock.assert_not_called()
        status_mock.assert_called_once()

    def test_cursor_mode_still_requires_api_key(self) -> None:
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r"}, clear=True):
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
        assert result == 1

    def test_agent_review_label_uses_claude_path(self) -> None:
        event = {
            "label": {"name": "agent-review"},
            "pull_request": {
                "number": 10,
                "title": "PR",
                "body": "Closes #66",
                "html_url": "https://github.com/o/r/pull/10",
            },
        }
        with patch.object(dispatch, "add_pr_label", return_value=True), patch.object(
            dispatch, "launch_review_delegated_to_claude", return_value=0
        ) as delegate_mock, patch.dict(
            "os.environ",
            {"REVIEW_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = handle_label("o/r", "https://github.com/o/r", event)

        assert result == 0
        delegate_mock.assert_called_once()
        assert delegate_mock.call_args.kwargs["number"] == 10

    def test_feature_push_launches_review_via_claude(self) -> None:
        event = {"ref": "refs/heads/feature/66-my-feature"}
        pr_list = [{"number": 10}]
        issue = {"title": "My feature"}

        def gh_json_side_effect(args: list[str]):
            if args[:2] == ["pr", "list"]:
                return pr_list
            if args[:2] == ["issue", "view"]:
                return issue
            if args[:2] == ["pr", "view"]:
                return {
                    "number": 10,
                    "title": "PR",
                    "body": "Closes #66",
                    "url": "https://github.com/o/r/pull/10",
                }
            raise AssertionError(f"unexpected gh_json: {args}")

        with patch.object(dispatch, "gh_json", side_effect=gh_json_side_effect), patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(dispatch, "add_pr_label", return_value=True) as label_mock, patch.object(
            dispatch, "launch_review_delegated_to_claude", return_value=0
        ) as delegate_mock, patch.dict(
            "os.environ",
            {"REVIEW_PROVIDER": "claude", "GITHUB_REPOSITORY": "o/r"},
            clear=False,
        ):
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        label_mock.assert_called_once_with("o/r", 10, "agent-review")
        delegate_mock.assert_called_once()


class TestReviewProviderCursor:
    def test_unset_uses_cursor_launch(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent", return_value=("agent-1", "run-1")
        ) as launch_mock, patch.object(dispatch, "post_comment"), patch.object(
            status_labels, "resolve_issue_number_for_pr", return_value=66
        ), patch.object(status_labels, "set_factory_status"), patch.dict(
            "os.environ",
            {"CURSOR_API_KEY": "key", "GITHUB_REPOSITORY": "o/r"},
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
        launch_mock.assert_called_once()
