"""Tests for factory queue drain after merge to main and Start factory."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import dispatch
from dispatch import (
    handle_merged_pr_closed,
    start_oldest_factory_queued,
)


class TestStartOldestFactoryQueued:
    def test_starts_lowest_number_queued_issue(self) -> None:
        queued = [
            {"number": 40, "title": "Later", "body": "b", "url": "https://github.com/o/r/issues/40"},
            {"number": 12, "title": "First", "body": "a", "url": "https://github.com/o/r/issues/12"},
        ]
        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "list_factory_queued_issues", return_value=queued
        ), patch.object(
            dispatch, "skip_dev_if_feature_pr_open", return_value=False
        ), patch.object(
            dispatch, "remove_issue_label"
        ) as remove_mock, patch.object(
            dispatch, "launch_role", return_value=0
        ) as launch_mock:
            code = start_oldest_factory_queued("o/r", "https://github.com/o/r")

        assert code == 0
        remove_mock.assert_called_once_with("o/r", 12, dispatch.FACTORY_QUEUED_LABEL)
        launch_mock.assert_called_once()
        assert launch_mock.call_args.kwargs["number"] == 12
        assert launch_mock.call_args[0][2] == "dev"

    def test_idle_when_queue_empty(self) -> None:
        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "list_factory_queued_issues", return_value=[]
        ), patch.object(dispatch, "launch_role") as launch_mock:
            code = start_oldest_factory_queued("o/r", "https://github.com/o/r")

        assert code == 0
        launch_mock.assert_not_called()

    def test_skips_when_feature_pr_into_dev_open(self) -> None:
        queued = [
            {"number": 5, "title": "Queued", "body": "", "url": "https://github.com/o/r/issues/5"},
        ]
        with patch.object(dispatch, "ensure_branch"), patch.object(
            dispatch, "list_factory_queued_issues", return_value=queued
        ), patch.object(
            dispatch, "skip_dev_if_feature_pr_open", return_value=True
        ), patch.object(dispatch, "launch_role") as launch_mock, patch.object(
            dispatch, "remove_issue_label"
        ) as remove_mock:
            code = start_oldest_factory_queued("o/r", "https://github.com/o/r")

        assert code == 0
        launch_mock.assert_not_called()
        remove_mock.assert_not_called()


class TestMergeToMainDrainsQueue:
    def test_merged_into_main_starts_oldest_queued(self) -> None:
        pr = {
            "number": 900,
            "merged": True,
            "base": {"ref": "main"},
        }
        with patch.object(
            dispatch, "start_oldest_factory_queued", return_value=0
        ) as start_mock:
            code = handle_merged_pr_closed("o/r", "https://github.com/o/r", "o", pr)

        assert code == 0
        start_mock.assert_called_once_with("o/r", "https://github.com/o/r")

    def test_merged_into_dev_does_not_drain_queue(self) -> None:
        pr = {
            "number": 901,
            "merged": True,
            "base": {"ref": "dev"},
        }
        with patch.object(
            dispatch, "start_oldest_factory_queued"
        ) as start_mock, patch.object(
            dispatch, "promote_dev_to_test", return_value=0
        ):
            code = handle_merged_pr_closed("o/r", "https://github.com/o/r", "o", pr)

        assert code == 0
        start_mock.assert_not_called()

    def test_merged_into_test_does_not_drain_queue(self) -> None:
        pr = {
            "number": 902,
            "merged": True,
            "base": {"ref": "test"},
        }
        with patch.object(
            dispatch, "start_oldest_factory_queued"
        ) as start_mock, patch.object(
            dispatch, "promote_test_to_main", return_value=0
        ):
            code = handle_merged_pr_closed("o/r", "https://github.com/o/r", "o", pr)

        assert code == 0
        start_mock.assert_not_called()


class TestMainDispatchMergeHandler:
    def test_pull_request_closed_merged_to_main_calls_start(self, tmp_path: Path) -> None:
        event = {
            "action": "closed",
            "pull_request": {
                "number": 77,
                "merged": True,
                "base": {"ref": "main"},
            },
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")

        env = {
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": "pull_request",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(
            dispatch, "start_oldest_factory_queued", return_value=0
        ) as start_mock:
            code = dispatch.main([])

        assert code == 0
        start_mock.assert_called_once()
