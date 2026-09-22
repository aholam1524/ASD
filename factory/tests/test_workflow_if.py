"""Guardrail: test-result FAIL must not trigger dispatch on non-PR issue comments."""

from __future__ import annotations

from pathlib import Path


def test_issue_comment_fail_is_grouped_with_pull_request() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "agent-factory.yml"
    ).read_text(encoding="utf-8")

    assert "factory:test-result:fail" in workflow

    # Parenthesized PASS || FAIL must immediately follow the PR issue_comment guard.
    needle = (
        "github.event_name == 'issue_comment' && github.event.issue.pull_request &&\n"
        "        (contains(github.event.comment.body, 'factory:test-result:pass') ||\n"
        "         contains(github.event.comment.body, 'factory:test-result:fail') ||\n"
        "         contains(github.event.comment.body, 'factory:conflict-resolved'))"
    )
    assert needle in workflow, (
        "FAIL and conflict-resolved markers must be OR'd with PASS inside the same issue_comment+PR group"
    )

    bad = (
        "contains(github.event.comment.body, 'factory:test-result:pass')) ||\n"
        "      contains(github.event.comment.body, 'factory:test-result:fail')"
    )
    assert bad not in workflow

    bad_conflict = (
        "contains(github.event.comment.body, 'factory:test-result:fail')) ||\n"
        "      contains(github.event.comment.body, 'factory:conflict-resolved')"
    )
    assert bad_conflict not in workflow
