# Secure by Design Assessment Automation

Automatically creates a linked "Secure by Design Assessment" work item in
Azure DevOps for every new User Story, so nobody has to do it by hand.

## How it works

1. A GitHub Actions workflow runs on a schedule (hourly by default).
2. `create_assessment_tickets.py` queries Azure DevOps for User Stories
   created in the last ~26 hours (a bit over the run interval, so a missed
   or delayed run can't silently skip a story).
3. For each story, it checks the story's **Related** links for an existing
   `Secure by Design Assessment` work item. If one exists, it's skipped.
4. If none exists, it creates one titled `SbD_<Story Title>`, tags it `SbD`,
   copies the assignee from the story (unless `DEFAULT_ASSIGNEE` is set),
   and links it back to the story with a **Related** link.
5. Only work item IDs and HTTP status codes are logged — never story titles
   or descriptions.

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

To find your org URL and project name: open the relevant board in Azure
DevOps in your browser — the address bar shows
`https://dev.azure.com/{org}/{project}/_boards/...`.

### 3. (Optional) Scope to one Area Path / POD

If a repo/workflow should only handle one POD, set the `ADO_AREA_PATH`
environment variable in the workflow file (see the commented-out line in
`automate_tickets.yml`) to something like `ADI\CDQ-iPAL_PowerApps`. Leave it
unset to scan the whole project.

### 4. Confirm the schedule

The workflow runs hourly (`0 * * * *`). To switch to once a day, edit the
`cron` line in `.github/workflows/automate_tickets.yml` — e.g. `0 6 * * *`
runs once daily at 06:00 UTC.

## Scaling to other PODs

Two options, both supported by the same script:

- **Shared repo, multiple workflows**: duplicate `automate_tickets.yml` per
  POD (e.g. `automate_tickets_pod2.yml`), each with its own `ADO_AREA_PATH`
  and, if needed, its own PAT secret with a different name. No code changes.
- **Each POD forks/uses this repo**: since nothing here is POD-specific
  beyond the secrets and optional area path, another team can plug into this
  exact same script with only their own secrets configured.

## Doing a dry run first

Before turning on the schedule, run it once manually with
`workflow_dispatch` (the "Run workflow" button on the Actions tab) against a
project/Area Path with only a couple of test User Stories, and check the
Actions log output — you'll see ticket IDs and status codes, and you can
verify in Azure DevOps that the created ticket looks right before it runs
unattended across all 50-60 stories a month.

## Config reference (environment variables)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ADO_ORG_URL` | Yes | — | e.g. `https://dev.azure.com/nestle-it` |
| `ADO_PROJECT` | Yes | — | e.g. `ADI` |
| `ADO_PAT` | Yes | — | PAT with Work Items Read & Write scope |
| `ADO_AREA_PATH` | No | *(whole project)* | Restrict scan to one Area Path |
| `LOOKBACK_HOURS` | No | `26` | How far back to scan for new stories |
| `ASSESSMENT_WORK_ITEM_TYPE` | No | `Secure by Design Assessment` | Custom work item type name |
| `ASSESSMENT_TAG` | No | `SbD` | Tag applied to the new ticket |
| `ASSESSMENT_TITLE_PREFIX` | No | `SbD_` | Prefix for the assessment title |
| `DEFAULT_ASSIGNEE` | No | *(copies story's assignee)* | Fixed assignee/queue override |
