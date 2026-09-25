"""Factory pipeline status as exactly one `factory-*` label on the GitHub issue."""

from __future__ import annotations

import re
import subprocess
from typing import Callable

FEATURE_BRANCH_RE = re.compile(r"^feature/(\d+)(?:-|$)")
CLOSES_ISSUE_RE = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.I)
TICKET_ISSUE_RE = re.compile(r"Ticket\s+#(\d+)", re.I)

FACTORY_STATUS_LABELS: frozenset[str] = frozenset(
    {
        "factory-queued",
        "factory-dev",
        "factory-review",
        "factory-waiting-dev",
        "factory-test",
        "factory-fixer",
        "factory-conflict",
        "factory-waiting-main",
        "factory-done",
        "factory-blocked",
    }
)

LABEL_CREATE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("factory-queued", "C5DEF5", "Ticket queued for Dev"),
    ("factory-dev", "1D76DB", "Dev agent running"),
    ("factory-review", "5319E7", "Review agent on feature PR"),
    ("factory-waiting-dev", "BFDADC", "Review done; merge feature PR into dev"),
    ("factory-test", "0E8A16", "Test agent on dev→test promotion PR"),
    ("factory-fixer", "D93F0B", "Fixer agent on feature PR"),
    ("factory-conflict", "FBCA04", "Conflict agent on dev→test PR"),
    ("factory-waiting-main", "FEF2C0", "test→main PR open; merge to main"),
    ("factory-done", "006B75", "Work merged to main"),
    ("factory-blocked", "B60205", "Test failed after Fixer; manual retry"),
)


def issue_number_from_branch(branch: str) -> int | None:
    match = FEATURE_BRANCH_RE.match(branch or "")
    if not match:
        return None
    return int(match.group(1))


def issue_number_from_pr_body(body: str) -> int | None:
    text = body or ""
    match = CLOSES_ISSUE_RE.search(text)
    if match:
        return int(match.group(1))
    match = TICKET_ISSUE_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def ensure_factory_status_labels(owner_repo: str) -> None:
    for name, color, description in LABEL_CREATE_SPECS:
        result = subprocess.run(
            [
                "gh",
                "label",
                "create",
                name,
                "--repo",
                owner_repo,
                "--color",
                color,
                "--description",
                description,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            combined = (result.stdout or "") + (result.stderr or "")
            if "already exists" in combined.lower():
                continue
            print(f"Could not create label {name!r}: {combined.strip()}")


def set_factory_status(
    owner_repo: str,
    issue_number: int,
    status: str,
    *,
    gh_json: Callable[..., object] | None = None,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> None:
    if status not in FACTORY_STATUS_LABELS:
        raise ValueError(f"Not a factory status label: {status!r}")

    if gh_json is None:
        from dispatch import gh_json as default_gh_json

        gh_json = default_gh_json
    if run is None:
        run = subprocess.run

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
    current = [label["name"] for label in (issue.get("labels") or [])]
    to_remove = [name for name in current if name in FACTORY_STATUS_LABELS and name != status]
    if not to_remove and status in current:
        return
    args = ["gh", "issue", "edit", str(issue_number), "--repo", owner_repo]
    for name in to_remove:
        args.extend(["--remove-label", name])
    if status not in current:
        args.extend(["--add-label", status])
    result = run(args, capture_output=True, text=True)
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        print(
            f"Could not set factory status {status!r} on issue #{issue_number}: "
            f"{combined.strip()}"
        )
        return
    print(f"Issue #{issue_number} factory status -> {status!r}")


def resolve_issue_number_for_pr(
    owner_repo: str,
    pr_number: int,
    *,
    gh_json: Callable[..., object] | None = None,
) -> int | None:
    if gh_json is None:
        from dispatch import gh_json as default_gh_json

        gh_json = default_gh_json

    pr = gh_json(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            owner_repo,
            "--json",
            "headRefName,body",
        ]
    )
    head = pr.get("headRefName") or ""
    from_branch = issue_number_from_branch(head)
    if from_branch is not None:
        return from_branch
    return issue_number_from_pr_body(pr.get("body") or "")


def factory_status_for_test_pr(
    owner_repo: str,
    pr_number: int,
    *,
    gh_json: Callable[..., object] | None = None,
) -> str:
    if gh_json is None:
        from dispatch import gh_json as default_gh_json

        gh_json = default_gh_json

    pr = gh_json(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            owner_repo,
            "--json",
            "baseRefName,headRefName",
        ]
    )
    base = pr.get("baseRefName") or ""
    head = pr.get("headRefName") or ""
    if base == "main" and head == "test":
        return "factory-waiting-main"
    return "factory-test"


def apply_factory_status_for_launch(
    owner_repo: str,
    role: str,
    *,
    number: int,
    is_pr: bool,
    factory_issue_number: int | None = None,
) -> None:
    issue_number = factory_issue_number
    if issue_number is None and not is_pr:
        issue_number = number
    if issue_number is None and is_pr:
        issue_number = resolve_issue_number_for_pr(owner_repo, number)
    if issue_number is None:
        print(f"No issue number for factory status (role={role}, #{number})")
        return

    if role == "dev":
        set_factory_status(owner_repo, issue_number, "factory-dev")
    elif role == "review":
        set_factory_status(owner_repo, issue_number, "factory-waiting-dev")
    elif role == "fix":
        set_factory_status(owner_repo, issue_number, "factory-fixer")
    elif role == "conflict":
        set_factory_status(owner_repo, issue_number, "factory-conflict")
    elif role == "test":
        status = factory_status_for_test_pr(owner_repo, number)
        set_factory_status(owner_repo, issue_number, status)
    else:
        print(f"No factory status mapping for role {role!r}")


MAIN_MERGE_CLOSE_COMMENT = "Merged to main, closing."


def mark_issue_factory_done(
    owner_repo: str,
    issue_number: int | None,
    *,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> None:
    if issue_number is None:
        print("No issue number for factory-done after merge to main")
        return
    if run is None:
        run = subprocess.run
    set_factory_status(owner_repo, issue_number, "factory-done", run=run)
    comment = run(
        [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            owner_repo,
            "--body",
            MAIN_MERGE_CLOSE_COMMENT,
        ],
        capture_output=True,
        text=True,
    )
    if comment.returncode != 0:
        combined = (comment.stdout or "") + (comment.stderr or "")
        print(
            f"Could not comment on issue #{issue_number} before close: {combined.strip()}"
        )
    close = run(
        [
            "gh",
            "issue",
            "close",
            str(issue_number),
            "--repo",
            owner_repo,
        ],
        capture_output=True,
        text=True,
    )
    if close.returncode != 0:
        combined = (close.stdout or "") + (close.stderr or "")
        print(f"Could not close issue #{issue_number}: {combined.strip()}")
        return
    print(f"Issue #{issue_number} closed after merge to main")
