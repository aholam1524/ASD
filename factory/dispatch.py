#!/usr/bin/env python3
"""Launch a Cursor cloud agent from a GitHub Issues/PR label event.

The GitHub Action should exit after send(); the cloud VM keeps working.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cursor_sdk import Agent, CloudAgentOptions, CloudRepository, CursorAgentError

FACTORY_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = FACTORY_ROOT / "prompts"
MODEL = "composer-2.5"
ROLES = {
    "agent-dev": "dev",
    "agent-test": "test",
    "agent-review": "review",
}
ISSUE_ROLES = {"dev"}
PR_ROLES = {"test", "review"}


def main() -> int:
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        print("CURSOR_API_KEY is not set", file=sys.stderr)
        return 1

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH is not set", file=sys.stderr)
        return 1

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    label = (event.get("label") or {}).get("name", "")
    role = ROLES.get(label)
    if role is None:
        print(f"Ignoring unlabeled factory event: {label!r}")
        return 0

    owner_repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in owner_repo:
        print("GITHUB_REPOSITORY is missing or invalid", file=sys.stderr)
        return 1

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo_url = f"{server}/{owner_repo}"
    default_branch = (event.get("repository") or {}).get("default_branch") or "main"

    issue = event.get("issue")
    pull_request = event.get("pull_request")
    is_pr = pull_request is not None or bool(issue and issue.get("pull_request"))
    target = pull_request or issue
    if target is None:
        print("Event has no issue or pull_request", file=sys.stderr)
        return 1

    number = target["number"]
    title = target.get("title") or ""
    body = target.get("body") or ""
    html_url = target.get("html_url") or f"{repo_url}/{'pull' if is_pr else 'issues'}/{number}"
    marker = f"<!-- factory:{role}:{number} -->"

    if role in ISSUE_ROLES and is_pr:
        post_comment(
            owner_repo,
            number,
            f"`agent-dev` is only valid on issues. Ignoring on PR #{number}.",
        )
        return 0
    if role in PR_ROLES and not is_pr:
        post_comment(
            owner_repo,
            number,
            f"`agent-{role}` is only valid on pull requests. Ignoring on issue #{number}.",
        )
        return 0

    if comment_has_marker(owner_repo, number, marker):
        print(f"Already launched {role} for #{number}; skipping")
        return 0

    prompt = build_prompt(role, number=number, title=title, body=body, html_url=html_url)
    repo = cloud_repo(repo_url, role=role, default_branch=default_branch, pr_url=html_url)
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


def cloud_repo(repo_url: str, *, role: str, default_branch: str, pr_url: str) -> CloudRepository:
    if role == "dev":
        return CloudRepository(url=repo_url, starting_ref=default_branch)
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
    auto_create_pr = role == "dev"
    cloud_kwargs: dict = {
        "repos": [repo],
        "auto_create_pr": auto_create_pr,
    }
    if auto_create_pr:
        cloud_kwargs["skip_reviewer_request"] = True

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


def build_prompt(role: str, *, number: int, title: str, body: str, html_url: str) -> str:
    instructions = (PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8").strip()
    kind = "Issue" if role == "dev" else "Pull request"
    return (
        f"{instructions}\n\n"
        f"## {kind}\n"
        f"- Number: #{number}\n"
        f"- Title: {title}\n"
        f"- URL: {html_url}\n\n"
        f"{body.strip() or '(no description)'}\n"
    )


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
