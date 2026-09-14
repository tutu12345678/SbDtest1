#!/usr/bin/env python3
"""
create_assessment_tickets.py

Queries Azure DevOps (ADO) for User Stories and ensures every one of them has
a linked "Secure by Design Assessment" work item. Creates the assessment
ticket if it doesn't already exist, and links it back to the parent story
with a "Related" link.

Security notes:
  - The PAT is read from an environment variable only (GitHub Secret). It is
    never printed, logged, or written to disk.
  - Logging is restricted to work item IDs and HTTP status codes. Story
    titles/descriptions are never written to logs.
  - All requests go over HTTPS to dev.azure.com (TLS 1.2+ is enforced by
    Azure DevOps server-side).

Required environment variables:
  ADO_ORG_URL      e.g. https://dev.azure.com/nestle-it
  ADO_PROJECT      e.g. ADI
  ADO_PAT          Personal Access Token with "Work Items (Read & Write)" scope

Optional environment variables (safe defaults shown):
  ADO_AREA_PATH           Restrict scan to a specific Area Path (e.g. "ADI\\CDQ-iPAL_PowerApps").
                          Leave unset to scan the whole project.
  LOOKBACK_HOURS          How far back to scan for new User Stories. Default: 26
                          (slightly over 24h/daily-run to cover any workflow delay/skip).
  ASSESSMENT_WORK_ITEM_TYPE  Default: "Secure by Design Assessment"
  ASSESSMENT_TAG          Default: "SbD"
  ASSESSMENT_TITLE_PREFIX Default: "SbD_"
  DEFAULT_ASSIGNEE        If set, overrides "copy the story's assignee" behavior.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from requests.auth import HTTPBasicAuth

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADO_ORG_URL = os.environ.get("ADO_ORG_URL", "").rstrip("/")
ADO_PROJECT = os.environ.get("ADO_PROJECT", "")
ADO_PAT = os.environ.get("ADO_PAT", "")
ADO_AREA_PATH = os.environ.get("ADO_AREA_PATH", "").strip()

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "26"))
ASSESSMENT_WORK_ITEM_TYPE = os.environ.get(
    "ASSESSMENT_WORK_ITEM_TYPE", "Secure by Design Assessment"
)
ASSESSMENT_TAG = os.environ.get("ASSESSMENT_TAG", "SbD")
ASSESSMENT_TITLE_PREFIX = os.environ.get("ASSESSMENT_TITLE_PREFIX", "SbD_")
DEFAULT_ASSIGNEE = os.environ.get("DEFAULT_ASSIGNEE", "").strip()

API_VERSION = "7.1"
REQUEST_TIMEOUT_SECS = 30

# ---------------------------------------------------------------------------
# Logging — IDs and status codes only, never story content
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sbd-automation")


def fail_fast_on_missing_config():
    missing = [
        name
        for name, val in (
            ("ADO_ORG_URL", ADO_ORG_URL),
            ("ADO_PROJECT", ADO_PROJECT),
            ("ADO_PAT", ADO_PAT),
        )
        if not val
    ]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)


def ado_session() -> requests.Session:
    session = requests.Session()
    # ADO REST API uses Basic auth with an empty username and the PAT as password.
    session.auth = HTTPBasicAuth("", ADO_PAT)
    session.headers.update({"Content-Type": "application/json"})
    return session


# ---------------------------------------------------------------------------
# Step 1: find recently created User Stories
# ---------------------------------------------------------------------------

def get_recent_user_stories(session: requests.Session) -> list[int]:
    """Returns a list of work item IDs for User Stories created in the lookback window."""
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    area_clause = ""
    if ADO_AREA_PATH:
        # WIQL uses UNDER for area path scoping so sub-areas are included too.
        area_clause = f"AND [System.AreaPath] UNDER '{ADO_AREA_PATH}'\n"

    wiql = {
        "query": f"""
            SELECT [System.Id]
            FROM WorkItems
            WHERE [System.TeamProject] = '{ADO_PROJECT}'
            AND [System.WorkItemType] = 'User Story'
            AND [System.CreatedDate] >= '{since}'
            {area_clause}
            ORDER BY [System.CreatedDate] ASC
        """
    }

    url = f"{ADO_ORG_URL}/{ADO_PROJECT}/_apis/wit/wiql?api-version={API_VERSION}"
    resp = session.post(url, json=wiql, timeout=REQUEST_TIMEOUT_SECS)
    log.info("WIQL query for recent User Stories -> status %s", resp.status_code)
    resp.raise_for_status()

    work_items = resp.json().get("workItems", [])
    ids = [wi["id"] for wi in work_items]
    log.info("Found %d User Story candidate(s) in lookback window", len(ids))
    return ids


# ---------------------------------------------------------------------------
# Step 2: check whether a story already has a linked SbD assessment
# ---------------------------------------------------------------------------

def get_work_item_with_relations(session: requests.Session, story_id: int) -> dict:
    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}/_apis/wit/workitems/{story_id}"
        f"?$expand=relations&api-version={API_VERSION}"
    )
    resp = session.get(url, timeout=REQUEST_TIMEOUT_SECS)
    log.info("Fetch story %s with relations -> status %s", story_id, resp.status_code)
    resp.raise_for_status()
    return resp.json()


def has_existing_assessment(session: requests.Session, story: dict) -> bool:
    """Follows 'Related' links off the story and checks if any linked item is
    already an assessment ticket (by work item type). Avoids relying on title
    matching alone, since titles could be edited later."""
    relations = story.get("relations", [])
    related_ids = []
    for rel in relations:
        # System.LinkTypes.Related is the relation name for "Related" links.
        if rel.get("rel") == "System.LinkTypes.Related":
            url = rel.get("url", "")
            if "/workItems/" in url:
                try:
                    related_ids.append(int(url.rsplit("/", 1)[-1]))
                except ValueError:
                    continue

    if not related_ids:
        return False

    ids_param = ",".join(str(i) for i in related_ids)
    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}/_apis/wit/workitems"
        f"?ids={ids_param}&fields=System.WorkItemType&api-version={API_VERSION}"
    )
    resp = session.get(url, timeout=REQUEST_TIMEOUT_SECS)
    log.info(
        "Check related items %s for story %s -> status %s",
        ids_param,
        story.get("id"),
        resp.status_code,
    )
    resp.raise_for_status()

    for item in resp.json().get("value", []):
        if item.get("fields", {}).get("System.WorkItemType") == ASSESSMENT_WORK_ITEM_TYPE:
            return True
    return False


# ---------------------------------------------------------------------------
# Step 3: create the assessment ticket and link it back
# ---------------------------------------------------------------------------

def build_description(story_id: int, story_title: str) -> str:
    return (
        f"<div>Auto-generated Secure by Design Assessment for User Story "
        f"<a href=\"{ADO_ORG_URL}/{ADO_PROJECT}/_workitems/edit/{story_id}\">#{story_id}</a>: "
        f"{story_title}.</div>"
        f"<div><br/></div>"
        f"<div><b>Reviewer notes:</b></div>"
        f"<div>- Threat model reviewed: </div>"
        f"<div>- Data classification: </div>"
        f"<div>- Findings / follow-ups: </div>"
    )


def create_assessment_ticket(session: requests.Session, story: dict) -> int:
    fields = story.get("fields", {})
    story_id = story["id"]
    story_title = fields.get("System.Title", "")
    assignee = DEFAULT_ASSIGNEE or (fields.get("System.AssignedTo", {}) or {}).get(
        "uniqueName", ""
    )

    patch_doc = [
        {
            "op": "add",
            "path": "/fields/System.Title",
            "value": f"{ASSESSMENT_TITLE_PREFIX}{story_title}",
        },
        {
            "op": "add",
            "path": "/fields/System.Description",
            "value": build_description(story_id, story_title),
        },
        {
            "op": "add",
            "path": "/fields/System.Tags",
            "value": ASSESSMENT_TAG,
        },
        {
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "System.LinkTypes.Related",
                "url": f"{ADO_ORG_URL}/{ADO_PROJECT}/_apis/wit/workItems/{story_id}",
            },
        },
    ]
    if assignee:
        patch_doc.append(
            {"op": "add", "path": "/fields/System.AssignedTo", "value": assignee}
        )

    work_item_type_encoded = ASSESSMENT_WORK_ITEM_TYPE.replace(" ", "%20")
    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}/_apis/wit/workitems/${work_item_type_encoded}"
        f"?api-version={API_VERSION}"
    )
    headers = {"Content-Type": "application/json-patch+json"}
    resp = session.post(
        url, json=patch_doc, headers=headers, timeout=REQUEST_TIMEOUT_SECS
    )
    log.info(
        "Create assessment ticket for story %s -> status %s", story_id, resp.status_code
    )
    resp.raise_for_status()
    new_id = resp.json()["id"]
    log.info("Created assessment ticket %s linked to story %s", new_id, story_id)
    return new_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    fail_fast_on_missing_config()
    session = ado_session()

    story_ids = get_recent_user_stories(session)
    created_count = 0
    skipped_count = 0
    error_count = 0

    for story_id in story_ids:
        try:
            story = get_work_item_with_relations(session, story_id)
            if has_existing_assessment(session, story):
                log.info("Story %s already has an assessment ticket, skipping", story_id)
                skipped_count += 1
                continue
            create_assessment_ticket(session, story)
            created_count += 1
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            log.error("HTTP error processing story %s -> status %s", story_id, status)
            error_count += 1
        except Exception:
            log.error("Unexpected error processing story %s", story_id)
            error_count += 1

    log.info(
        "Run complete. created=%d skipped=%d errors=%d scanned=%d",
        created_count,
        skipped_count,
        error_count,
        len(story_ids),
    )

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
