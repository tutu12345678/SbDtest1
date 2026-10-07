# Secure by Design Assessment Automation

Automatically creates a linked "Secure by Design Assessment" work item in
Azure DevOps for every User Story in a given iteration, so nobody has to do
it by hand.

## Built-in safety behavior

1. **Dry run by default.** `DRY_RUN` defaults to `true`. In dry-run mode the
   script does a real read-only scan of Azure DevOps and logs exactly what
   it *would* create (story IDs, assignee, would-create vs. would-skip) —
   but makes zero write calls. Nothing gets created in ADO until you
   explicitly set `DRY_RUN=false`.
2. **Scoped by iteration, not a rolling time window.** There's no date-based
   filtering at all. The script only acts on User Stories under
   `ADO_ITERATION_PATH` (and, if set, `ADO_AREA_PATH`) — the same two fields
   you'd fill in on a manual ADO query. This is required, not optional: the
   script refuses to run without it, so it can never accidentally scan the
   whole project's history.

## How it works

1. A GitHub Actions workflow runs on a schedule (not started by default).
2. `create_assessment_tickets.py` queries Azure DevOps for every User Story
   under the configured Iteration Path (and Area Path, if set).
3. For each story, it checks the story's **Related** links for its own
   `Secure by Design Assessment`. A match is found only when the related work
   item is a `Secure by Design Assessment` and its title exactly matches
   `SbD_<Story Title>`. Assessments belonging to other User Stories are ignored.
4. Stories in **New** or **Removed** state are skipped and no SbD ticket is
   created for them. For all other states, if no matching SbD ticket exists
   and `DRY_RUN` is false, the script creates one titled
   `SbD_<Story Title>`.

   The new ticket:
   - is assigned to `Dhruv.Jain@nestle.com`
   - is tagged `SbD`
   - uses the configured `ADO_AREA_PATH`
   - uses the configured `ADO_ITERATION_PATH`
   - is linked back to the User Story with a **Related** link
   - contains the standard Secure by Design tables in the Description field

   If `DRY_RUN` is true, it only logs what it would create.
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
   (project, area path, iteration path, dry-run status) followed by one line
   per story: either "already has an assessment ticket, skipping" or
   "[DRY-RUN] would create assessment ticket for story &lt;id&gt;".
4. Cross-check a few of those story IDs in Azure DevOps yourself to confirm
   they're in the right project/POD/iteration and really do need an
   assessment ticket.
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

| Secret name          | Example value                                          |
|-----------------------|---------------------------------------------------------|
| `ADO_ORG_URL`         | `https://dev.azure.com/nestle-it`                       |
| `ADO_PROJECT`         | `ADI`                                                    |
| `ADO_PAT`             | *(the token from step 1 — paste once, never logged)*    |
| `ADO_ITERATION_PATH`  | `ADI\CDQ-iPAL_PowerApps\2026\Y26-Oct` *(required — this defines "this month")* |
| `ADO_AREA_PATH`       | `ADI\CDQ-iPAL_PowerApps` *(optional — narrows further)* |

`ADO_AREA_PATH` and `ADO_ITERATION_PATH` mirror the Area and Iteration
fields you'd fill in on a manual ADO query (see your board's work item
details pane for the exact values). **`ADO_ITERATION_PATH` is required** —
the script fails fast with a clear error if it's missing, the same way it
already does for a missing PAT.

Each new month, update the `ADO_ITERATION_PATH` secret's value to that
month's iteration (e.g. `...\2026\Y26-Nov` once November starts) — that one
change is what moves the automation on to the next month's stories.

### 3. Confirm the schedule

The workflow runs hourly (`0 * * * *`). To switch to once a day, at 9:00 am IST edit the
`cron` line —"30 3 * * 1-5", (1-5 means monday to driday) 

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
  POD (e.g. `automate_tickets_pod2.yml`), each with its own
  `ADO_AREA_PATH`/`ADO_ITERATION_PATH` and, if needed, its own PAT secret
  with a different name. No code changes.
- **Each POD forks/uses this repo**: since nothing here is POD-specific
  beyond the secrets, another team can plug into this exact same script with
  only their own secrets configured.

## Config reference (environment variables)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ADO_ORG_URL` | Yes | — | e.g. `https://dev.azure.com/nestle-it` |
| `ADO_PROJECT` | Yes | — | e.g. `ADI` |
| `ADO_PAT` | Yes | — | PAT with Work Items Read & Write scope |
| `ADO_ITERATION_PATH` | Yes | — | Iteration Path defining the scan scope, e.g. this month's sprint |
| `DRY_RUN` | No | `true` | Set to `false` to let the script actually create tickets |
| `DRY_RUN_SHOW_TITLES` | No | `false` | Include would-be titles in dry-run logs for manual review |
| `ADO_AREA_PATH` | No | *(whole project)* | Narrow the scan to one Area Path within the iteration |
| `ASSESSMENT_WORK_ITEM_TYPE` | No | `Secure by Design Assessment` | Custom work item type name |
| `ASSESSMENT_TAG` | No | `SbD` | Tag applied to the new ticket |
| `ASSESSMENT_TITLE_PREFIX` | No | `SbD_` | Prefix for the assessment title |
| `SBD_ASSIGNEE` | No | `Dhruv.Jain@nestle.com` | Assignee for all newly created SbD tickets |
