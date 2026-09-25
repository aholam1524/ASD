#!/usr/bin/env python3
"""Launch Cursor cloud agents and promote PRs along feature → dev → test → main.

The GitHub Action should exit after send(); the cloud VM keeps working.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from cursor_sdk import Agent, CloudAgentOptions, CloudRepository, CursorAgentError

from status_labels import (
    apply_factory_status_for_launch,
    ensure_factory_status_labels,
    issue_number_from_branch,
    mark_issue_factory_done,
    resolve_issue_number_for_pr,
    set_factory_status,
)

FACTORY_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = FACTORY_ROOT / "prompts"
CI_SCRIPT = FACTORY_ROOT / "run_ci.sh"
MODEL = "composer-2.5"
DEV_BRANCH = "dev"
TEST_BRANCH = "test"
MAIN_BRANCH = "main"
LABEL_ROLES = {
    "agent-dev": "dev",
    "agent-test": "test",
    "agent-review": "review",
    "agent-fix": "fix",
    "agent-conflict": "conflict",
}
PASS_MARKER = "<!-- factory:test-result:pass -->"
FAIL_MARKER = "<!-- factory:test-result:fail -->"
PROMOTE_DEV_TO_TEST = "<!-- factory:promote:dev-to-test -->"
PROMOTE_TEST_TO_MAIN = "<!-- factory:promote:test-to-main -->"
FACTORY_QUEUED_LABEL = "factory-queued"


def review_provider() -> str:
    value = os.environ.get("REVIEW_PROVIDER", "").strip().lower()
    if value in {"", "cursor"}:
        return "cursor"
    if value == "claude":
        return "claude"
    return "cursor"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in owner_repo:
        print("GITHUB_REPOSITORY is missing or invalid", file=sys.stderr)
        return 1

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo_url = f"{server}/{owner_repo}"
    owner = owner_repo.split("/", 1)[0]
    ensure_factory_status_labels(owner_repo)

    if args.role and args.pr:
        return launch_role_on_pr(owner_repo, repo_url, args.role, args.pr)

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    factory_command = os.environ.get("FACTORY_COMMAND", "").strip().lower()
    if factory_command == "start" or event_name == "workflow_dispatch":
        return handle_start_factory(owner_repo, repo_url)

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH is not set", file=sys.stderr)
        return 1

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    action = event.get("action") or ""

    if event_name == "issues" and action == "opened":
        issue = event.get("issue") or {}
        if issue.get("pull_request"):
            print("Ignoring pull_request issue-opened event")
            return 0
        return queue_issue_for_factory(owner_repo, issue)

    if event_name in {"issues", "pull_request"} and action == "labeled":
        return handle_label(owner_repo, repo_url, event)

    if event_name == "pull_request" and action == "opened":
        pr = event["pull_request"]
        base = pr.get("base", {}).get("ref") or ""
        head = pr.get("head", {}).get("ref") or ""
        if base == DEV_BRANCH and head.startswith("feature/"):
            return launch_role_on_pr(owner_repo, repo_url, "review", pr["number"])
        print(f"Ignoring opened PR #{pr['number']} {head} -> {base}")
        return 0

    if event_name == "pull_request" and action == "closed":
        pr = event["pull_request"]
        if not pr.get("merged"):
            print("PR closed without merge; ignoring")
            return 0
        base = pr.get("base", {}).get("ref") or ""
        if base == DEV_BRANCH:
            head_ref = pr.get("headRefName") or (pr.get("head") or {}).get("ref") or ""
            merged = {**pr, "headRefName": head_ref}
            return promote_dev_to_test(owner_repo, repo_url, owner, merged)
        if base == TEST_BRANCH:
            head_ref = pr.get("headRefName") or (pr.get("head") or {}).get("ref") or ""
            merged = {**pr, "headRefName": head_ref}
            return promote_test_to_main(owner_repo, repo_url, owner, merged_pr=merged)
        if base == MAIN_BRANCH:
            ticket = resolve_issue_number_for_pr(owner_repo, pr["number"])
            mark_issue_factory_done(owner_repo, ticket)
            print(f"Merged PR #{pr['number']} into {MAIN_BRANCH}; marked factory-done")
            return start_oldest_factory_queued(owner_repo, repo_url)
        print(f"Merged PR is not into {DEV_BRANCH} or {TEST_BRANCH}; ignoring")
        return 0

    if event_name == "issue_comment" and action == "created":
        return handle_issue_comment(owner_repo, repo_url, owner, event)

    if event_name == "push":
        return handle_feature_push(owner_repo, repo_url, owner, event)

    print(f"Ignoring event {event_name}.{action}")
    return 0


def handle_feature_push(owner_repo: str, repo_url: str, owner: str, event: dict) -> int:
    ref = event.get("ref") or ""
    prefix = "refs/heads/"
    if not ref.startswith(prefix):
        print(f"Ignoring push ref {ref!r}")
        return 0
    branch = ref[len(prefix) :]
    if not branch.startswith("feature/"):
        print(f"Ignoring push to {branch}")
        return 0

    issue_number = issue_number_from_branch(branch)
    if issue_number is None:
        print(f"Feature branch {branch} has no leading issue number; ignoring")
        return 0

    existing = gh_json(
        [
            "pr",
            "list",
            "--repo",
            owner_repo,
            "--base",
            DEV_BRANCH,
            "--head",
            f"{owner}:{branch}",
            "--state",
            "open",
            "--json",
            "number",
        ]
    )
    if existing:
        pr_number = existing[0]["number"]
        print(f"Reusing open PR #{pr_number} ({branch} -> {DEV_BRANCH})")
    else:
        issue = gh_json(
            ["issue", "view", str(issue_number), "--repo", owner_repo, "--json", "title"]
        )
        title = issue.get("title") or branch
        body = f"Closes #{issue_number}\n"
        created = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                owner_repo,
                "--base",
                DEV_BRANCH,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            combined = (created.stdout or "") + (created.stderr or "")
            print(combined, file=sys.stderr)
            created.check_returncode()
        pr_number = pr_number_from_create_output(created.stdout) or pr_number_from_create_output(
            created.stderr
        )
        if pr_number is None:
            raise RuntimeError(
                f"Created {branch} -> {DEV_BRANCH} PR but could not parse its number "
                f"from: {(created.stdout or created.stderr or '').strip()!r}"
            )
        print(f"Opened PR #{pr_number} ({branch} -> {DEV_BRANCH})")

    fix_marker = role_marker("fix", pr_number)
    if comment_has_marker(owner_repo, pr_number, fix_marker):
        fix_count = count_marker_occurrences(owner_repo, pr_number, fix_marker)
        test_after_count = count_test_after_fix_launches(owner_repo, pr_number)
        if fix_count > test_after_count:
            attempt = test_after_count + 1
            marker = after_fix_test_marker(pr_number, attempt)
            print(f"Post-fix push on PR #{pr_number}; launching Test (after fix #{attempt})")
            return launch_role_on_pr(
                owner_repo,
                repo_url,
                "test",
                pr_number,
                marker=marker,
                idempotency_key=f"factory-test-after-fix-{owner_repo}-{pr_number}-{attempt}",
            )
        print(
            f"Fixer already ran for PR #{pr_number} and Test-after-fix is up to date; ignoring push"
        )
        return 0

    add_pr_label(owner_repo, pr_number, "agent-review")
    return launch_role_on_pr(owner_repo, repo_url, "review", pr_number)


def pr_number_from_create_output(stdout: str) -> int | None:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        number = data.get("number")
        if isinstance(number, int):
            return number
    except json.JSONDecodeError:
        pass
    match = re.search(r"/pull/(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def add_pr_label(owner_repo: str, pr_number: int, label: str) -> None:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "edit",
            str(pr_number),
            "--repo",
            owner_repo,
            "--add-label",
            label,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Could not add label {label!r} to PR #{pr_number}: {(result.stderr or result.stdout).strip()}")
        return
    print(f"Added label {label!r} to PR #{pr_number}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent factory dispatcher")
    parser.add_argument("--role", choices=["dev", "test", "review", "fix"])
    parser.add_argument("--pr", type=int)
    args = parser.parse_args(argv)
    if (args.role is None) != (args.pr is None):
        parser.error("--role and --pr must be used together")
    return args


def handle_label(owner_repo: str, repo_url: str, event: dict) -> int:
    label = (event.get("label") or {}).get("name", "")
    role = LABEL_ROLES.get(label)
    if role is None:
        print(f"Ignoring unlabeled factory event: {label!r}")
        return 0

    issue = event.get("issue")
    pull_request = event.get("pull_request")
    is_pr = pull_request is not None or bool(issue and issue.get("pull_request"))
    target = pull_request or issue
    if target is None:
        print("Event has no issue or pull_request", file=sys.stderr)
        return 1

    number = target["number"]
    if role == "dev" and is_pr:
        post_comment(owner_repo, number, f"`agent-dev` is only valid on issues. Ignoring on PR #{number}.")
        return 0
    if role in {"test", "review", "fix", "conflict"} and not is_pr:
        post_comment(
            owner_repo,
            number,
            f"`agent-{role}` is only valid on pull requests. Ignoring on issue #{number}.",
        )
        return 0

    if role == "dev":
        ensure_branch(DEV_BRANCH, MAIN_BRANCH)
        if not is_pr and skip_dev_if_feature_pr_open(owner_repo, number):
            return 0

    html_url = target.get("html_url") or (
        f"{repo_url}/{'pull' if is_pr else 'issues'}/{number}"
    )
    force = role in {"fix", "conflict"}
    idempotency_key = None
    if force:
        retry_marker = role_marker(role, number)
        next_attempt = count_marker_occurrences(owner_repo, number, retry_marker) + 1
        if next_attempt > 1:
            idempotency_key = f"factory-{role}-{owner_repo}-{number}-{next_attempt}"
    return launch_role(
        owner_repo,
        repo_url,
        role,
        number=number,
        title=target.get("title") or "",
        body=target.get("body") or "",
        html_url=html_url,
        is_pr=is_pr,
        force=force,
        idempotency_key=idempotency_key,
    )


def handle_issue_comment(owner_repo: str, repo_url: str, owner: str, event: dict) -> int:
    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    body = comment.get("body") or ""
    user = (comment.get("user") or {}).get("login") or ""

    if not issue.get("pull_request"):
        print("Comment is not on a pull request; ignoring")
        return 0
    if user in {"github-actions[bot]", "github-actions"}:
        print("Ignoring comment from github-actions")
        return 0
    if PASS_MARKER in body:
        return handle_test_pass(owner_repo, repo_url, owner, issue["number"])
    if FAIL_MARKER in body:
        return handle_test_fail(owner_repo, repo_url, issue["number"])
    if "factory:conflict-resolved" in body:
        return handle_conflict_resolved(owner_repo, repo_url, issue["number"])
    print("Comment has no test result marker; ignoring")
    return 0


def handle_test_fail(owner_repo: str, repo_url: str, number: int) -> int:
    pr = gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            owner_repo,
            "--json",
            "number,title,body,url,state,baseRefName,headRefName",
        ]
    )
    if pr.get("state") != "OPEN":
        print(f"PR #{number} is not open; ignoring")
        return 0

    base = pr.get("baseRefName") or ""
    head = pr.get("headRefName") or ""

    if base == DEV_BRANCH and head.startswith("feature/"):
        fix_marker = role_marker("fix", number)
        if comment_has_marker(owner_repo, number, fix_marker):
            post_comment(
                owner_repo,
                number,
                "Test reported FAIL, but **Fixer** already ran for this PR. "
                "Not launching another Fixer. Add the `agent-fix` label to retry Fixer.",
            )
            blocked_issue = issue_number_from_branch(head)
            if blocked_issue is not None:
                set_factory_status(owner_repo, blocked_issue, "factory-blocked")
            print(f"Fixer marker already present on PR #{number}; skipping")
            return 0
        print(f"Test FAIL on feature PR #{number}; launching Fixer")
        return launch_role_on_pr(owner_repo, repo_url, "fix", number)

    if is_promotion_pr(base, head):
        post_comment(
            owner_repo,
            number,
            "Test reported FAIL on a promotion PR. There is no automatic merge and no Fixer for this PR.",
        )
        print(f"Test FAIL on promotion PR #{number}; not launching Fixer")
        return 0

    print(f"Test FAIL on PR #{number} ({head} -> {base}); ignoring")
    return 0


def is_promotion_pr(base: str, head: str) -> bool:
    return (base == TEST_BRANCH and head == DEV_BRANCH) or (
        base == MAIN_BRANCH and head == TEST_BRANCH
    )


def is_merge_conflict_error(output: str) -> bool:
    text = output.lower()
    needles = (
        "merge conflict",
        "not mergeable",
        "mergeable_state",
        "unmergeable",
        "dirty",
        "conflicting files",
    )
    return any(n in text for n in needles)


def handle_merge_conflict(
    owner_repo: str, repo_url: str, number: int, merge_output: str
) -> int:
    pr = gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            owner_repo,
            "--json",
            "number,baseRefName,headRefName,state",
        ]
    )
    base = pr.get("baseRefName") or ""
    head = pr.get("headRefName") or ""
    if base != TEST_BRANCH or head != DEV_BRANCH:
        post_comment(
            owner_repo,
            number,
            "Merge conflict reported, but this is not a `dev` → `test` promotion PR. "
            "Not launching Conflict.",
        )
        print(f"Merge conflict on non-promotion PR #{number} ({head} -> {base}); ignoring")
        return 0

    conflict_marker = role_marker("conflict", number)
    if comment_has_marker(owner_repo, number, conflict_marker):
        post_comment(
            owner_repo,
            number,
            "Auto-merge into `test` failed due to conflicts, but **Conflict** already ran for this PR. "
            "Add the `agent-conflict` label to retry Conflict.",
        )
        print(f"Conflict marker already present on PR #{number}; skipping")
        return 0

    post_comment(
        owner_repo,
        number,
        "CI passed but auto-merge into `test` failed due to merge conflicts. Launching **Conflict**…\n\n"
        f"```\n{merge_output[-2000:]}\n```",
    )
    print(f"Merge conflict on promotion PR #{number}; launching Conflict")
    factory_issue = resolve_issue_number_for_pr(owner_repo, number)
    return launch_role_on_pr(
        owner_repo,
        repo_url,
        "conflict",
        number,
        factory_issue_number=factory_issue,
    )


def handle_conflict_resolved(owner_repo: str, repo_url: str, number: int) -> int:
    pr = gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            owner_repo,
            "--json",
            "number,state,baseRefName,headRefName",
        ]
    )
    if pr.get("state") != "OPEN":
        print(f"PR #{number} is not open; ignoring conflict-resolved")
        return 0

    base = pr.get("baseRefName") or ""
    head = pr.get("headRefName") or ""
    if base != TEST_BRANCH or head != DEV_BRANCH:
        print(f"conflict-resolved on non-promotion PR #{number}; ignoring")
        return 0

    resolved_marker = conflict_resolved_marker(number)
    resolved_count = count_marker_occurrences(owner_repo, number, resolved_marker)
    test_after_count = count_test_after_conflict_launches(owner_repo, number)
    if resolved_count <= test_after_count:
        print(
            f"conflict-resolved on PR #{number} but Test-after-conflict is up to date; ignoring"
        )
        return 0

    attempt = test_after_count + 1
    marker = after_conflict_test_marker(number, attempt)
    print(f"conflict-resolved on PR #{number}; launching Test (after conflict #{attempt})")
    factory_issue = resolve_issue_number_for_pr(owner_repo, number)
    return launch_role_on_pr(
        owner_repo,
        repo_url,
        "test",
        number,
        marker=marker,
        idempotency_key=f"factory-test-after-conflict-{owner_repo}-{number}-{attempt}",
        factory_issue_number=factory_issue,
    )


def handle_test_pass(owner_repo: str, repo_url: str, owner: str, number: int) -> int:
    pr = gh_json(["pr", "view", str(number), "--repo", owner_repo, "--json",
                  "number,title,body,url,state,mergedAt,baseRefName,headRefName"])
    if pr.get("state") != "OPEN":
        print(f"PR #{number} is not open; ignoring")
        return 0

    post_comment(owner_repo, number, "Test agent reported PASS. Running CI…")
    ci_ok, ci_output = run_ci()
    if not ci_ok:
        post_comment(
            owner_repo,
            number,
            "CI failed. Not merging.\n\n```\n" + ci_output[-4000:] + "\n```",
        )
        return 0

    base = pr.get("baseRefName") or ""
    if base == MAIN_BRANCH:
        post_comment(
            owner_repo,
            number,
            "CI passed. This PR targets **main** — merge it yourself. No automatic merge.",
        )
        return 0

    if base != TEST_BRANCH:
        post_comment(
            owner_repo,
            number,
            f"CI passed. Automatic merge only runs for PRs into `{TEST_BRANCH}` (this one targets `{base}`).",
        )
        return 0

    merge_result = subprocess.run(
        ["gh", "pr", "merge", str(number), "--repo", owner_repo, "--merge"],
        capture_output=True,
        text=True,
    )
    if merge_result.returncode != 0:
        combined = (merge_result.stdout or "") + (merge_result.stderr or "")
        if is_merge_conflict_error(combined):
            return handle_merge_conflict(owner_repo, repo_url, number, combined)
        post_comment(
            owner_repo,
            number,
            "CI passed but merge into `test` failed (not a merge conflict). Not launching Conflict.\n\n"
            f"```\n{combined[-4000:]}\n```",
        )
        return 0

    post_comment(owner_repo, number, "CI passed. Merged into `test`.")
    merged = {**pr, "number": number}
    return promote_test_to_main(owner_repo, repo_url, owner, merged_pr=merged)


def promotion_ticket_line(issue_number: int) -> str:
    return f"Closes #{issue_number}\n"


def ensure_promotion_pr_has_ticket(
    owner_repo: str, pr_number: int, issue_number: int | None
) -> None:
    if issue_number is None:
        return
    if resolve_issue_number_for_pr(owner_repo, pr_number) is not None:
        return
    pr = gh_json(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            owner_repo,
            "--json",
            "body",
        ]
    )
    body = (pr.get("body") or "").rstrip()
    body = f"{body}\n\n{promotion_ticket_line(issue_number)}".strip() + "\n"
    result = subprocess.run(
        [
            "gh",
            "pr",
            "edit",
            str(pr_number),
            "--repo",
            owner_repo,
            "--body",
            body,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        print(f"Could not add ticket to promotion PR #{pr_number}: {combined.strip()}")


def promote_dev_to_test(owner_repo: str, repo_url: str, owner: str, merged_pr: dict) -> int:
    ensure_branch(DEV_BRANCH, MAIN_BRANCH)
    ensure_branch(TEST_BRANCH, MAIN_BRANCH)
    factory_issue = issue_number_from_branch(merged_pr.get("headRefName") or "")
    title = f"Promote {DEV_BRANCH} to {TEST_BRANCH}"
    ticket_line = promotion_ticket_line(factory_issue) if factory_issue else ""
    body = (
        f"{PROMOTE_DEV_TO_TEST}\n"
        f"Promotion after merging #{merged_pr['number']} into `{DEV_BRANCH}`.\n"
        f"{ticket_line}"
    )
    pr_number = ensure_promotion_pr(
        owner_repo,
        owner,
        base=TEST_BRANCH,
        head=DEV_BRANCH,
        title=title,
        body=body,
        marker=PROMOTE_DEV_TO_TEST,
    )
    if pr_number is None:
        post_comment(
            owner_repo,
            merged_pr["number"],
            f"Nothing to promote from `{DEV_BRANCH}` to `{TEST_BRANCH}` (branches are even).",
        )
        return 0
    ensure_promotion_pr_has_ticket(owner_repo, pr_number, factory_issue)
    return launch_role_on_pr(
        owner_repo,
        repo_url,
        "test",
        pr_number,
        factory_issue_number=factory_issue,
    )


def promote_test_to_main(
    owner_repo: str, repo_url: str, owner: str, *, merged_pr: dict | None = None
) -> int:
    ensure_branch(TEST_BRANCH, MAIN_BRANCH)
    factory_issue = None
    if merged_pr is not None:
        factory_issue = resolve_issue_number_for_pr(owner_repo, merged_pr["number"])
    title = f"Promote {TEST_BRANCH} to {MAIN_BRANCH}"
    ticket_line = promotion_ticket_line(factory_issue) if factory_issue else ""
    body = (
        f"{PROMOTE_TEST_TO_MAIN}\n"
        f"Promotion after merging into `{TEST_BRANCH}`. Merge this PR yourself; no automatic merge to main.\n"
        f"{ticket_line}"
    )
    pr_number = ensure_promotion_pr(
        owner_repo,
        owner,
        base=MAIN_BRANCH,
        head=TEST_BRANCH,
        title=title,
        body=body,
        marker=PROMOTE_TEST_TO_MAIN,
    )
    if pr_number is None:
        print("Nothing to promote from test to main")
        return 0
    ensure_promotion_pr_has_ticket(owner_repo, pr_number, factory_issue)
    return launch_role_on_pr(
        owner_repo,
        repo_url,
        "test",
        pr_number,
        factory_issue_number=factory_issue,
    )


def ensure_promotion_pr(
    owner_repo: str,
    owner: str,
    *,
    base: str,
    head: str,
    title: str,
    body: str,
    marker: str,
) -> int | None:
    existing = gh_json(
        [
            "pr",
            "list",
            "--repo",
            owner_repo,
            "--base",
            base,
            "--head",
            f"{owner}:{head}",
            "--state",
            "open",
            "--json",
            "number,body",
        ]
    )
    if existing:
        number = existing[0]["number"]
        print(f"Reusing open promotion PR #{number} ({head} -> {base})")
        return number

    created = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            owner_repo,
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        ],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        combined = (created.stdout or "") + (created.stderr or "")
        if "no commits between" in combined.lower() or "already up-to-date" in combined.lower():
            print(combined.strip())
            return None
        print(combined, file=sys.stderr)
        created.check_returncode()

    pr_number = pr_number_from_create_output(created.stdout) or pr_number_from_create_output(
        created.stderr
    )
    if pr_number is None:
        raise RuntimeError(
            f"Created {head} -> {base} PR but could not parse its number "
            f"from: {(created.stdout or created.stderr or '').strip()!r}"
        )
    print(f"Opened promotion PR #{pr_number} ({head} -> {base})")
    return pr_number


def launch_role_on_pr(
    owner_repo: str,
    repo_url: str,
    role: str,
    pr_number: int,
    *,
    marker: str | None = None,
    idempotency_key: str | None = None,
    force: bool = False,
    factory_issue_number: int | None = None,
) -> int:
    pr = gh_json(
        ["pr", "view", str(pr_number), "--repo", owner_repo, "--json", "number,title,body,url"]
    )
    return launch_role(
        owner_repo,
        repo_url,
        role,
        number=pr["number"],
        title=pr.get("title") or "",
        body=pr.get("body") or "",
        html_url=pr.get("url") or f"{repo_url}/pull/{pr_number}",
        is_pr=True,
        marker=marker,
        idempotency_key=idempotency_key,
        force=force,
        factory_issue_number=factory_issue_number,
    )


def role_marker(role: str, number: int) -> str:
    return f"<!-- factory:{role}:{number} -->"


def claude_review_delegation_comment(marker: str) -> str:
    return (
        f"{marker}\n"
        "**Review** delegated to the Claude review workflow (`REVIEW_PROVIDER=claude`).\n\n"
        "The `agent-review` label triggers the app-repo workflow; no Cursor cloud agent was launched."
    )


def launch_review_delegated_to_claude(
    owner_repo: str,
    *,
    number: int,
    is_pr: bool,
    marker: str,
    force: bool,
    factory_issue_number: int | None,
) -> int:
    if not force and comment_has_marker(owner_repo, number, marker):
        print(f"Already launched review for #{number}; skipping")
        apply_factory_status_for_launch(
            owner_repo,
            "review",
            number=number,
            is_pr=is_pr,
            factory_issue_number=factory_issue_number,
        )
        return 0

    post_comment(owner_repo, number, claude_review_delegation_comment(marker))
    apply_factory_status_for_launch(
        owner_repo,
        "review",
        number=number,
        is_pr=is_pr,
        factory_issue_number=factory_issue_number,
    )
    print(f"Delegated review for PR #{number} to Claude workflow (REVIEW_PROVIDER=claude)")
    return 0


def after_fix_test_marker(number: int, attempt: int) -> str:
    return f"<!-- factory:test-after-fix:{number}:{attempt} -->"


def after_conflict_test_marker(number: int, attempt: int) -> str:
    return f"<!-- factory:test-after-conflict:{number}:{attempt} -->"


def conflict_resolved_marker(number: int) -> str:
    return f"<!-- factory:conflict-resolved:{number} -->"


def launch_role(
    owner_repo: str,
    repo_url: str,
    role: str,
    *,
    number: int,
    title: str,
    body: str,
    html_url: str,
    is_pr: bool,
    marker: str | None = None,
    idempotency_key: str | None = None,
    force: bool = False,
    factory_issue_number: int | None = None,
) -> int:
    marker = marker or role_marker(role, number)
    if role == "review" and review_provider() == "claude":
        return launch_review_delegated_to_claude(
            owner_repo,
            number=number,
            is_pr=is_pr,
            marker=marker,
            force=force,
            factory_issue_number=factory_issue_number,
        )

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        print("CURSOR_API_KEY is not set", file=sys.stderr)
        return 1

    if not force and comment_has_marker(owner_repo, number, marker):
        print(f"Already launched {role} for #{number}; skipping")
        apply_factory_status_for_launch(
            owner_repo,
            role,
            number=number,
            is_pr=is_pr,
            factory_issue_number=factory_issue_number,
        )
        return 0

    extra = ""
    if role == "dev":
        extra = (
            f"Required Git branch name: `{feature_branch_name(number, title)}`\n"
            f"Base branch for the pull request: `{DEV_BRANCH}`\n"
        )

    prompt = build_prompt(
        role,
        number=number,
        title=title,
        body=body,
        html_url=html_url,
        extra=extra,
    )
    repo = cloud_repo(repo_url, role=role, pr_url=html_url)
    metadata = {"role": role, "issue" if role == "dev" else "pr": str(number)}

    try:
        agent_id, run_id = launch_agent(
            api_key=api_key,
            role=role,
            number=number,
            repo=repo,
            prompt=prompt,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
    except CursorAgentError as err:
        print(
            f"startup failed: {err.message}, retryable={err.is_retryable}",
            file=sys.stderr,
        )
        return 1

    print(f"launched agent_id={agent_id} run_id={run_id} role={role} number={number}")
    post_comment(owner_repo, number, launch_comment(marker, role, agent_id, run_id))
    apply_factory_status_for_launch(
        owner_repo,
        role,
        number=number,
        is_pr=is_pr,
        factory_issue_number=factory_issue_number,
    )
    return 0


def cloud_repo(repo_url: str, *, role: str, pr_url: str) -> CloudRepository:
    if role == "dev":
        return CloudRepository(url=repo_url, starting_ref=DEV_BRANCH)
    return CloudRepository(url=repo_url, pr_url=pr_url)


def launch_agent(
    *,
    api_key: str,
    role: str,
    number: int,
    repo: CloudRepository,
    prompt: str,
    metadata: dict[str, str],
    idempotency_key: str | None = None,
) -> tuple[str, str]:
    cloud_kwargs: dict = {
        "repos": [repo],
        "auto_create_pr": False,
    }

    try:
        return _send(
            api_key=api_key,
            role=role,
            number=number,
            prompt=prompt,
            cloud=CloudAgentOptions(**cloud_kwargs, metadata=metadata),
            idempotency_key=idempotency_key,
        )
    except CursorAgentError as err:
        message = str(err).lower()
        if "feature_unavailable" not in message and "metadata" not in message:
            raise
        print("metadata not available for this API key; launching without it")
        return _send(
            api_key=api_key,
            role=role,
            number=number,
            prompt=prompt,
            cloud=CloudAgentOptions(**cloud_kwargs),
            idempotency_key=idempotency_key,
        )


def _send(
    *,
    api_key: str,
    role: str,
    number: int,
    prompt: str,
    cloud: CloudAgentOptions,
    idempotency_key: str | None = None,
) -> tuple[str, str]:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    key = idempotency_key or f"factory-{role}-{repo}-{number}"
    with Agent.create(
        model=MODEL,
        api_key=api_key,
        name=f"factory-{role}-{number}",
        idempotency_key=key,
        cloud=cloud,
    ) as agent:
        run = agent.send(prompt)
        agent_id = agent.agent_id
        run_id = run.id
        print(f"agent.agent_id={agent_id} run.id={run_id}")
        return agent_id, run_id


def build_prompt(
    role: str,
    *,
    number: int,
    title: str,
    body: str,
    html_url: str,
    extra: str = "",
) -> str:
    instructions = (PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8").strip()
    kind = "Issue" if role == "dev" else "Pull request"
    extra_block = f"{extra}\n" if extra else ""
    return (
        f"{instructions}\n\n"
        f"## {kind}\n"
        f"- Number: #{number}\n"
        f"- Title: {title}\n"
        f"- URL: {html_url}\n"
        f"{extra_block}\n"
        f"{body.strip() or '(no description)'}\n"
    )


def add_issue_label(owner_repo: str, issue_number: int, label: str) -> None:
    result = subprocess.run(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            owner_repo,
            "--add-label",
            label,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        print(
            f"Could not add label {label!r} to issue #{issue_number}: {combined.strip()}",
            file=sys.stderr,
        )
        result.check_returncode()
    print(f"Added label {label!r} to issue #{issue_number}")


def remove_issue_label(owner_repo: str, issue_number: int, label: str) -> None:
    result = subprocess.run(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            owner_repo,
            "--remove-label",
            label,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        print(
            f"Could not remove label {label!r} from issue #{issue_number}: {combined.strip()}",
            file=sys.stderr,
        )
        result.check_returncode()
    print(f"Removed label {label!r} from issue #{issue_number}")


def queue_issue_for_factory(owner_repo: str, issue: dict) -> int:
    number = issue["number"]
    set_factory_status(owner_repo, number, FACTORY_QUEUED_LABEL)
    post_comment(
        owner_repo,
        number,
        "This issue is **queued** for the factory. Dev does not start automatically.\n\n"
        "Run **Start factory** from GitHub Actions "
        "(Actions → **Start factory** → **Run workflow**) to begin work on the oldest queued issue.",
    )
    print(f"Queued issue #{number} with label {FACTORY_QUEUED_LABEL!r}")
    return 0


def oldest_queued_issue(owner_repo: str) -> dict | None:
    issues = gh_json(
        [
            "issue",
            "list",
            "--repo",
            owner_repo,
            "--label",
            FACTORY_QUEUED_LABEL,
            "--state",
            "open",
            "--json",
            "number,title,body,url",
            "--limit",
            "500",
        ]
    )
    if not issues:
        return None
    return min(issues, key=lambda item: item["number"])


def start_oldest_factory_queued(owner_repo: str, repo_url: str) -> int:
    """Start Dev on the oldest open issue with `factory-queued` (Start factory)."""
    return handle_start_factory(owner_repo, repo_url)


def handle_start_factory(owner_repo: str, repo_url: str) -> int:
    issue = oldest_queued_issue(owner_repo)
    if issue is None:
        print("No issues in factory queue (no open issues with factory-queued label)")
        return 0

    ensure_branch(DEV_BRANCH, MAIN_BRANCH)
    number = issue["number"]
    if skip_dev_if_feature_pr_open(owner_repo, number):
        return 0

    html_url = issue.get("url") or f"{repo_url}/issues/{number}"
    rc = launch_role(
        owner_repo,
        repo_url,
        "dev",
        number=number,
        title=issue.get("title") or "",
        body=issue.get("body") or "",
        html_url=html_url,
        is_pr=False,
    )
    return rc


def open_feature_pr_into_dev(owner_repo: str) -> dict | None:
    prs = gh_json(
        [
            "pr",
            "list",
            "--repo",
            owner_repo,
            "--base",
            DEV_BRANCH,
            "--state",
            "open",
            "--json",
            "number,headRefName,url",
        ]
    )
    for pr in prs:
        head = pr.get("headRefName") or ""
        if head.startswith("feature/"):
            return pr
    return None


def skip_dev_if_feature_pr_open(owner_repo: str, issue_number: int) -> bool:
    blocking = open_feature_pr_into_dev(owner_repo)
    if blocking is None:
        return False
    url = blocking.get("url") or ""
    number = blocking.get("number")
    ref = blocking.get("headRefName") or "feature/…"
    detail = f"{url}" if url else f"PR #{number} (`{ref}`)"
    post_comment(
        owner_repo,
        issue_number,
        "Dev agent was not started because there is already an open feature PR into "
        f"`{DEV_BRANCH}`: {detail}\n\n"
        f"Merge that PR first, then add the `agent-dev` label on this issue to retry.",
    )
    print(f"Skipping dev launch for issue #{issue_number}; open feature PR {detail}")
    return True


def feature_branch_name(number: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())
    slug = slug.strip("-")[:50].strip("-") or "change"
    return f"feature/{number}-{slug}"


def ensure_branch(branch: str, source: str) -> None:
    remote = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        check=True,
        capture_output=True,
        text=True,
    )
    if remote.stdout.strip():
        print(f"Branch origin/{branch} already exists")
        return
    print(f"Creating origin/{branch} from origin/{source}")
    subprocess.run(["git", "fetch", "origin", source], check=True)
    subprocess.run(["git", "checkout", "-B", branch, f"origin/{source}"], check=True)
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)


def run_ci() -> tuple[bool, str]:
    result = subprocess.run(
        ["bash", str(CI_SCRIPT)],
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    return result.returncode == 0, output


def gh_json(args: list[str]):
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout or "null")


def issue_comment_bodies(owner_repo: str, number: int) -> str:
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{owner_repo}/issues/{number}/comments",
            "--jq",
            ".[].body",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout or ""


def comment_has_marker(owner_repo: str, number: int, marker: str) -> bool:
    return marker in issue_comment_bodies(owner_repo, number)


def count_marker_occurrences(owner_repo: str, number: int, marker: str) -> int:
    return issue_comment_bodies(owner_repo, number).count(marker)


def count_test_after_fix_launches(owner_repo: str, number: int) -> int:
    prefix = f"<!-- factory:test-after-fix:{number}:"
    return issue_comment_bodies(owner_repo, number).count(prefix)


def count_test_after_conflict_launches(owner_repo: str, number: int) -> int:
    prefix = f"<!-- factory:test-after-conflict:{number}:"
    return issue_comment_bodies(owner_repo, number).count(prefix)


def post_comment(owner_repo: str, number: int, body: str) -> None:
    subprocess.run(
        [
            "gh",
            "issue",
            "comment",
            str(number),
            "--repo",
            owner_repo,
            "--body",
            body,
        ],
        check=True,
    )


def launch_comment(marker: str, role: str, agent_id: str, run_id: str) -> str:
    return (
        f"{marker}\n"
        f"**{role}** agent launched.\n\n"
        f"- Agent: `{agent_id}`\n"
        f"- Run: `{run_id}`\n\n"
        "The cloud agent keeps working after this Action finishes. "
        "In Cursor, open Agents and filter Source → SDK."
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CursorAgentError as err:
        print(
            f"startup failed: {err.message}, retryable={err.is_retryable}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except subprocess.CalledProcessError as err:
        print(f"github cli failed: {err}", file=sys.stderr)
        raise SystemExit(1)
