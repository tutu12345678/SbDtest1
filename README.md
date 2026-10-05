# Secure by Design Assessment Automation

Automatically creates a linked "Secure by Design Assessment" work item in
Azure DevOps for every new User Story, so nobody has to do it by hand.

## Built-in safety behavior

Two things keep this from making a mess on the client board before you're
ready:

1. **Dry run by default.** `DRY_RUN` defaults to `true`. In dry-run mode the
   script does a real read-only scan of Azure DevOps and logs exactly what
   it *would* create (story IDs, assignee, would-create vs. would-skip) —
   but makes zero write calls. Nothing gets created in ADO until you
   explicitly set `DRY_RUN=false`.
2. **Current-month floor.** The scan window never reaches earlier than the
   first moment of the current calendar month (UTC), no matter what
   `LOOKBACK_HOURS` is set to. This is a hard safety floor, not just a
   lookback setting — a misconfigured lookback value can't accidentally pull
   in stories from previous months.

## How it works

1. A GitHub Actions workflow runs on a schedule (hourly by default).
2. `create_assessment_tickets.py` queries Azure DevOps for User Stories
   created since `max(start of current month, now - LOOKBACK_HOURS)`.
3. For each story, it checks the story's **Related** links for an existing
   `Secure by Design Assessment` work item. If one exists, it's skipped.
4. If none exists **and** `DRY_RUN` is false, it creates one titled
   `SbD_<Story Title>`, tags it `SbD`, copies the assignee from the story
   (unless `DEFAULT_ASSIGNEE` is set), and links it back to the story with a
   **Related** link. If `DRY_RUN` is true, it just logs the decision.
5. Only work item IDs and HTTP status codes are logged — never story titles
   or descriptions, unless you explicitly opt into
   `DRY_RUN_SHOW_TITLES=true` for manual review during a dry run.

## Doing the dry run (do this first)

1. Push the code to the repo with the workflow's `DRY_RUN` left at its
   default (`true`).
2. Go to the repo's **Actions** tab → the workflow → **Run workflow**
   (this is the `workflow_dispatch` trigger — it has a `dry_run` checkbox,
   ticked by default).
3. Open the completed run's log. You'll see a scope summary line
   (project, area path, date range, dry-run status) followed by one line per
   story: either "already has an assessment ticket, skipping" or
   "[DRY-RUN] would create assessment ticket for story &lt;id&gt;".
4. Cross-check a few of those story IDs in Azure DevOps yourself to confirm
   they're in the right project/POD and really do need an assessment ticket.
5. If you want to see the *titles* the script would use (to sanity-check the
   `SbD_` naming) without creating anything, re-run manually with
   `DRY_RUN_SHOW_TITLES=true` set as an extra env var on that one run.
6. Only once you're confident in the output: edit the workflow's `DRY_RUN`
   line (see below) to `false`, or untick the dry-run box on a manual run,
   so the **next** scheduled run actually creates tickets.

**Important:** the scheduled (cron) trigger always falls back to dry-run
`true` unless you edit the `DRY_RUN:` line in `automate_tickets.yml`
directly — the workflow_dispatch checkbox only affects manual runs. So the
hourly schedule stays a safe no-op until you deliberately flip that line.

## One-time setup

### 1. Create the Personal Access Token (PAT)

In Azure DevOps: **User settings → Personal access tokens → New Token**

- Scope: **Work Items → Read & Write** only (don't grant more than that)
- Organization: the org this automation will run against
- Expiration: set a reminder to rotate it before it expires — an expired PAT
  will make the workflow start failing with 401s

### 2. Add GitHub repo secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name    | Example value                          |
|----------------|-----------------------------------------|
| `ADO_ORG_URL`  | `https://dev.azure.com/nestle-it`       |
| `ADO_PROJECT`  | `ADI`                                   |
| `ADO_PAT`      | *(the token from step 1 — paste once, never logged)* |
| `ADO_AREA_PATH`| `ADI\CDQ-iPAL_PowerApps` *(dry-run scope)* |

### 3. Confirm the schedule

The workflow runs hourly (`0 * * * *`). To switch to once a day, edit the
`cron` line — e.g. `0 6 * * *` runs once daily at 06:00 UTC.

## Turning it on for real

Once the dry run looks correct:

1. Open `.github/workflows/automate_tickets.yml`.
2. Change the line `DRY_RUN: ${{ inputs.dry_run != false }}` to
   `DRY_RUN: "false"`.
3. Commit. The next scheduled run will create real tickets.

Keep the `ADO_AREA_PATH` secret scoped to one POD for as long as you want
this limited to a single team, and widen or remove it when you're ready to
cover the whole project.

## Scaling to other PODs

Two options, both supported by the same script:

- **Shared repo, multiple workflows**: duplicate `automate_tickets.yml` per
  POD (e.g. `automate_tickets_pod2.yml`), each with its own `ADO_AREA_PATH`
  and, if needed, its own PAT secret with a different name. No code changes.
- **Each POD forks/uses this repo**: since nothing here is POD-specific
  beyond the secrets and optional area path, another team can plug into this
  exact same script with only their own secrets configured.

## Config reference (environment variables)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ADO_ORG_URL` | Yes | — | e.g. `https://dev.azure.com/nestle-it` |
| `ADO_PROJECT` | Yes | — | e.g. `ADI` |
| `ADO_PAT` | Yes | — | PAT with Work Items Read & Write scope |
| `DRY_RUN` | No | `true` | Set to `false` to let the script actually create tickets |
| `DRY_RUN_SHOW_TITLES` | No | `false` | Include would-be titles in dry-run logs for manual review |
| `ADO_AREA_PATH` | No | *(whole project)* | Restrict scan to one Area Path |
| `LOOKBACK_HOURS` | No | `26` | How far back to scan; never earlier than start of current month |
| `ASSESSMENT_WORK_ITEM_TYPE` | No | `Secure by Design Assessment` | Custom work item type name |
| `ASSESSMENT_TAG` | No | `SbD` | Tag applied to the new ticket |
| `ASSESSMENT_TITLE_PREFIX` | No | `SbD_` | Prefix for the assessment title |
| `DEFAULT_ASSIGNEE` | No | *(copies story's assignee)* | Fixed assignee/queue override |
