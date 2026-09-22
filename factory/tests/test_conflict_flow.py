"""Tests for Conflict agent on failed auto-merge into test and conflict-resolved re-test."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dispatch
from dispatch import (
    PASS_MARKER,
    after_conflict_test_marker,
    conflict_resolved_marker,
    handle_conflict_resolved,
    handle_merge_conflict,
    handle_test_pass,
    is_merge_conflict_error,
    role_marker,
)


def _promotion_pr_dev_to_test(number: int = 20) -> dict:
    return {
        "number": number,
        "state": "OPEN",
        "baseRefName": "test",
        "headRefName": "dev",
        "title": "Promote dev to test",
        "body": "body",
        "url": f"https://github.com/o/r/pull/{number}",
        "mergedAt": None,
    }


class TestIsMergeConflictError:
    def test_detects_common_conflict_messages(self) -> None:
        assert is_merge_conflict_error("Pull Request is not mergeable")
        assert is_merge_conflict_error("merge conflict between dev and test")
        assert is_merge_conflict_error("Mergeable state: dirty")

    def test_rejects_unrelated_errors(self) -> None:
        assert not is_merge_conflict_error("Resource not accessible by integration")
        assert not is_merge_conflict_error("HTTP 403: Forbidden")


class TestHandleTestPassMergeFailure:
    def test_conflict_launches_conflict_agent(self) -> None:
        pr = _promotion_pr_dev_to_test(20)
        merge_fail = MagicMock()
        merge_fail.returncode = 1
        merge_fail.stdout = ""
        merge_fail.stderr = "Pull Request is not mergeable (merge conflict)"

        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "post_comment"
        ), patch.object(dispatch, "run_ci", return_value=(True, "ok")), patch.object(
            dispatch.subprocess, "run", return_value=merge_fail
        ), patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(
            dispatch, "resolve_issue_number_for_pr", return_value=74
        ), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock:
            result = handle_test_pass("o/r", "https://github.com/o/r", "o", 20)

        assert result == 0
        launch_mock.assert_called_once_with(
            "o/r",
            "https://github.com/o/r",
            "conflict",
            20,
            factory_issue_number=74,
        )

    def test_non_conflict_merge_error_does_not_launch_conflict(self) -> None:
        pr = _promotion_pr_dev_to_test(20)
        merge_fail = MagicMock()
        merge_fail.returncode = 1
        merge_fail.stdout = ""
        merge_fail.stderr = "HTTP 403: Resource not accessible by integration"

        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "post_comment"
        ) as comment_mock, patch.object(dispatch, "run_ci", return_value=(True, "ok")), patch.object(
            dispatch.subprocess, "run", return_value=merge_fail
        ), patch.object(dispatch, "launch_role_on_pr") as launch_mock:
            result = handle_test_pass("o/r", "https://github.com/o/r", "o", 20)

        assert result == 0
        launch_mock.assert_not_called()
        comment_mock.assert_called()
        last_body = comment_mock.call_args_list[-1][0][2]
        assert "not a merge conflict" in last_body.lower()
        assert "not launching conflict" in last_body.lower()


class TestHandleMergeConflict:
    def test_skips_when_conflict_marker_already_present(self) -> None:
        conflict_marker = role_marker("conflict", 20)

        def has_marker(_repo: str, _num: int, marker: str) -> bool:
            return marker == conflict_marker

        with patch.object(dispatch, "gh_json", return_value=_promotion_pr_dev_to_test(20)), patch.object(
            dispatch, "comment_has_marker", side_effect=has_marker
        ), patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            dispatch, "launch_role_on_pr"
        ) as launch_mock:
            result = handle_merge_conflict("o/r", "https://github.com/o/r", 20, "conflict")

        assert result == 0
        launch_mock.assert_not_called()
        comment_mock.assert_called_once()
        assert "already ran" in comment_mock.call_args[0][2].lower()


class TestHandleConflictResolved:
    def test_launches_test_after_conflict(self) -> None:
        pr = _promotion_pr_dev_to_test(20)
        expected_marker = after_conflict_test_marker(20, 1)
        resolved = conflict_resolved_marker(20)

        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "count_marker_occurrences", side_effect=lambda _r, _n, m: 1 if m == resolved else 0
        ), patch.object(dispatch, "count_test_after_conflict_launches", return_value=0), patch.object(
            dispatch, "resolve_issue_number_for_pr", return_value=74
        ), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock:
            result = handle_conflict_resolved("o/r", "https://github.com/o/r", 20)

        assert result == 0
        launch_mock.assert_called_once()
        args, kwargs = launch_mock.call_args
        assert args[2] == "test"
        assert kwargs["marker"] == expected_marker
        assert kwargs["idempotency_key"] == "factory-test-after-conflict-o/r-20-1"
        assert kwargs["factory_issue_number"] == 74

    def test_second_conflict_resolved_launches_test_again(self) -> None:
        pr = _promotion_pr_dev_to_test(20)
        expected_marker = after_conflict_test_marker(20, 2)
        resolved = conflict_resolved_marker(20)

        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "count_marker_occurrences", side_effect=lambda _r, _n, m: 2 if m == resolved else 0
        ), patch.object(dispatch, "count_test_after_conflict_launches", return_value=1), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock:
            result = handle_conflict_resolved("o/r", "https://github.com/o/r", 20)

        assert result == 0
        launch_mock.assert_called_once()
        _, kwargs = launch_mock.call_args
        assert kwargs["marker"] == expected_marker
        assert kwargs["idempotency_key"] == "factory-test-after-conflict-o/r-20-2"


class TestPassMarkerConstant:
    def test_pass_marker_unchanged(self) -> None:
        assert PASS_MARKER == "<!-- factory:test-result:pass -->"
