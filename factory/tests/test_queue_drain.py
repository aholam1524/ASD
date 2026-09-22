"""Tests for draining the factory queue after merge to main."""

from __future__ import annotations

import json
from unittest.mock import patch

import dispatch
from cursor_sdk import CursorAgentError
from dispatch import (
    DEV_BRANCH,
    MAIN_BRANCH,
    TEST_BRANCH,
    handle_start_factory,
    launch_role,
    start_oldest_factory_queued,
)


class TestStartOldestFactoryQueued:
    def test_delegates_to_handle_start_factory(self) -> None:
        with patch.object(
            dispatch, "handle_start_factory", return_value=0
        ) as handle_mock:
            rc = start_oldest_factory_queued("o/r", "https://github.com/o/r")

        assert rc == 0
        handle_mock.assert_called_once_with("o/r", "https://github.com/o/r")


class TestDrainOnMainMerge:
    def test_closed_pr_merged_into_main_starts_queue(self, tmp_path, monkeypatch) -> None:
        event = {
            "action": "closed",
            "pull_request": {
                "merged": True,
                "number": 99,
                "base": {"ref": MAIN_BRANCH},
                "head": {"ref": "test"},
            },
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        with patch.object(
            dispatch, "ensure_factory_status_labels"
        ), patch.object(
            dispatch, "mark_issues_waiting_main_done"
        ) as done_mock, patch.object(
            dispatch, "start_oldest_factory_queued", return_value=0
        ) as drain_mock:
            rc = dispatch.main([])

        assert rc == 0
        done_mock.assert_called_once_with("o/r")
        drain_mock.assert_called_once_with("o/r", "https://github.com/o/r")

    def test_idle_queue_after_main_merge(self, tmp_path, monkeypatch) -> None:
        event = {
            "action": "closed",
            "pull_request": {
                "merged": True,
                "number": 100,
                "base": {"ref": MAIN_BRANCH},
            },
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        with patch.object(
            dispatch, "ensure_factory_status_labels"
        ), patch.object(
            dispatch, "mark_issues_waiting_main_done"
        ), patch.object(
            dispatch, "oldest_queued_issue", return_value=None
        ), patch.object(dispatch, "launch_role") as launch_mock:
            rc = dispatch.main([])

        assert rc == 0
        launch_mock.assert_not_called()

    def test_merge_into_dev_does_not_drain_queue(self, tmp_path, monkeypatch) -> None:
        event = {
            "action": "closed",
            "pull_request": {
                "merged": True,
                "number": 50,
                "base": {"ref": DEV_BRANCH},
                "headRefName": "feature/12-slug",
                "head": {"ref": "feature/12-slug"},
            },
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        with patch.object(
            dispatch, "ensure_factory_status_labels"
        ), patch.object(
            dispatch, "promote_dev_to_test", return_value=0
        ) as promote_mock, patch.object(
            dispatch, "start_oldest_factory_queued"
        ) as drain_mock:
            rc = dispatch.main([])

        assert rc == 0
        promote_mock.assert_called_once()
        drain_mock.assert_not_called()

    def test_merge_into_test_does_not_drain_queue(self, tmp_path, monkeypatch) -> None:
        event = {
            "action": "closed",
            "pull_request": {
                "merged": True,
                "number": 51,
                "base": {"ref": TEST_BRANCH},
            },
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        with patch.object(
            dispatch, "ensure_factory_status_labels"
        ), patch.object(
            dispatch, "promote_test_to_main", return_value=0
        ) as promote_mock, patch.object(
            dispatch, "start_oldest_factory_queued"
        ) as drain_mock:
            rc = dispatch.main([])

        assert rc == 0
        promote_mock.assert_called_once()
        drain_mock.assert_not_called()


class TestFeaturePrGateOnDrain:
    def test_skips_launch_when_feature_pr_open(self) -> None:
        issue = {
            "number": 3,
            "title": "Next ticket",
            "body": "body",
            "url": "https://github.com/o/r/issues/3",
        }
        with patch.object(
            dispatch, "oldest_queued_issue", return_value=issue
        ), patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "skip_dev_if_feature_pr_open", return_value=True
        ), patch.object(dispatch, "launch_role") as launch_mock:
            rc = handle_start_factory("o/r", "https://github.com/o/r")

        assert rc == 0
        launch_mock.assert_not_called()


class TestFailedLaunchKeepsQueued:
    def test_launch_failure_does_not_update_factory_status(self, monkeypatch) -> None:
        monkeypatch.setenv("CURSOR_API_KEY", "test-key")
        with patch.object(
            dispatch, "comment_has_marker", return_value=False
        ), patch.object(
            dispatch, "launch_agent", side_effect=CursorAgentError("boom")
        ), patch.object(
            dispatch, "apply_factory_status_for_launch"
        ) as status_mock:
            rc = launch_role(
                "o/r",
                "https://github.com/o/r",
                "dev",
                number=8,
                title="T",
                body="B",
                html_url="https://github.com/o/r/issues/8",
                is_pr=False,
            )

        assert rc == 1
        status_mock.assert_not_called()
