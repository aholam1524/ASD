"""Tests for Fixer launch on Test FAIL and post-fix Test re-run."""

from __future__ import annotations

from unittest.mock import patch

import dispatch
from dispatch import (
    FAIL_MARKER,
    handle_feature_push,
    handle_test_fail,
    role_marker,
    after_fix_test_marker,
)


def _feature_pr(number: int = 10) -> dict:
    return {
        "number": number,
        "state": "OPEN",
        "baseRefName": "dev",
        "headRefName": "feature/10-something",
        "title": "Feature",
        "body": "body",
        "url": f"https://github.com/o/r/pull/{number}",
    }


def _promotion_pr_dev_to_test(number: int = 20) -> dict:
    return {
        "number": number,
        "state": "OPEN",
        "baseRefName": "test",
        "headRefName": "dev",
        "title": "Promote",
        "body": "body",
        "url": f"https://github.com/o/r/pull/{number}",
    }


class TestHandleTestFail:
    def test_feature_pr_fail_launches_fixer(self) -> None:
        pr = _feature_pr(10)
        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock:
            result = handle_test_fail("o/r", "https://github.com/o/r", 10)

        assert result == 0
        launch_mock.assert_called_once_with("o/r", "https://github.com/o/r", "fix", 10)

    def test_feature_pr_fail_skips_when_fix_marker_exists(self) -> None:
        pr = _feature_pr(10)
        fix_marker = role_marker("fix", 10)

        def has_marker(_repo: str, _num: int, marker: str) -> bool:
            return marker == fix_marker

        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "comment_has_marker", side_effect=has_marker
        ), patch.object(dispatch, "post_comment") as comment_mock, patch.object(
            dispatch, "launch_role_on_pr"
        ) as launch_mock:
            result = handle_test_fail("o/r", "https://github.com/o/r", 10)

        assert result == 0
        launch_mock.assert_not_called()
        comment_mock.assert_called_once()
        assert "already ran" in comment_mock.call_args[0][2].lower()

    def test_promotion_pr_fail_does_not_launch_fixer(self) -> None:
        pr = _promotion_pr_dev_to_test(20)
        with patch.object(dispatch, "gh_json", return_value=pr), patch.object(
            dispatch, "post_comment"
        ) as comment_mock, patch.object(dispatch, "launch_role_on_pr") as launch_mock:
            result = handle_test_fail("o/r", "https://github.com/o/r", 20)

        assert result == 0
        launch_mock.assert_not_called()
        comment_mock.assert_called_once()
        assert "no automatic merge" in comment_mock.call_args[0][2].lower()
        assert "no fixer" in comment_mock.call_args[0][2].lower()


class TestHandleFeaturePushAfterFix:
    def test_post_fix_push_launches_test_after_fix_once(self) -> None:
        event = {"ref": "refs/heads/feature/10-something"}
        pr_list = [{"number": 10}]
        fix_marker = role_marker("fix", 10)
        expected_marker = after_fix_test_marker(10, 1)

        def has_marker(_repo: str, _num: int, marker: str) -> bool:
            return marker == fix_marker

        with patch.object(dispatch, "gh_json", return_value=pr_list), patch.object(
            dispatch, "comment_has_marker", side_effect=has_marker
        ), patch.object(dispatch, "count_marker_occurrences", return_value=1), patch.object(
            dispatch, "count_test_after_fix_launches", return_value=0
        ), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock, patch.object(
            dispatch, "add_pr_label"
        ) as label_mock:
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        label_mock.assert_not_called()
        launch_mock.assert_called_once()
        args, kwargs = launch_mock.call_args
        assert args[2] == "test"
        assert kwargs["marker"] == expected_marker
        assert kwargs["idempotency_key"] == "factory-test-after-fix-o/r-10-1"

    def test_second_fix_attempt_push_launches_test_after_fix_again(self) -> None:
        event = {"ref": "refs/heads/feature/10-something"}
        pr_list = [{"number": 10}]
        fix_marker = role_marker("fix", 10)
        expected_marker = after_fix_test_marker(10, 2)

        def has_marker(_repo: str, _num: int, marker: str) -> bool:
            return marker == fix_marker

        with patch.object(dispatch, "gh_json", return_value=pr_list), patch.object(
            dispatch, "comment_has_marker", side_effect=has_marker
        ), patch.object(dispatch, "count_marker_occurrences", return_value=2), patch.object(
            dispatch, "count_test_after_fix_launches", return_value=1
        ), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock:
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        launch_mock.assert_called_once()
        _, kwargs = launch_mock.call_args
        assert kwargs["marker"] == expected_marker
        assert kwargs["idempotency_key"] == "factory-test-after-fix-o/r-10-2"

    def test_post_fix_push_skips_when_test_after_fix_caught_up(self) -> None:
        event = {"ref": "refs/heads/feature/10-something"}
        pr_list = [{"number": 10}]
        fix_marker = role_marker("fix", 10)

        def has_marker(_repo: str, _num: int, marker: str) -> bool:
            return marker == fix_marker

        with patch.object(dispatch, "gh_json", return_value=pr_list), patch.object(
            dispatch, "comment_has_marker", side_effect=has_marker
        ), patch.object(dispatch, "count_marker_occurrences", return_value=1), patch.object(
            dispatch, "count_test_after_fix_launches", return_value=1
        ), patch.object(dispatch, "launch_role_on_pr") as launch_mock:
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        launch_mock.assert_not_called()


class TestFailMarkerConstant:
    def test_fail_marker_string(self) -> None:
        assert FAIL_MARKER == "<!-- factory:test-result:fail -->"
