    #!/usr/bin/env python3
"""
create_assessment_tickets.py

Queries Azure DevOps (ADO) for User Stories and ensures every one of them has
a linked "Secure by Design Assessment" work item.

Creates the assessment ticket if the story does not already have its OWN
assessment ticket.

Important duplicate-check behavior:
    A User Story may have multiple "Related" work items, including SbD
    assessment tickets belonging to OTHER User Stories.

    Therefore, an existing assessment is considered a match ONLY when:
        1. The related work item type is "Secure by Design Assessment", AND
        2. Its title exactly matches:
               <ASSESSMENT_TITLE_PREFIX><current story title>

    Example:
        User Story:
            Implement login with MFA

        Expected assessment:
            SbD_Implement login with MFA

    If the story is related to:
        SbD_Add password reset

    that assessment is ignored because it belongs to another story.

Scoping:
    - Scope is purely by Iteration Path (ADO_ITERATION_PATH, required) and,
      optionally, Area Path.
    - There is no date-based filtering.
    - Every run scans every User Story in the configured iteration.
    - The script is safe to run repeatedly against the same iteration.

Safety behavior:
    - DRY_RUN defaults to "true".
    - In dry-run mode, the script makes no write calls to Azure DevOps.
    - Set DRY_RUN=false to actually create tickets.

Security notes:
    - The PAT is read from an environment variable only.
    - The PAT is never printed, logged, or written to disk.
    - Logging is restricted to work item IDs and HTTP status codes unless
      DRY_RUN_SHOW_TITLES=true is explicitly enabled.

Required environment variables:
    ADO_ORG_URL
    ADO_PROJECT
    ADO_PAT
    ADO_ITERATION_PATH

Optional environment variables:
    DRY_RUN
    DRY_RUN_SHOW_TITLES
    ADO_AREA_PATH
    ASSESSMENT_WORK_ITEM_TYPE
    ASSESSMENT_TAG
    ASSESSMENT_TITLE_PREFIX
"""

import logging
import os
import sys

import requests
from requests.auth import HTTPBasicAuth


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADO_ORG_URL = os.environ.get("ADO_ORG_URL", "").rstrip("/")
ADO_PROJECT = os.environ.get("ADO_PROJECT", "")
ADO_PAT = os.environ.get("ADO_PAT", "")
ADO_AREA_PATH = os.environ.get("ADO_AREA_PATH", "").strip()
ADO_ITERATION_PATH = os.environ.get("ADO_ITERATION_PATH", "").strip()

DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() != "false"

DRY_RUN_SHOW_TITLES = (
    os.environ.get("DRY_RUN_SHOW_TITLES", "false").strip().lower() == "true"
)

ASSESSMENT_WORK_ITEM_TYPE = os.environ.get(
    "ASSESSMENT_WORK_ITEM_TYPE",
    "Secure by Design Assessment",
)

ASSESSMENT_TAG = os.environ.get(
    "ASSESSMENT_TAG",
    "SbD",
)

ASSESSMENT_TITLE_PREFIX = os.environ.get(
    "ASSESSMENT_TITLE_PREFIX",
    "SbD_",
)

# ADO account that should receive all Secure by Design Assessment tickets
SBD_ASSIGNEE = "Dhruv.Jain@nestle.com"

API_VERSION = "7.1"
REQUEST_TIMEOUT_SECS = 30


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("sbd-automation")


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

def fail_fast_on_missing_config():
    missing = [
        name
        for name, val in (
            ("ADO_ORG_URL", ADO_ORG_URL),
            ("ADO_PROJECT", ADO_PROJECT),
            ("ADO_PAT", ADO_PAT),
            ("ADO_ITERATION_PATH", ADO_ITERATION_PATH),
        )
        if not val
    ]

    if missing:
        log.error(
            "Missing required environment variables: %s",
            ", ".join(missing),
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Azure DevOps session
# ---------------------------------------------------------------------------

def ado_session() -> requests.Session:
    session = requests.Session()

    # Azure DevOps REST API uses Basic authentication with
    # an empty username and the PAT as the password.
    session.auth = HTTPBasicAuth("", ADO_PAT)

    session.headers.update(
        {
            "Content-Type": "application/json",
        }
    )

    return session


# ---------------------------------------------------------------------------
# Step 1:
# Find User Stories in the target iteration
# ---------------------------------------------------------------------------

def get_user_stories_in_iteration(
    session: requests.Session,
) -> list[int]:
    """
    Returns every User Story under ADO_ITERATION_PATH.

    If ADO_AREA_PATH is configured, the query is additionally restricted
    to that Area Path and its sub-areas.

    There is no date filtering.
    """

    area_clause = ""

    if ADO_AREA_PATH:
        # WIQL UNDER includes sub-areas.
        area_clause = (
            f"AND [System.AreaPath] UNDER '{ADO_AREA_PATH}'\n"
        )

    wiql = {
        "query": f"""
            SELECT [System.Id]
            FROM WorkItems
            WHERE [System.TeamProject] = '{ADO_PROJECT}'
            AND [System.WorkItemType] = 'User Story'
            AND [System.IterationPath] UNDER '{ADO_ITERATION_PATH}'
            {area_clause}
            ORDER BY [System.CreatedDate] ASC
        """
    }

    log.info(
        "Scope for this run: project=%s area_path=%s "
        "iteration_path=%s dry_run=%s",
        ADO_PROJECT,
        ADO_AREA_PATH or "(entire project)",
        ADO_ITERATION_PATH,
        DRY_RUN,
    )

    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}"
        f"/_apis/wit/wiql?api-version={API_VERSION}"
    )

    resp = session.post(
        url,
        json=wiql,
        timeout=REQUEST_TIMEOUT_SECS,
    )

    log.info(
        "WIQL query for User Stories in iteration -> status %s",
        resp.status_code,
    )

    resp.raise_for_status()

    work_items = resp.json().get("workItems", [])

    ids = [
        wi["id"]
        for wi in work_items
    ]

    log.info(
        "Found %d User Story candidate(s) in scope",
        len(ids),
    )

    return ids


# ---------------------------------------------------------------------------
# Step 2:
# Get a User Story including its Related work items
# ---------------------------------------------------------------------------

def get_work_item_with_relations(
    session: requests.Session,
    story_id: int,
) -> dict:
    """
    Fetches the User Story including its relations.
    """

    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}"
        f"/_apis/wit/workitems/{story_id}"
        f"?$expand=relations&api-version={API_VERSION}"
    )

    resp = session.get(
        url,
        timeout=REQUEST_TIMEOUT_SECS,
    )

    log.info(
        "Fetch story %s with relations -> status %s",
        story_id,
        resp.status_code,
    )

    resp.raise_for_status()

    return resp.json()


# ---------------------------------------------------------------------------
# Step 2A:
# Check whether the story already has ITS OWN SbD assessment
# ---------------------------------------------------------------------------

def has_existing_assessment(
    session: requests.Session,
    story: dict,
) -> bool:
    """
    Checks whether the current User Story has its own Secure by Design
    Assessment.

    IMPORTANT:

    We cannot simply check whether ANY related item is a
    "Secure by Design Assessment".

    A User Story may be related to an assessment belonging to another
    User Story.

    Therefore, an existing assessment is considered a match only when:

        Work Item Type == ASSESSMENT_WORK_ITEM_TYPE

    AND

        Work Item Title ==
        ASSESSMENT_TITLE_PREFIX + current User Story title

    Example:

        Current story:
            Implement login with MFA

        Expected assessment:
            SbD_Implement login with MFA

        Related assessment:
            SbD_Add password reset

        Result:
            NOT a match -> create assessment for current story.
    """

    relations = story.get("relations", [])

    related_ids = []

    # ---------------------------------------------------------------
    # Extract only "Related" work item IDs
    # ---------------------------------------------------------------

    for rel in relations:

        if rel.get("rel") != "System.LinkTypes.Related":
            continue

        url = rel.get("url", "")

        if "/workItems/" not in url:
            continue

        try:
            related_id = int(
                url.rsplit("/", 1)[-1]
            )

            related_ids.append(related_id)

        except ValueError:
            continue

    if not related_ids:
        return False

    # ---------------------------------------------------------------
    # Get current story title
    # ---------------------------------------------------------------

    story_fields = story.get("fields", {})

    story_title = story_fields.get(
        "System.Title",
        "",
    )

    expected_assessment_title = (
        f"{ASSESSMENT_TITLE_PREFIX}{story_title}"
    )

    # ---------------------------------------------------------------
    # Fetch type + title for all related items
    # ---------------------------------------------------------------

    ids_param = ",".join(
        str(item_id)
        for item_id in related_ids
    )

    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}"
        f"/_apis/wit/workitems"
        f"?ids={ids_param}"
        f"&fields=System.WorkItemType,System.Title"
        f"&api-version={API_VERSION}"
    )

    resp = session.get(
        url,
        timeout=REQUEST_TIMEOUT_SECS,
    )

    log.info(
        "Check related items %s for story %s -> status %s",
        ids_param,
        story.get("id"),
        resp.status_code,
    )

    resp.raise_for_status()

    # ---------------------------------------------------------------
    # Check each related work item
    # ---------------------------------------------------------------

    for item in resp.json().get("value", []):

        fields = item.get(
            "fields",
            {},
        )

        work_item_type = fields.get(
            "System.WorkItemType",
            "",
        )

        work_item_title = fields.get(
            "System.Title",
            "",
        )

        # First check that it is an SbD assessment.
        if work_item_type != ASSESSMENT_WORK_ITEM_TYPE:
            continue

        # Then check that it belongs to THIS story.
        if work_item_title == expected_assessment_title:

            log.info(
                "Story %s already has its own assessment ticket %s",
                story.get("id"),
                item.get("id"),
            )

            return True

        # This is an assessment, but it belongs to another story.
        log.info(
            "Related assessment %s for story %s does not match "
            "the expected assessment title; continuing",
            item.get("id"),
            story.get("id"),
        )

    # No matching assessment was found.
    return False


# ---------------------------------------------------------------------------
# Step 3:
# Build assessment description
# ---------------------------------------------------------------------------

def build_description(
    story_id: int,
    story_title: str,
) -> str:
    return f"""
    <table border="1" cellpadding="6" cellspacing="0">
        <thead>
            <tr>
                <th>Security Service</th>
                <th>Description</th>
                <th>Prerequisites</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><b>Security Requirements</b></td>
                <td>Define security controls early in SDLC</td>
                <td>Project scope, regulatory requirements, business objectives</td>
            </tr>
            <tr>
                <td><b>Threat Modeling</b></td>
                <td>Identify and prioritize potential threats</td>
                <td>Architecture/design documents, data flow diagrams</td>
            </tr>
            <tr>
                <td><b>Static Application Security Testing (SAST)</b></td>
                <td>Analyze source code for vulnerabilities before deployment</td>
                <td>Source code, build pipeline integration</td>
            </tr>
            <tr>
                <td><b>Dynamic Application Security Testing (DAST)</b></td>
                <td>Scan the running application for vulnerabilities</td>
                <td>Running instance of application, test environment</td>
            </tr>
        </tbody>
    </table>

    <br/>

    <table border="1" cellpadding="6" cellspacing="0">
        <thead>
            <tr>
                <th>Security Service</th>
                <th>Scope</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><b>Security Requirements</b></td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
            </tr>
            <tr>
                <td><b>Threat Modeling</b></td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
            </tr>
            <tr>
                <td><b>Static Application Security Testing (SAST)</b></td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
            </tr>
            <tr>
                <td><b>Dynamic Application Security Testing (DAST)</b></td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
            </tr>
        </tbody>
    </table>
    """


# ---------------------------------------------------------------------------
# Step 4:
# Create the assessment ticket
# ---------------------------------------------------------------------------

def create_assessment_ticket(
    session: requests.Session,
    story: dict,
) -> int:
    """
    Creates the Secure by Design Assessment and links it back to
    the User Story using a Related link.
    """

    fields = story.get(
        "fields",
        {},
    )

    story_id = story["id"]

    story_title = fields.get(
        "System.Title",
        "",
    )

    assignee = SBD_ASSIGNEE
    
    # ---------------------------------------------------------------
    # Dry run
    # ---------------------------------------------------------------

    if DRY_RUN:

        if DRY_RUN_SHOW_TITLES:

            log.info(
                "[DRY-RUN] would create '%s%s' for story %s "
                "(assignee=%s)",
                ASSESSMENT_TITLE_PREFIX,
                story_title,
                story_id,
                assignee or "(unset)",
            )

        else:

            log.info(
                "[DRY-RUN] would create assessment ticket for story %s "
                "(assignee=%s)",
                story_id,
                assignee or "(unset)",
            )

        # No real ticket created.
        return -1

    # ---------------------------------------------------------------
    # Build Azure DevOps JSON Patch document
    # ---------------------------------------------------------------

    patch_doc = [
        {
            "op": "add",
            "path": "/fields/System.Title",
            "value": (
                f"{ASSESSMENT_TITLE_PREFIX}"
                f"{story_title}"
            ),
        },
        {
            "op": "add",
            "path": "/fields/System.AreaPath",
            "value": ADO_AREA_PATH,
        },
        {
            "op": "add",
            "path": "/fields/System.IterationPath",
            "value": ADO_ITERATION_PATH,
        },
        {
            "op": "add",
            "path": "/fields/System.Description",
            "value": build_description(
                story_id,
                story_title,
            ),
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
                "url": (
                    f"{ADO_ORG_URL}/{ADO_PROJECT}"
                    f"/_apis/wit/workItems/{story_id}"
                ),
            },
        },
    ]

    # ---------------------------------------------------------------
    # Optional assignee
    # ---------------------------------------------------------------

    if assignee:

        patch_doc.append(
            {
                "op": "add",
                "path": "/fields/System.AssignedTo",
                "value": assignee,
            }
        )

    # ---------------------------------------------------------------
    # Create work item
    # ---------------------------------------------------------------

    work_item_type_encoded = (
        ASSESSMENT_WORK_ITEM_TYPE.replace(
            " ",
            "%20",
        )
    )

    url = (
        f"{ADO_ORG_URL}/{ADO_PROJECT}"
        f"/_apis/wit/workitems/"
        f"${work_item_type_encoded}"
        f"?api-version={API_VERSION}"
    )

    headers = {
        "Content-Type": "application/json-patch+json",
    }

    resp = session.post(
        url,
        json=patch_doc,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECS,
    )

    log.info(
        "Create assessment ticket for story %s -> status %s",
        story_id,
        resp.status_code,
    )

    resp.raise_for_status()

    new_id = resp.json()["id"]

    log.info(
        "Created assessment ticket %s linked to story %s",
        new_id,
        story_id,
    )

    return new_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    # ---------------------------------------------------------------
    # Validate configuration
    # ---------------------------------------------------------------

    fail_fast_on_missing_config()

    session = ado_session()

    # ---------------------------------------------------------------
    # Dry-run notification
    # ---------------------------------------------------------------

    if DRY_RUN:

        log.info(
            "DRY_RUN is ON — no tickets will be created or modified "
            "in Azure DevOps. Set DRY_RUN=false to let this run "
            "create real tickets."
        )

    # ---------------------------------------------------------------
    # Get User Stories
    # ---------------------------------------------------------------

    story_ids = get_user_stories_in_iteration(
        session
    )

    created_count = 0
    skipped_count = 0
    error_count = 0

    # ---------------------------------------------------------------
    # Process every User Story
    # ---------------------------------------------------------------

    for story_id in story_ids:

        try:

            # Fetch story + Related work items.
            story = get_work_item_with_relations(
                session,
                story_id,
            )

            # Check whether THIS story has its OWN assessment.
            if has_existing_assessment(
                session,
                story,
            ):

                log.info(
                    "Story %s already has its own assessment ticket, "
                    "skipping",
                    story_id,
                )

                skipped_count += 1

                continue

            # No matching assessment found.
            create_assessment_ticket(
                session,
                story,
            )

            created_count += 1

        except requests.HTTPError as exc:

            status = (
                exc.response.status_code
                if exc.response is not None
                else "unknown"
            )

            log.error(
                "HTTP error processing story %s -> status %s",
                story_id,
                status,
            )

            error_count += 1

        except Exception:

            log.error(
                "Unexpected error processing story %s",
                story_id,
            )

            error_count += 1

    # ---------------------------------------------------------------
    # Run summary
    # ---------------------------------------------------------------

    verb = (
        "would create"
        if DRY_RUN
        else "created"
    )

    log.info(
        "Run complete (dry_run=%s). %s=%d skipped=%d "
        "errors=%d scanned=%d",
        DRY_RUN,
        verb,
        created_count,
        skipped_count,
        error_count,
        len(story_ids),
    )

    # ---------------------------------------------------------------
    # Fail GitHub Actions job if any story encountered an error
    # ---------------------------------------------------------------

    if error_count > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
