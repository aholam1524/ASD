"""Tests for factory issue author allow-list."""

from __future__ import annotations

from unittest.mock import patch

import dispatch
from dispatch import (
    allowed_factory_authors,
    handle_label,
    is_factory_author_allowed,
    oldest_queued_issue,
    queue_issue_for_factory,
)


class TestAllowedFactoryAuthors:
    def test_owner_only_by_default(self) -> None:
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r"}, clear=True):
            assert allowed_factory_authors("o/r") == frozenset({"o"})
            assert is_factory_author_allowed("o/r", "o") is True
            assert is_factory_author_allowed("o/r", "stranger") is False

    def test_env_var_adds_logins(self) -> None:
        env = {
            "GITHUB_REPOSITORY": "o/r",
            "FACTORY_ALLOWED_AUTHORS": " teammate , bot-account ",
        }
        with patch.dict("os.environ", env, clear=True):
            assert allowed_factory_authors("o/r") == frozenset(
                {"o", "teammate", "bot-account"}
            )
            assert is_factory_author_allowed("o/r", "teammate") is True


class TestQueueIssueAuthorCheck:
    def test_owner_is_queued(self) -> None:
        issue = {
            "number": 1,
            "title": "Work",
            "user": {"login": "o"},
        }
        with patch.object(dispatch, "set_factory_status") as status_mock, patch.object(
            dispatch, "post_comment"
        ) as comment_mock:
            rc = queue_issue_for_factory("o/r", issue)

        assert rc == 0
        status_mock.assert_called_once()
        comment_mock.assert_called_once()

    def test_stranger_is_ignored(self, capsys) -> None:
        issue = {
            "number": 2,
            "title": "Malicious",
            "user": {"login": "evil-user"},
        }
        with patch.object(dispatch, "set_factory_status") as status_mock, patch.object(
            dispatch, "post_comment"
        ) as comment_mock:
            rc = queue_issue_for_factory("o/r", issue)

        assert rc == 0
        status_mock.assert_not_called()
        comment_mock.assert_not_called()
        out = capsys.readouterr().out
        assert "Ignoring issue #2" in out
        assert "evil-user" in out


class TestOldestQueuedIssueAuthorFilter:
    def test_skips_disallowed_even_with_label(self) -> None:
        issues = [
            {
                "number": 5,
                "title": "blocked",
                "author": {"login": "stranger"},
            },
            {
                "number": 10,
                "title": "ok",
                "author": {"login": "o"},
            },
        ]
        with patch.object(dispatch, "gh_json", return_value=issues):
            found = oldest_queued_issue("o/r")
        assert found == issues[1]

    def test_returns_none_when_only_disallowed(self) -> None:
        issues = [
            {"number": 3, "title": "x", "author": {"login": "stranger"}},
        ]
        with patch.object(dispatch, "gh_json", return_value=issues):
            assert oldest_queued_issue("o/r") is None


class TestAgentDevRetryAuthorCheck:
    def test_agent_dev_on_stranger_issue_is_ignored(self, capsys) -> None:
        event = {
            "label": {"name": "agent-dev"},
            "issue": {
                "number": 9,
                "title": "Retry",
                "body": "body",
                "html_url": "https://github.com/o/r/issues/9",
                "user": {"login": "stranger"},
            },
        }
        with patch.object(dispatch, "ensure_branch") as branch_mock, patch.object(
            dispatch, "launch_role"
        ) as launch_mock:
            rc = handle_label("o/r", "https://github.com/o/r", event)

        assert rc == 0
        branch_mock.assert_not_called()
        launch_mock.assert_not_called()
        assert "Ignoring issue #9" in capsys.readouterr().out

    def test_agent_dev_on_owner_issue_proceeds(self) -> None:
        event = {
            "label": {"name": "agent-dev"},
            "issue": {
                "number": 9,
                "title": "Retry",
                "body": "body",
                "html_url": "https://github.com/o/r/issues/9",
                "user": {"login": "o"},
            },
        }
        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "skip_dev_if_feature_pr_open", return_value=False
        ), patch.object(dispatch, "launch_role", return_value=0) as launch_mock:
            rc = handle_label("o/r", "https://github.com/o/r", event)

        assert rc == 0
        launch_mock.assert_called_once()
