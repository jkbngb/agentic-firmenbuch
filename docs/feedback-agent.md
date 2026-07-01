# Autonomous user-feedback agent

How user feedback becomes a fix, with the owner in control. Rules the agent follows:
[`AGENT_PLAYBOOK.md`](../AGENT_PLAYBOOK.md).

## Flow

```
User (non-technical) → Feedback form on the site (feedback.html, header + footer link)
     │  message + optional screenshot (drag-drop / click / paste), Turnstile-gated
     ▼
POST /api/feedback  (api/asgi.py)
     │  screenshot → Blob (90-raw/feedback/, 7-day signed URL), then:
     ▼
GitHub issue, label `user-feedback`   ← created via GH_FEEDBACK_TOKEN
     ▼
.github/workflows/feedback-agent.yml  (fires on issue opened/labeled with that label)
     │  runs anthropics/claude-code-action (model: claude-opus-4-8), follows AGENT_PLAYBOOK.md
     ▼
   ┌─ SMALL → branch + PR (`Closes #n`) + CI gate           → owner reviews, merges, DEPLOYS
   ├─ NOT ACTIONABLE (spam/dup/unclear) → comment + close    (agent)
   └─ BIG (schema/infra/auth/cost) → plan + `needs-owner-approval` + @-mention owner → STOP
```

## FAQ

- **Does every submission create an issue?** Yes — every valid (Turnstile-passed, non-empty)
  submission creates one issue. There is no "does it make sense" pre-filter; the **agent** decides
  after: it fixes real items and **closes** spam/duplicates/out-of-scope with a reason.
- **Are issues closed when fixed?** Yes, automatically — the fix PR says `Closes #n`, so **merging
  it closes the issue**, with the PR as the justification. Not-actionable issues the agent closes
  itself with a comment. Big items stay open for the owner.
- **One feedback with several requests?** The form creates one intake issue; the **agent splits
  it** — a separate PR per independent small item, and a separate tracking issue per big item
  (linked back), then closes the intake issue once everything is dispatched (see the playbook).
- **Where are the prompts?** The **system prompt** is [`AGENT_PLAYBOOK.md`](../AGENT_PLAYBOOK.md)
  (persistent rules); the **user prompt** is the `prompt:` field in `feedback-agent.yml` (the
  per-issue task, injecting the issue title + body).
- **Which model?** `claude-opus-4-8` (best code-change quality). Cost is bounded by the spend
  limit on the API key.
- **Can the agent break production?** No. It only opens PRs — it **cannot push to `main`, merge, or
  deploy** (branch protection + least-privilege token). Deploy is always a manual owner step, so
  even a merged PR does not change production until the owner deploys.
- **Security (public repo):** the workflow runs only for issues carrying the `user-feedback` label;
  random visitors can't apply labels, and only the feedback form's token or a maintainer adds it.
  Issue text is treated as untrusted (anti-prompt-injection). No secrets live in the repo.

## Human-in-the-loop notifications (Slack instead of e-mail)

Default notifications are GitHub's (e-mail / GitHub inbox). For **Slack**, install the GitHub Slack
app and subscribe a channel — no custom code:

```
/github subscribe jkbngb/agentic-firmenbuch issues pulls
```

You'll get new feedback issues, the agent's PRs, and its @-mentions (big-change escalations) in
Slack. Optional: a workflow step can post a tailored Slack message on escalation — ask if wanted.

## Owner setup

| step | done by | status |
|------|---------|--------|
| Install the Claude GitHub App on the repo | owner | ✅ |
| Repo secret `ANTHROPIC_API_KEY` (+ spend limit on the key) | owner | ✅ |
| Branch protection on `main` (require PR; admins bypass) | — | ✅ |
| Fine-grained PAT (Issues: write) as `GH_FEEDBACK_TOKEN` on `app-firmenbuch-signup` | owner | ⏳ **needed** |

Until `GH_FEEDBACK_TOKEN` is set, `POST /api/feedback` returns `503` and no issue is created:

```bash
az containerapp secret set -n app-firmenbuch-signup -g rg-firmenbuch-prod \
  --secrets gh-feedback-token=<PAT>
az containerapp update -n app-firmenbuch-signup -g rg-firmenbuch-prod \
  --set-env-vars GH_FEEDBACK_TOKEN=secretref:gh-feedback-token
```
