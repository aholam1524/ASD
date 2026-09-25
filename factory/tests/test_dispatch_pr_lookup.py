"""PR lookup and idempotent create behavior in factory/dispatch.py."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import dispatch
import pytest
from dispatch import (
    DEV_BRANCH,
    ensure_promotion_pr,
    filter_open_prs_by_head_owner,
    handle_feature_push,
    list_open_prs_for_head,
    resolve_pr_number_after_create_failure,
)


class TestListOpenPrsForHead:
    def test_gh_pr_list_uses_plain_head_branch_name(self) -> None:
        captured: list[list[str]] = []

        def capture(args: list[str]) -> list[dict]:
            captured.append(args)
            return [
                {
                    "number": 63,
                    "headRepositoryOwner": {"login": "acme"},
                }
            ]

        with patch.object(dispatch, "gh_json", side_effect=capture):
            result = list_open_prs_for_head(
                "acme/repo",
                "acme",
                base="dev",
                head="feature/52-my-branch",
                json_fields="number",
            )

        assert result == [{"number": 63, "headRepositoryOwner": {"login": "acme"}}]
        assert len(captured) == 1
        args = captured[0]
        head_index = args.index("--head")
        assert args[head_index + 1] == "feature/52-my-branch"
        assert "acme:feature" not in " ".join(args)
        json_index = args.index("--json")
        assert "headRepositoryOwner" in args[json_index + 1]

    def test_filter_open_prs_by_head_owner_excludes_fork(self) -> None:
        prs = [
            {"number": 1, "headRepositoryOwner": {"login": "forker"}},
            {"number": 2, "headRepositoryOwner": {"login": "acme"}},
        ]
        assert filter_open_prs_by_head_owner(prs, "acme") == [prs[1]]


class TestHandleFeaturePushPrLookup:
    def test_second_push_reuses_same_pr_number(self) -> None:
        event = {"ref": "refs/heads/feature/52-something"}
        pr_row = {"number": 63, "headRepositoryOwner": {"login": "o"}}

        with patch.object(
            dispatch, "list_open_prs_for_head", return_value=[pr_row]
        ) as list_mock, patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(dispatch, "add_pr_label", return_value=True), patch.object(
            dispatch, "launch_role_on_pr", return_value=0
        ) as launch_mock, patch.object(dispatch.subprocess, "run") as run_mock:
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        list_mock.assert_called_once_with(
            "o/r", "o", base=DEV_BRANCH, head="feature/52-something", json_fields="number"
        )
        launch_mock.assert_called_once_with("o/r", "https://github.com/o/r", "review", 63)
        run_mock.assert_not_called()

    def test_create_already_exists_recovers_pr_and_continues(self) -> None:
        event = {"ref": "refs/heads/feature/52-something"}
        issue = {"title": "Fix thing"}
        create_result = MagicMock()
        create_result.returncode = 1
        create_result.stdout = ""
        create_result.stderr = (
            "a pull request for branch feature/52-something into branch dev already exists: "
            "https://github.com/o/r/pull/63\n"
        )
        create_result.check_returncode.side_effect = subprocess.CalledProcessError(
            1, "gh"
        )

        def list_side_effect(*_args, **_kwargs):
            return []

        with patch.object(
            dispatch, "list_open_prs_for_head", side_effect=list_side_effect
        ), patch.object(
            dispatch, "gh_json", return_value=issue
        ), patch.object(dispatch, "comment_has_marker", return_value=False), patch.object(
            dispatch, "add_pr_label", return_value=True
        ), patch.object(dispatch, "launch_role_on_pr", return_value=0) as launch_mock, patch.object(
            dispatch.subprocess, "run", return_value=create_result
        ):
            result = handle_feature_push("o/r", "https://github.com/o/r", "o", event)

        assert result == 0
        launch_mock.assert_called_once_with("o/r", "https://github.com/o/r", "review", 63)

    def test_unrelated_create_failure_raises(self) -> None:
        event = {"ref": "refs/heads/feature/52-something"}
        issue = {"title": "Fix thing"}
        create_result = MagicMock()
        create_result.returncode = 1
        create_result.stdout = ""
        create_result.stderr = "GraphQL error: something went wrong\n"
        create_result.check_returncode.side_effect = subprocess.CalledProcessError(
            1, "gh"
        )

        with patch.object(dispatch, "list_open_prs_for_head", return_value=[]), patch.object(
            dispatch, "gh_json", return_value=issue
        ), patch.object(dispatch.subprocess, "run", return_value=create_result):
            with pytest.raises(subprocess.CalledProcessError):
                handle_feature_push("o/r", "https://github.com/o/r", "o", event)


class TestEnsurePromotionPrRecovery:
    def test_create_already_exists_reuses_promotion_pr(self) -> None:
        create_result = MagicMock()
        create_result.returncode = 1
        create_result.stdout = ""
        create_result.stderr = (
            "pull request already exists: https://github.com/acme/repo/pull/88\n"
        )
        create_result.check_returncode.side_effect = subprocess.CalledProcessError(
            1, "gh"
        )

        with patch.object(dispatch, "list_open_prs_for_head", return_value=[]), patch.object(
            dispatch.subprocess, "run", return_value=create_result
        ):
            pr_number = ensure_promotion_pr(
                "acme/repo",
                "acme",
                base="test",
                head="dev",
                title="Promote",
                body="body",
                marker="<!-- m -->",
            )

        assert pr_number == 88

    def test_unrelated_create_failure_raises(self) -> None:
        create_result = MagicMock()
        create_result.returncode = 1
        create_result.stdout = ""
        create_result.stderr = "permission denied\n"
        create_result.check_returncode.side_effect = subprocess.CalledProcessError(
            1, "gh"
        )

        with patch.object(dispatch, "list_open_prs_for_head", return_value=[]), patch.object(
            dispatch.subprocess, "run", return_value=create_result
        ):
            with pytest.raises(subprocess.CalledProcessError):
                ensure_promotion_pr(
                    "acme/repo",
                    "acme",
                    base="test",
                    head="dev",
                    title="Promote",
                    body="body",
                    marker="<!-- m -->",
                )


class TestResolvePrNumberAfterCreateFailure:
    def test_lookup_after_message_without_url(self) -> None:
        with patch.object(
            dispatch,
            "list_open_prs_for_head",
            return_value=[{"number": 5, "headRepositoryOwner": {"login": "o"}}],
        ) as list_mock:
            number = resolve_pr_number_after_create_failure(
                "o/r",
                "o",
                base="dev",
                head="feature/1-x",
                combined="pull request already exists\n",
            )
        assert number == 5
        list_mock.assert_called_once()
