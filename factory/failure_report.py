"""Best-effort factory failure comments and factory-blocked status."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from status_labels import (
    issue_number_from_branch,
    issue_number_from_pr_body,
    resolve_issue_number_for_pr,
    set_factory_status,
)

_FAILURE_REPORTED = False
_SECRET_PATTERNS = (
    re.compile(r"(?i)(gh_token|github_token|factory_github_token|cursor_api_key)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(ghp_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{20,})\b"),
)


def factory_failure_marker(run_id: str) -> str:
    return f"<!-- factory:failure:{run_id} -->"


def _sanitize_text(text: str) -> str:
    out = text or ""
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("***", out)
    return out


def _last_lines(text: str, *, limit: int = 20) -> str:
    lines = (text or "").splitlines()
    excerpt = "\n".join(lines[-limit:])
    return _sanitize_text(excerpt)


def _load_event() -> dict[str, Any] | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return None
    try:
        return json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _owner_from_env() -> str:
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in owner_repo:
        return ""
    return owner_repo.split("/", 1)[0]


def _resolve_comment_target(
    event: dict[str, Any] | None, owner_repo: str
) -> int | None:
    if not event:
        return None

    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict) and pull_request.get("number") is not None:
        return int(pull_request["number"])

    issue = event.get("issue")
    if isinstance(issue, dict) and issue.get("number") is not None:
        return int(issue["number"])

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "push":
        ref = event.get("ref") or ""
        prefix = "refs/heads/"
        if not ref.startswith(prefix):
            return None
        branch = ref[len(prefix) :]
        if not branch.startswith("feature/"):
            return None
        issue_number = issue_number_from_branch(branch)
        if issue_number is None:
            return None
        owner = _owner_from_env()
        if not owner:
            return None
        try:
            from dispatch import gh_json

            existing = gh_json(
                [
                    "pr",
                    "list",
                    "--repo",
                    owner_repo,
                    "--base",
                    "dev",
                    "--head",
                    f"{owner}:{branch}",
                    "--state",
                    "open",
                    "--json",
                    "number",
                ]
            )
            if existing:
                return int(existing[0]["number"])
        except Exception:
            return issue_number
        return issue_number

    return None


def _resolve_ticket_issue_number(
    event: dict[str, Any] | None, owner_repo: str, comment_target: int
) -> int | None:
    if event:
        pull_request = event.get("pull_request")
        if isinstance(pull_request, dict):
            from_branch = issue_number_from_branch(pull_request.get("head", {}).get("ref") or "")
            if from_branch is not None:
                return from_branch
            from_body = issue_number_from_pr_body(pull_request.get("body") or "")
            if from_body is not None:
                return from_body

        issue = event.get("issue")
        if isinstance(issue, dict) and not issue.get("pull_request"):
            number = issue.get("number")
            if number is not None:
                return int(number)

        if os.environ.get("GITHUB_EVENT_NAME") == "push":
            ref = event.get("ref") or ""
            prefix = "refs/heads/"
            if ref.startswith(prefix):
                from_push = issue_number_from_branch(ref[len(prefix) :])
                if from_push is not None:
                    return from_push

    try:
        return resolve_issue_number_for_pr(owner_repo, comment_target)
    except Exception:
        return None


def _issue_has_factory_done(owner_repo: str, issue_number: int) -> bool:
    try:
        from dispatch import gh_json

        issue = gh_json(
            [
                "issue",
                "view",
                str(issue_number),
                "--repo",
                owner_repo,
                "--json",
                "labels",
            ]
        )
        labels = [label["name"] for label in (issue.get("labels") or [])]
        return "factory-done" in labels
    except Exception:
        return False


def _comment_bodies(owner_repo: str, number: int) -> str:
    from dispatch import issue_comment_bodies

    return issue_comment_bodies(owner_repo, number)


def _post_failure_comment(owner_repo: str, number: int, body: str) -> None:
    from dispatch import _gh_run

    result = _gh_run(
        [
            "issue",
            "comment",
            str(number),
            "--repo",
            owner_repo,
            "--body",
            body,
        ]
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        print(
            f"Could not post factory failure comment on #{number}: {combined.strip()}",
            file=sys.stderr,
        )


def _failure_step_and_output(exc: BaseException | None, *, exit_code: int | None) -> tuple[str, str]:
    if isinstance(exc, subprocess.CalledProcessError):
        cmd = " ".join(str(part) for part in (exc.cmd or [])[:6])
        step = f"GitHub CLI failed ({cmd})"
        combined = (exc.stdout or "") + (exc.stderr or "") + str(exc)
        return step, _last_lines(combined)
    if exc is not None:
        from cursor_sdk import CursorAgentError

        if isinstance(exc, CursorAgentError):
            step = f"Cursor agent startup failed: {exc.message}"
            return step, _last_lines(str(exc))
        step = f"{type(exc).__name__}: {exc}"
        return step, _last_lines(traceback.format_exc())

    if exit_code is not None and exit_code != 0:
        return f"Factory dispatcher exited with code {exit_code}", ""
    return "Factory dispatcher failed", ""


def _actions_run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not repo or not run_id:
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"


def report_factory_failure(
    exc: BaseException | None = None,
    *,
    exit_code: int | None = None,
) -> None:
    """Post one failure comment and set factory-blocked; never raises."""
    global _FAILURE_REPORTED
    if _FAILURE_REPORTED:
        return
    _FAILURE_REPORTED = True

    try:
        owner_repo = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" not in owner_repo:
            print("report_factory_failure: GITHUB_REPOSITORY missing; skipping comment", file=sys.stderr)
            return

        run_id = os.environ.get("GITHUB_RUN_ID", "").strip() or "unknown"
        marker = factory_failure_marker(run_id)
        event = _load_event()
        comment_target = _resolve_comment_target(event, owner_repo)
        if comment_target is None:
            print(
                "report_factory_failure: could not resolve issue/PR for comment; skipping",
                file=sys.stderr,
            )
            return

        try:
            existing = _comment_bodies(owner_repo, comment_target)
            if marker in existing:
                return
        except Exception as err:
            print(
                f"report_factory_failure: could not read comments on #{comment_target}: {err}",
                file=sys.stderr,
            )

        step, excerpt = _failure_step_and_output(exc, exit_code=exit_code)
        run_url = _actions_run_url()
        lines = [
            marker,
            "**Factory step failed**",
            "",
            f"**Step:** {step}",
        ]
        if run_url:
            lines.extend(["", f"**Actions run:** {run_url}"])
        if excerpt.strip():
            lines.extend(["", "**Output (last lines):**", "", f"```\n{excerpt}\n```"])
        body = "\n".join(lines)

        try:
            _post_failure_comment(owner_repo, comment_target, body)
        except Exception as err:
            print(f"report_factory_failure: comment post failed: {err}", file=sys.stderr)
            return

        ticket = _resolve_ticket_issue_number(event, owner_repo, comment_target)
        if ticket is None:
            return
        if _issue_has_factory_done(owner_repo, ticket):
            return
        try:
            set_factory_status(owner_repo, ticket, "factory-blocked")
        except Exception as err:
            print(
                f"report_factory_failure: could not set factory-blocked on #{ticket}: {err}",
                file=sys.stderr,
            )
    except Exception as err:
        print(f"report_factory_failure: unexpected error: {err}", file=sys.stderr)
