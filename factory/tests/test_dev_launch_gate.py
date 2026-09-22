"""Tests for skipping Dev when a feature/* -> dev PR is already open."""

from __future__ import annotations

from unittest.mock import patch

import dispatch
from dispatch import open_feature_pr_into_dev, skip_dev_if_feature_pr_open


class TestOpenFeaturePrIntoDev:
    def test_returns_first_feature_head_pr(self) -> None:
        prs = [
            {"number": 1, "headRefName": "dev", "url": "https://github.com/o/r/pull/1"},
            {
                "number": 2,
                "headRefName": "feature/10-something",
                "url": "https://github.com/o/r/pull/2",
            },
        ]
        with patch.object(dispatch, "gh_json", return_value=prs):
            found = open_feature_pr_into_dev("o/r")
        assert found == prs[1]

    def test_returns_none_when_no_feature_heads(self) -> None:
        prs = [
            {"number": 3, "headRefName": "dev", "url": "https://github.com/o/r/pull/3"},
        ]
        with patch.object(dispatch, "gh_json", return_value=prs):
            assert open_feature_pr_into_dev("o/r") is None

    def test_returns_none_when_list_empty(self) -> None:
        with patch.object(dispatch, "gh_json", return_value=[]):
            assert open_feature_pr_into_dev("o/r") is None


class TestSkipDevIfFeaturePrOpen:
    def test_skips_and_comments_when_feature_pr_open(self) -> None:
        blocking = {
            "number": 5,
            "headRefName": "feature/7-other",
            "url": "https://github.com/o/r/pull/5",
        }
        with patch.object(
            dispatch, "open_feature_pr_into_dev", return_value=blocking
        ), patch.object(dispatch, "post_comment") as comment_mock:
            skipped = skip_dev_if_feature_pr_open("o/r", 99)

        assert skipped is True
        comment_mock.assert_called_once()
        args = comment_mock.call_args[0]
        assert args[0] == "o/r"
        assert args[1] == 99
        assert "https://github.com/o/r/pull/5" in args[2]
        assert "agent-dev" in args[2]

    def test_does_not_skip_when_no_blocking_pr(self) -> None:
        with patch.object(
            dispatch, "open_feature_pr_into_dev", return_value=None
        ), patch.object(dispatch, "post_comment") as comment_mock:
            skipped = skip_dev_if_feature_pr_open("o/r", 99)

        assert skipped is False
        comment_mock.assert_not_called()
