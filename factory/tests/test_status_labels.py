"""Tests for factory status label helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dispatch
import status_labels
from dispatch import launch_role
from status_labels import (
    apply_factory_status_for_launch,
    issue_number_from_branch,
    issue_number_from_pr_body,
    set_factory_status,
)


class TestIssueNumberFromBranch:
    def test_feature_branch(self) -> None:
        assert issue_number_from_branch("feature/66-set-factory-status") == 66

    def test_non_feature(self) -> None:
        assert issue_number_from_branch("dev") is None


class TestIssueNumberFromPrBody:
    def test_closes_line(self) -> None:
        assert issue_number_from_pr_body("Closes #66\n") == 66

    def test_ticket_line(self) -> None:
        assert issue_number_from_pr_body("Ticket #74\n") == 74


class TestSetFactoryStatus:
    def test_replaces_other_factory_labels(self) -> None:
        gh_json = MagicMock(
            return_value={
                "labels": [
                    {"name": "factory-dev"},
                    {"name": "agent-review"},
                    {"name": "bug"},
                ]
            }
        )
        run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        set_factory_status("o/r", 66, "factory-review", gh_json=gh_json, run=run)

        gh_json.assert_called_once()
        args = run.call_args[0][0]
        assert args[:4] == ["gh", "issue", "edit", "66"]
        assert "--remove-label" in args
        assert "factory-dev" in args
        assert "factory-review" in args
        assert "agent-review" not in args
        assert "bug" not in args

    def test_no_edit_when_already_correct(self) -> None:
        gh_json = MagicMock(return_value={"labels": [{"name": "factory-review"}]})
        run = MagicMock()

        set_factory_status("o/r", 66, "factory-review", gh_json=gh_json, run=run)

        run.assert_not_called()


class TestLaunchSetsFactoryStatus:
    def test_dev_launch_sets_factory_dev_on_issue(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent", return_value=("agent-1", "run-1")
        ), patch.object(dispatch, "post_comment"), patch.object(
            status_labels, "set_factory_status"
        ) as status_mock, patch.dict(
            "os.environ", {"CURSOR_API_KEY": "key", "GITHUB_REPOSITORY": "o/r"}
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
        status_mock.assert_called_once_with("o/r", 66, "factory-dev")

    def test_review_launch_sets_factory_review_on_issue(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "launch_agent", return_value=("agent-1", "run-1")
        ), patch.object(dispatch, "post_comment"), patch.object(
            status_labels, "resolve_issue_number_for_pr", return_value=66
        ), patch.object(status_labels, "set_factory_status") as status_mock, patch.dict(
            "os.environ", {"CURSOR_API_KEY": "key", "GITHUB_REPOSITORY": "o/r"}
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
        status_mock.assert_called_once_with("o/r", 66, "factory-waiting-dev")

    def test_skip_already_launched_still_sets_status(self) -> None:
        with patch.object(dispatch, "comment_has_marker", return_value=True), patch.object(
            dispatch, "launch_agent"
        ) as launch_mock, patch.object(
            dispatch, "apply_factory_status_for_launch"
        ) as status_mock, patch.dict(
            "os.environ", {"CURSOR_API_KEY": "key", "GITHUB_REPOSITORY": "o/r"}
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
        status_mock.assert_called_once()


class TestApplyFactoryStatusForLaunch:
    def test_test_on_main_pr_uses_waiting_main(self) -> None:
        with patch.object(
            status_labels,
            "factory_status_for_test_pr",
            return_value="factory-waiting-main",
        ), patch.object(status_labels, "set_factory_status") as status_mock:
            apply_factory_status_for_launch(
                "o/r",
                "test",
                number=99,
                is_pr=True,
                factory_issue_number=66,
            )
        status_mock.assert_called_once_with("o/r", 66, "factory-waiting-main")


class TestMarkIssueFactoryDone:
    def test_marks_single_issue(self) -> None:
        with patch.object(status_labels, "set_factory_status") as status_mock:
            status_labels.mark_issue_factory_done("o/r", 74)
        status_mock.assert_called_once_with("o/r", 74, "factory-done")

    def test_skips_when_no_issue_number(self) -> None:
        with patch.object(status_labels, "set_factory_status") as status_mock:
            status_labels.mark_issue_factory_done("o/r", None)
        status_mock.assert_not_called()


class TestConflictAndPromotionIssueResolution:
    def test_apply_conflict_uses_factory_issue_number(self) -> None:
        with patch.object(status_labels, "set_factory_status") as status_mock:
            apply_factory_status_for_launch(
                "o/r",
                "conflict",
                number=20,
                is_pr=True,
                factory_issue_number=74,
            )
        status_mock.assert_called_once_with("o/r", 74, "factory-conflict")

    def test_resolve_issue_from_promotion_pr_body(self) -> None:
        gh_json = MagicMock(
            return_value={"headRefName": "dev", "body": "<!-- promote -->\nCloses #74\n"}
        )
        assert status_labels.resolve_issue_number_for_pr("o/r", 20, gh_json=gh_json) == 74
