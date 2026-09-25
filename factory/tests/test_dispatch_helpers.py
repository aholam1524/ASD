"""Tests for small helpers in factory/dispatch.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dispatch
from dispatch import (
    ensure_promotion_pr,
    feature_branch_name,
    pr_number_from_create_output,
    promote_test_to_main,
    waiting_main_notice_marker,
)


class TestPrNumberFromCreateOutput:
    def test_pull_url_in_text(self) -> None:
        assert pr_number_from_create_output(
            "https://github.com/o/r/pull/99\n"
        ) == 99

    def test_json_with_integer_number(self) -> None:
        assert pr_number_from_create_output('{"number": 12, "url": "x"}') == 12

    def test_empty_string(self) -> None:
        assert pr_number_from_create_output("") is None
        assert pr_number_from_create_output("   ") is None

    def test_unparseable_garbage(self) -> None:
        assert pr_number_from_create_output("totally not json or a url") is None


class TestFeatureBranchName:
    def test_includes_issue_number_and_slugs_title(self) -> None:
        name = feature_branch_name(
            34, "Add pytest coverage for factory dispatch helpers"
        )
        assert name == "feature/34-add-pytest-coverage-for-factory-dispatch-helpers"

    def test_strips_punctuation(self) -> None:
        assert feature_branch_name(1, "Hello!!! World???") == "feature/1-hello-world"


class TestEnsurePromotionPr:
    def test_returns_number_from_create_stdout_when_list_empty(self) -> None:
        create_result = MagicMock()
        create_result.returncode = 0
        create_result.stdout = "https://github.com/acme/repo/pull/777\n"
        create_result.stderr = ""

        with patch.object(dispatch, "gh_json", return_value=[]), patch.object(
            dispatch.subprocess, "run", return_value=create_result
        ) as run_mock:
            pr_number = ensure_promotion_pr(
                "acme/repo",
                "acme",
                base="test",
                head="dev",
                title="Promote dev to test",
                body="body",
                marker="<!-- marker -->",
            )

        assert pr_number == 777
        assert any(
            call.args[0][:3] == ["gh", "pr", "create"]
            for call in run_mock.call_args_list
        )


class TestPromoteTestToMain:
    def test_sets_waiting_main_and_comments_without_test_launch(self) -> None:
        merged = {"number": 20}
        notice = waiting_main_notice_marker(99)

        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "ensure_promotion_pr", return_value=99
        ), patch.object(dispatch, "ensure_promotion_pr_has_ticket"), patch.object(
            dispatch, "resolve_issue_number_for_pr", return_value=66
        ) as resolve_mock, patch.object(
            dispatch, "set_factory_status"
        ) as status_mock, patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            dispatch, "launch_role_on_pr"
        ) as launch_mock:
            result = promote_test_to_main(
                "o/r",
                "https://github.com/o/r",
                "o",
                merged_pr=merged,
            )

        assert result == 0
        resolve_mock.assert_called_once_with("o/r", 20)
        launch_mock.assert_not_called()
        status_mock.assert_called_once_with("o/r", 66, "factory-waiting-main")
        comment_mock.assert_called_once()
        body = comment_mock.call_args[0][2]
        assert notice in body
        assert "dev` → `test`" in body
        assert "merge" in body.lower()

    def test_skips_duplicate_notice_comment(self) -> None:
        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "ensure_promotion_pr", return_value=99
        ), patch.object(dispatch, "ensure_promotion_pr_has_ticket"), patch.object(
            dispatch, "resolve_issue_number_for_pr", return_value=66
        ), patch.object(dispatch, "set_factory_status"), patch.object(
            dispatch, "comment_has_marker", return_value=True
        ), patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            dispatch, "launch_role_on_pr"
        ) as launch_mock:
            promote_test_to_main("o/r", "https://github.com/o/r", "o", merged_pr={"number": 20})

        launch_mock.assert_not_called()
        comment_mock.assert_not_called()

    def test_works_without_merged_pr_resolves_ticket_from_promotion_pr(self) -> None:
        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "ensure_promotion_pr", return_value=99
        ), patch.object(dispatch, "ensure_promotion_pr_has_ticket"        ), patch.object(
            dispatch, "resolve_issue_number_for_pr", return_value=74
        ), patch.object(dispatch, "set_factory_status") as status_mock, patch.object(
            dispatch, "comment_has_marker", return_value=True
        ), patch.object(dispatch, "launch_role_on_pr") as launch_mock:
            promote_test_to_main("o/r", "https://github.com/o/r", "o")

        launch_mock.assert_not_called()
        status_mock.assert_called_once_with("o/r", 74, "factory-waiting-main")
