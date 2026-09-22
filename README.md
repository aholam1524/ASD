# Agent factory

GitHub Issues are tickets. Labels launch Cursor cloud agents for **dev**, **test**, and **review**. Agents never merge.

## One-time setup

1. Create labels in this repository:

   ```bash
   gh label create agent-dev --color 1D76DB --description "Launch the Dev cloud agent"
   gh label create agent-test --color 0E8A16 --description "Launch the Test cloud agent"
   gh label create agent-review --color 5319E7 --description "Launch the Review cloud agent"
   ```

2. Add a repository secret named `CURSOR_API_KEY` from [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations).

3. Grant this repository to the Cursor GitHub app so cloud agents can clone it and open pull requests. The GitHub Action only starts the agent.

## How to use it

1. Open an issue with the **Agent ticket** template. Fill in **What should happen** and **Done when**.
2. Add the `agent-dev` label. The Action comments an agent id and a run id; the Dev agent keeps working in the cloud and should open a PR that closes the issue.
3. On that PR, add `agent-test`. The Test agent comments pass/fail. It does not push or merge.
4. Add `agent-review`. The Review agent comments findings. It does not push or merge.
5. You merge.

Humans apply labels. That keeps Test and Review from retriggering each other.

Watch SDK-launched agents in Cursor: Agents → Filter → Source → SDK.

## Smoke test

Open an issue titled **Add a Factory section to the README**, describe a small README change, and label it `agent-dev`. Confirm the Action comments an agent id, then label the resulting PR `agent-test` and `agent-review`.
