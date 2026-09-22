# Agent factory

Opening a GitHub Issue starts the Dev agent. Code moves `feature/N-slug` → `dev` → `test` → `main`. You merge into `dev` and `main`. Merge into `test` is automatic when the Test agent reports PASS and CI is green.

```text
Issue opened
    → Dev agent: branch feature/<issue>-<slug>, PR into dev
    → Review agent (automatic)
    → You merge into dev
    → Factory opens PR dev → test and starts Test
    → Test PASS + CI green → automatic merge into test
    → Factory opens PR test → main and starts Test
    → You merge into main
```

## One-time setup

1. Repo secret `CURSOR_API_KEY` from [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations).

2. Grant this repository to the Cursor GitHub app (clone + open PRs).

3. Create long-lived branches if they do not exist (the factory will also create them from `main` on first promote):

   ```bash
   git fetch origin
   git checkout main
   git pull
   git checkout -b dev && git push -u origin dev
   git checkout main
   git checkout -b test && git push -u origin test
   ```

4. Optional retry labels:

   ```bash
   gh label create agent-dev --color 1D76DB --description "Retry the Dev cloud agent"
   gh label create agent-test --color 0E8A16 --description "Retry the Test cloud agent"
   gh label create agent-review --color 5319E7 --description "Retry the Review cloud agent"
   ```

If `test` is branch-protected, allow GitHub Actions to merge or auto-merge into `test` will fail.

In the repo: Settings → Actions → General → Workflow permissions → **Read and write**. Otherwise the factory cannot create `dev`/`test` or open promotion PRs.

## How to use it

1. Open an issue. The **Agent factory** workflow starts Dev. No label required.
2. Wait for a PR from `feature/<number>-<slug>` **into `dev`**. Review comments appear automatically.
3. You merge that PR into `dev`.
4. The factory opens `dev` → `test`, runs Test, then CI. On PASS + green CI it merges into `test` and opens `test` → `main`.
5. Read Test comments on the main PR. You merge into `main`.

Manual labels (`agent-dev` on an issue, `agent-test` / `agent-review` on a PR) retry a launch if one failed.

Watch SDK-launched agents in Cursor: Agents → Filter → Source → SDK.

## Smoke test

Open an issue titled **Add a one-line Status note to the README**. Confirm the Action comments a Dev agent id, then a PR into `dev`. Merge it. Confirm a `dev` → `test` PR, then a `test` → `main` PR that you merge yourself.
