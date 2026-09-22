# Agent factory

Describe the work in Cursor chat in this repo. The agent files a GitHub issue, which is **queued** until you run **Start factory**. Code moves `feature/N-slug` → `dev` → `test` → `main`. You merge into `dev` and `main`. Merge into `test` is automatic when the Test agent reports PASS and CI is green. Press **Start factory** once; after you merge each ticket to `main`, the next queued issue starts by itself.

When Dev pushes `feature/*`, the factory opens a PR **into `dev`**, adds `agent-review`, and starts Review (even if the Dev agent forgot to open the PR).

```text
You describe work in Cursor
    → Agent files a GitHub issue (factory-queued)
    → You run Start factory (or merge prior ticket to main to drain the queue)
    → Dev agent: branch feature/<issue>-<slug>
    → Push opens PR into dev, labels agent-review, starts Review
    → You merge into dev
    → Factory opens PR dev → test and starts Test
    → Test PASS + CI green → automatic merge into test
    → Factory opens PR test → main and starts Test
    → You merge into main
```

## One-time setup

1. Repo secret `CURSOR_API_KEY` from [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations).

2. Grant this repository to the Cursor GitHub app (clone + open PRs).

3. Add repo secret **`FACTORY_GITHUB_TOKEN`**: a GitHub classic PAT (scope `repo`) or a fine-grained token with Contents, Issues, and Pull requests read/write, created as **your user**. The factory uses it to open PRs as you. PRs opened as `github-actions[bot]` sit on **Approve workflows** and follow-up jobs (including `test` → `main`) may never start. Without this secret those waits come back.

4. Create long-lived branches if they do not exist (the factory will also create them from `main` on first promote):

   ```bash
   git fetch origin
   git checkout main
   git pull
   git checkout -b dev && git push -u origin dev
   git checkout main
   git checkout -b test && git push -u origin test
   ```

5. Retry labels (create once; ignore “already exists”):

   ```bash
   gh label create agent-dev --color 1D76DB --description "Retry the Dev cloud agent"
   gh label create agent-test --color 0E8A16 --description "Retry the Test cloud agent"
   gh label create agent-review --color 5319E7 --description "Retry the Review cloud agent"
   gh label create agent-fix --color D93F0B --description "Retry the Fixer cloud agent"
   gh label create agent-conflict --color FBCA04 --description "Retry the Conflict cloud agent"
   ```

If `test` is branch-protected, allow GitHub Actions to merge or auto-merge into `test` will fail.

In the repo: Settings → Actions → General → Workflow permissions → **Read and write**. Otherwise the factory cannot create `dev`/`test` or open promotion PRs.

You do not approve workflow runs. You only merge PRs into `dev` after Review, and into `main` after Test.

## How to use it

### How to start

Actions → [**Start factory**](https://github.com/aholam1524/ASD/actions/workflows/start-factory.yml) → **Run workflow**. That begins Dev on the oldest open issue with the `factory-queued` label.

1. In Cursor, say what you want built (for example: "add a clamp helper in clamp/"). The agent creates the issue; the factory adds `factory-queued` and waits for **Start factory** (or the next drain after a merge to `main`).
2. Wait for a PR from `feature/<number>-<slug>` **into `dev`** (opened on push if Dev only pushed a branch). Review starts automatically.
3. You merge that PR into `dev`.
4. The factory opens `dev` → `test`, runs Test, then CI. On PASS + green CI it merges into `test` and opens `test` → `main`.
5. Read Test comments on the main PR. You merge into `main`.

Happy path needs no labels. To retry a failed launch: `agent-dev` on an **issue**; `agent-test`, `agent-review`, `agent-fix`, or `agent-conflict` on a **PR**.

**Fixer:** Test FAIL on a feature PR into `dev` starts Fixer once; after Fixer pushes, Test runs again. **Conflict:** auto-merge into `test` failing on merge conflicts (on the `dev` → `test` PR) starts Conflict; after resolve, Test runs again then auto-merge retries. **One feature PR:** only one open `feature/*` → `dev` PR at a time—a second issue gets a comment and Dev is skipped until you merge the blocking PR or add `agent-dev`. You merge into `dev` and `main` yourself; `main` is never auto-merged.

Watch SDK-launched agents in Cursor: Agents → Filter → Source → SDK.

## Smoke test

In Cursor, ask to file an issue for a one-line README Status note. Confirm the issue opens, Dev launches, a PR appears into `dev` after the feature branch is pushed, and Review is labeled. Merge into `dev`, then merge `test` → `main` yourself.
