"""Tests for FACTORY_RELEASE_MODE=per-ticket: one PR per ticket straight to main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dispatch
from dispatch import (
    factory_release_mode,
    is_promotion_pr,
    promote_dev_merge_to_release,
    release_branch_name,
)


class TestFactoryReleaseMode:
    def test_defaults_to_cumulative(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert factory_release_mode() == "cumulative"

    def test_per_ticket_opt_in(self) -> None:
        with patch.dict("os.environ", {"FACTORY_RELEASE_MODE": "per-ticket"}, clear=False):
            assert factory_release_mode() == "per-ticket"


class TestReleaseBranchName:
    def test_slugs_title_like_feature_branch(self) -> None:
        assert release_branch_name(12, "Add a clamp helper") == "release/12-add-a-clamp-helper"


class TestIsPromotionPrReleaseBranch:
    def test_release_branch_into_main_is_a_promotion_pr(self) -> None:
        assert is_promotion_pr("main", "release/12-add-a-clamp-helper")

    def test_unrelated_pr_is_not(self) -> None:
        assert not is_promotion_pr("dev", "feature/12-add-a-clamp-helper")


class TestPromoteDevMergeToRelease:
    def _ok(self, stdout: str = "", stderr: str = "") -> MagicMock:
        result = MagicMock()
        result.returncode = 0
        result.stdout = stdout
        result.stderr = stderr
        return result

    def test_opens_independent_pr_and_launches_test(self) -> None:
        merged = {
            "number": 5,
            "headRefName": "feature/12-add-a-clamp-helper",
            "title": "Add a clamp helper",
        }

        with patch.object(dispatch.subprocess, "run", return_value=self._ok()) as run_mock, patch.object(
            dispatch, "ensure_promotion_pr", return_value=42
        ) as ensure_mock, patch.object(dispatch, "ensure_promotion_pr_has_ticket") as ticket_mock, patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock, patch.object(
            dispatch, "start_oldest_factory_queued", return_value=0
        ) as start_next_mock:
            result = promote_dev_merge_to_release("o/r", "https://github.com/o/r", "o", merged)

        assert result == 0
        ensure_mock.assert_called_once()
        assert ensure_mock.call_args.kwargs["base"] == "main"
        assert ensure_mock.call_args.kwargs["head"] == "release/12-add-a-clamp-helper"
        ticket_mock.assert_called_once_with("o/r", 42, 12)
        launch_mock.assert_called_once_with(
            "o/r",
            "https://github.com/o/r",
            "test",
            42,
            factory_issue_number=12,
        )
        # The dev slot this ticket held is free now; the next queued ticket
        # starts immediately instead of waiting for this one to reach main.
        start_next_mock.assert_called_once_with("o/r", "https://github.com/o/r")
        # git fetch/checkout/merge/push all happened via subprocess
        commands = [call.args[0][:2] for call in run_mock.call_args_list]
        assert ["git", "fetch"] in commands
        assert ["git", "checkout"] in commands
        assert ["git", "merge"] in commands
        assert ["git", "push"] in commands

    def test_next_queued_ticket_not_started_when_queue_empty(self) -> None:
        merged = {
            "number": 5,
            "headRefName": "feature/12-add-a-clamp-helper",
            "title": "Add a clamp helper",
        }

        with patch.object(dispatch.subprocess, "run", return_value=self._ok()), patch.object(
            dispatch, "ensure_promotion_pr", return_value=42
        ), patch.object(dispatch, "ensure_promotion_pr_has_ticket"), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ), patch.object(dispatch, "oldest_queued_issue", return_value=None) as oldest_mock:
            result = promote_dev_merge_to_release("o/r", "https://github.com/o/r", "o", merged)

        assert result == 0
        oldest_mock.assert_called_once()

    def test_configures_git_identity_before_merging(self) -> None:
        merged = {
            "number": 5,
            "headRefName": "feature/12-add-a-clamp-helper",
            "title": "Add a clamp helper",
        }

        with patch.object(dispatch.subprocess, "run", return_value=self._ok()) as run_mock, patch.object(
            dispatch, "ensure_promotion_pr", return_value=42
        ), patch.object(dispatch, "ensure_promotion_pr_has_ticket"), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ), patch.object(dispatch, "start_oldest_factory_queued", return_value=0):
            promote_dev_merge_to_release("o/r", "https://github.com/o/r", "o", merged)

        commands = [call.args[0] for call in run_mock.call_args_list]
        assert ["git", "config", "user.email", "factory-bot@users.noreply.github.com"] in commands
        assert ["git", "config", "user.name", "factory-bot"] in commands
        # Identity must be configured before the merge that needs it.
        identity_index = commands.index(["git", "config", "user.name", "factory-bot"])
        merge_index = next(
            i for i, c in enumerate(commands) if c[:2] == ["git", "merge"] and "--no-ff" in c
        )
        assert identity_index < merge_index

    def test_merge_conflict_aborts_and_reports_without_opening_pr(self) -> None:
        merged = {
            "number": 5,
            "headRefName": "feature/12-add-a-clamp-helper",
            "title": "Add a clamp helper",
        }

        def run_side_effect(args, **kwargs):
            if args[:2] == ["git", "merge"] and "--no-ff" in args:
                fail = MagicMock()
                fail.returncode = 1
                fail.stdout = ""
                fail.stderr = "CONFLICT (content): merge conflict in x.py"
                return fail
            return self._ok()

        with patch.object(dispatch.subprocess, "run", side_effect=run_side_effect), patch.object(
            dispatch, "ensure_promotion_pr"
        ) as ensure_mock, patch.object(dispatch, "post_comment") as comment_mock:
            result = promote_dev_merge_to_release("o/r", "https://github.com/o/r", "o", merged)

        assert result == 1
        ensure_mock.assert_not_called()
        comment_mock.assert_called_once()
        args = comment_mock.call_args[0]
        assert args[1] == 12
        assert "conflicts" in args[2].lower()

    def test_non_conflict_git_failure_is_not_reported_as_a_conflict(self) -> None:
        """Regression: a git failure unrelated to a real conflict (for example
        a missing committer identity) must not be reported as 'conflicts with
        main' — that message tells the user to do a manual merge that would
        succeed trivially and hides the real problem."""
        merged = {
            "number": 5,
            "headRefName": "feature/12-add-a-clamp-helper",
            "title": "Add a clamp helper",
        }

        def run_side_effect(args, **kwargs):
            if args[:2] == ["git", "merge"] and "--no-ff" in args:
                fail = MagicMock()
                fail.returncode = 1
                fail.stdout = ""
                fail.stderr = "fatal: empty ident name (for <runner@host>) not allowed"
                return fail
            return self._ok()

        with patch.object(dispatch.subprocess, "run", side_effect=run_side_effect), patch.object(
            dispatch, "ensure_promotion_pr"
        ) as ensure_mock, patch.object(dispatch, "post_comment") as comment_mock:
            result = promote_dev_merge_to_release("o/r", "https://github.com/o/r", "o", merged)

        assert result == 1
        ensure_mock.assert_not_called()
        comment_mock.assert_called_once()
        body = comment_mock.call_args[0][2]
        assert "not a merge conflict" in body.lower()
        assert "empty ident name" in body
