"""Tests for small helpers in factory/dispatch.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dispatch
from dispatch import ensure_promotion_pr, feature_branch_name, pr_number_from_create_output


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
