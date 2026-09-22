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
}
PASS_MARKER = "<!-- factory:test-result:pass -->"
FAIL_MARKER = "<!-- factory:test-result:fail -->"
PROMOTE_DEV_TO_TEST = "<!-- factory:promote:dev-to-test -->"
PROMOTE_TEST_TO_MAIN = "<!-- factory:promote:test-to-main -->"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in owner_repo:
        print("GITHUB_REPOSITORY is missing or invalid", file=sys.stderr)
        return 1

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo_url = f"{server}/{owner_repo}"
    owner = owner_repo.split("/", 1)[0]

    if args.role and args.pr:
        return launch_role_on_pr(owner_repo, repo_url, args.role, args.pr)

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH is not set", file=sys.stderr)
        return 1

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    action = event.get("action") or ""

    if event_name == "issues" and action == "opened":
        issue = event.get("issue") or {}
        if issue.get("pull_request"):
            print("Ignoring pull_request issue-opened event")
            return 0
        ensure_branch(DEV_BRANCH, MAIN_BRANCH)
        return launch_role(
            owner_repo,
            repo_url,
            "dev",
            number=issue["number"],
            title=issue.get("title") or "",
            body=issue.get("body") or "",
            html_url=issue.get("html_url") or f"{repo_url}/issues/{issue['number']}",
            is_pr=False,
        )

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
        if (pr.get("base", {}).get("ref") or "") != DEV_BRANCH:
            print("Merged PR is not into dev; ignoring")
            return 0
        return promote_dev_to_test(owner_repo, repo_url, owner, pr)

    if event_name == "issue_comment" and action == "created":
        return handle_issue_comment(owner_repo, repo_url, owner, event)

    print(f"Ignoring event {event_name}.{action}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent factory dispatcher")
    parser.add_argument("--role", choices=["dev", "test", "review"])
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
    if role in {"test", "review"} and not is_pr:
        post_comment(
            owner_repo,
            number,
            f"`agent-{role}` is only valid on pull requests. Ignoring on issue #{number}.",
        )
        return 0

    if role == "dev":
        ensure_branch(DEV_BRANCH, MAIN_BRANCH)

    html_url = target.get("html_url") or (
        f"{repo_url}/{'pull' if is_pr else 'issues'}/{number}"
    )
    return launch_role(
        owner_repo,
        repo_url,
        role,
        number=number,
        title=target.get("title") or "",
        body=target.get("body") or "",
        html_url=html_url,
        is_pr=is_pr,
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
    if PASS_MARKER not in body:
        print("Comment has no test PASS marker; ignoring")
        return 0

    number = issue["number"]
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

    subprocess.run(
        ["gh", "pr", "merge", str(number), "--repo", owner_repo, "--merge"],
        check=True,
    )
    post_comment(owner_repo, number, "CI passed. Merged into `test`.")
    return promote_test_to_main(owner_repo, repo_url, owner)


def promote_dev_to_test(owner_repo: str, repo_url: str, owner: str, merged_pr: dict) -> int:
    ensure_branch(DEV_BRANCH, MAIN_BRANCH)
    ensure_branch(TEST_BRANCH, MAIN_BRANCH)
    title = f"Promote {DEV_BRANCH} to {TEST_BRANCH}"
    body = (
        f"{PROMOTE_DEV_TO_TEST}\n"
        f"Promotion after merging #{merged_pr['number']} into `{DEV_BRANCH}`.\n"
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
    return launch_role_on_pr(owner_repo, repo_url, "test", pr_number)


def promote_test_to_main(owner_repo: str, repo_url: str, owner: str) -> int:
    ensure_branch(TEST_BRANCH, MAIN_BRANCH)
    title = f"Promote {TEST_BRANCH} to {MAIN_BRANCH}"
    body = (
        f"{PROMOTE_TEST_TO_MAIN}\n"
        f"Promotion after merging into `{TEST_BRANCH}`. Merge this PR yourself; no automatic merge to main.\n"
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
    return launch_role_on_pr(owner_repo, repo_url, "test", pr_number)


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

    listed = gh_json(
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
            "number",
        ]
    )
    if not listed:
        raise RuntimeError(f"Created {head} -> {base} PR but could not find it")
    print(f"Opened promotion PR #{listed[0]['number']} ({head} -> {base})")
    return listed[0]["number"]


def launch_role_on_pr(owner_repo: str, repo_url: str, role: str, pr_number: int) -> int:
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
    )


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
) -> int:
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        print("CURSOR_API_KEY is not set", file=sys.stderr)
        return 1

    marker = f"<!-- factory:{role}:{number} -->"
    if comment_has_marker(owner_repo, number, marker):
        print(f"Already launched {role} for #{number}; skipping")
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
        )
    except CursorAgentError as err:
        print(
            f"startup failed: {err.message}, retryable={err.is_retryable}",
            file=sys.stderr,
        )
        return 1

    print(f"launched agent_id={agent_id} run_id={run_id} role={role} number={number}")
    post_comment(owner_repo, number, launch_comment(marker, role, agent_id, run_id))
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
        )


def _send(
    *,
    api_key: str,
    role: str,
    number: int,
    prompt: str,
    cloud: CloudAgentOptions,
) -> tuple[str, str]:
    with Agent.create(
        model=MODEL,
        api_key=api_key,
        name=f"factory-{role}-{number}",
        idempotency_key=f"factory-{role}-{os.environ.get('GITHUB_REPOSITORY', '')}-{number}",
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


def comment_has_marker(owner_repo: str, number: int, marker: str) -> bool:
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
    return marker in result.stdout


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
