"""Tests for factory issue queue and Start factory dispatch."""

from __future__ import annotations

from unittest.mock import patch

import dispatch
from dispatch import (
    FACTORY_QUEUED_LABEL,
    handle_start_factory,
    oldest_queued_issue,
    queue_issue_for_factory,
)


class TestQueueIssueOnOpen:
    def test_does_not_launch_dev(self) -> None:
        issue = {
            "number": 42,
            "title": "Example",
            "body": "body",
            "html_url": "https://github.com/o/r/issues/42",
        }
        with patch.object(
            dispatch, "set_factory_status"
        ) as status_mock, patch.object(
            dispatch, "post_comment"
        ) as comment_mock, patch.object(
            dispatch, "launch_role"
        ) as launch_mock:
            rc = queue_issue_for_factory("o/r", issue)

        assert rc == 0
        status_mock.assert_called_once_with("o/r", 42, FACTORY_QUEUED_LABEL)
        comment_mock.assert_called_once()
        assert "Start factory" in comment_mock.call_args[0][2]
        launch_mock.assert_not_called()


class TestOldestQueuedIssue:
    def test_picks_lowest_issue_number(self) -> None:
        issues = [
            {"number": 50, "title": "b"},
            {"number": 12, "title": "a"},
            {"number": 30, "title": "c"},
        ]
        with patch.object(dispatch, "gh_json", return_value=issues):
            found = oldest_queued_issue("o/r")
        assert found == issues[1]


class TestHandleStartFactory:
    def test_no_op_when_queue_empty(self) -> None:
        with patch.object(
            dispatch, "oldest_queued_issue", return_value=None
        ), patch.object(dispatch, "launch_role") as launch_mock:
            rc = handle_start_factory("o/r", "https://github.com/o/r")

        assert rc == 0
        launch_mock.assert_not_called()

    def test_launches_oldest_and_removes_label(self) -> None:
        issue = {
            "number": 7,
            "title": "Oldest",
            "body": "work",
            "url": "https://github.com/o/r/issues/7",
        }
        with patch.object(
            dispatch, "oldest_queued_issue", return_value=issue
        ), patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "open_feature_pr_into_dev", return_value=None
        ), patch.object(
            dispatch, "launch_role", return_value=0
        ) as launch_mock:
            rc = handle_start_factory("o/r", "https://github.com/o/r")

        assert rc == 0
        launch_mock.assert_called_once()
        assert launch_mock.call_args.kwargs["number"] == 7

    def test_skips_when_feature_pr_open(self) -> None:
        issue = {
            "number": 7,
            "title": "Oldest",
            "body": "work",
            "url": "https://github.com/o/r/issues/7",
        }
        with patch.object(
            dispatch, "oldest_queued_issue", return_value=issue
        ), patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "skip_dev_if_feature_pr_open", return_value=True
        ), patch.object(dispatch, "launch_role") as launch_mock:
            rc = handle_start_factory("o/r", "https://github.com/o/r")

        assert rc == 0
        launch_mock.assert_not_called()
