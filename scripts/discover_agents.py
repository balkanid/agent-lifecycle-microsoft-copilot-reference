#!/usr/bin/env python3
"""Inventory Microsoft Copilot agents across a tenant and flag ungoverned ones.

Microsoft scatters agent inventory across three planes, none of which talk to each
other. This script reads all three and prints one reconciled list:

  1. Power Platform Inventory API  - tenant-wide Copilot Studio agents
     POST https://api.powerplatform.com/resourcequery/resources/query
     type: microsoft.copilotstudio/agents
  2. Dataverse `bots` table         - per-environment authoritative detail
     GET  https://<org>.crm.dynamics.com/api/data/v9.2/bots
  3. Microsoft Graph package catalog - declarative / custom-engine agents
     GET  https://graph.microsoft.com/v1.0/copilot/admin/catalog/packages

Each discovered agent is then checked against the BalkanID request history: an agent
with no approved AGENT_ACCESS request is reported as ungoverned. That list is the work
queue, and in most tenants it is the majority of the population.

Read-only. Run it on a schedule as well as in a pipeline: the agents it is designed to
find are precisely the ones that never entered a pipeline.

Auth: Entra client credentials (AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET).
Read-only throughout. Stdlib only.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from balkanid_api import (
    api_client_from_env,
    env,
    env_optional,
    gql,
    load_dotenv,
    log_stderr,
)

LOGIN_HOST = "https://login.microsoftonline.com"
POWER_PLATFORM_API = "https://api.powerplatform.com"
GRAPH_API = "https://graph.microsoft.com/v1.0"
INVENTORY_API_VERSION = "2024-10-01"

AGENT_REQUESTS = """
query AgentRequests($filter: RequestFilterInput, $first: Int, $after: String) {
  requests(filter: $filter, first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        status
        requestApprovalStatus
        requestType
        metadata
      }
    }
  }
}
"""


def token_for(scope: str) -> str:
    """Client-credentials token for one resource scope."""
    tenant = env("AZURE_TENANT_ID")
    body = urllib.parse.urlencode(
        {
            "client_id": env("AZURE_CLIENT_ID"),
            "client_secret": env("AZURE_CLIENT_SECRET"),
            "scope": scope,
            "grant_type": "client_credentials",
        }
    ).encode()
    req = urllib.request.Request(
        f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())["access_token"]
    except urllib.error.HTTPError as err:
        detail = err.read().decode() if err.fp else ""
        raise SystemExit(f"token request failed for scope {scope}: HTTP {err.code} {detail[:400]}") from err


# Planes that failed to read this run. A monitor must never report a clean tenant
# because it could not see it.
PLANE_ERRORS: list[str] = []


def call(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    plane: str = "",
) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode() if err.fp else ""
        message = f"{plane or url}: HTTP {err.code} {detail[:200]}"
        log_stderr(f"warning: {message}")
        PLANE_ERRORS.append(message)
        return {}
    except urllib.error.URLError as err:
        message = f"{plane or url}: unreachable: {err}"
        log_stderr(f"warning: {message}")
        PLANE_ERRORS.append(message)
        return {}


def inventory_agents() -> list[dict]:
    """Tenant-wide Copilot Studio agents via the Power Platform Inventory API."""
    token = token_for(f"{POWER_PLATFORM_API}/.default")
    url = f"{POWER_PLATFORM_API}/resourcequery/resources/query?api-version={INVENTORY_API_VERSION}"
    out: list[dict] = []
    skip = 0
    while True:
        body = {
            "TableName": "PowerPlatformResources",
            "Clauses": [
                {
                    "$type": "where",
                    "FieldName": "type",
                    "Operator": "in~",
                    "Values": ["'microsoft.copilotstudio/agents'"],
                },
                {
                    "$type": "project",
                    "FieldList": [
                        "name",
                        "type",
                        "properties.displayName",
                        "properties.environmentId",
                        "properties.createdBy",
                        "properties.createdTime",
                    ],
                },
            ],
            "Options": {"Top": 100, "Skip": skip},
        }
        page = call(url, token, method="POST", payload=body, plane="power-platform-inventory")
        rows = page.get("value") or page.get("Results") or []
        for row in rows:
            props = row.get("properties") or {}
            out.append(
                {
                    "source": "power-platform-inventory",
                    "id": row.get("name") or "",
                    "name": props.get("displayName") or row.get("name") or "",
                    "environment_id": normalise_environment_id(props.get("environmentId") or ""),
                    "created_by": _principal(props.get("createdBy")),
                    "created_at": props.get("createdTime") or "",
                }
            )
        if len(rows) < 100:
            break
        skip += 100
    return out


def _principal(value) -> str:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("userPrincipalName") or value.get("id") or ""
    return str(value or "")


def dataverse_agents(org_url: str) -> list[dict]:
    """Authoritative per-environment detail from the Dataverse `bots` table."""
    org_url = org_url.rstrip("/")
    token = token_for(f"{org_url}/.default")
    select = "botid,name,schemaname,statecode,statuscode,ismanaged,publishedon,_ownerid_value"
    # statecode 0 is Active; componentstate 0 is Published. Without these the result
    # includes retired and unpublished solution components and overstates the count.
    query = f"$select={select}&$filter=statecode eq 0 and componentstate eq 0"
    url = f"{org_url}/api/data/v9.2/bots?{query}"

    environment_id = normalise_environment_id(env_optional("POWER_PLATFORM_ENVIRONMENT_ID"))
    out: list[dict] = []

    while url:
        page = call(url, token, plane="dataverse")
        for row in page.get("value") or []:
            out.append(
                {
                    "source": "dataverse",
                    "id": row.get("botid") or "",
                    "name": row.get("name") or "",
                    "schema_name": row.get("schemaname") or "",
                    "managed": bool(row.get("ismanaged")),
                    "state": "active",
                    "published_on": row.get("publishedon") or "",
                    "environment_id": environment_id,
                }
            )
        url = page.get("@odata.nextLink") or ""

    return out


def graph_agents() -> list[dict]:
    """Declarative / custom-engine agents from the Graph package catalog.

    Requires CopilotPackages.Read.All and an Agent 365 license on the tenant.
    """
    token = token_for("https://graph.microsoft.com/.default")
    url = f"{GRAPH_API}/copilot/admin/catalog/packages"
    out: list[dict] = []
    while url:
        page = call(url, token, plane="graph-package-catalog")
        for row in page.get("value") or []:
            out.append(
                {
                    "source": "graph-package-catalog",
                    "id": row.get("id") or "",
                    "name": row.get("displayName") or "",
                    "publisher": _principal(row.get("publisher")),
                    "element_types": row.get("elementTypes") or [],
                    "platform": row.get("platform") or "",
                    "blocked": bool(row.get("isBlocked")),
                }
            )
        url = page.get("@odata.nextLink") or ""

    if not out and not PLANE_ERRORS:
        message = (
            "graph-package-catalog returned no agents. This endpoint requires Agent 365 "
            "licensing plus AI Administrator or Global Administrator, and returns an empty "
            "result rather than an error when either is missing. Declarative and "
            "custom-engine agents may be under-reported."
        )
        log_stderr(f"warning: {message}")
        PLANE_ERRORS.append(message)

    return out


def normalise_environment_id(value: str) -> str:
    """Environment ids arrive in different shapes from different planes.

    The Inventory API may return a fully qualified resource path while the operator
    supplies a bare GUID. Comparing them unnormalised would defeat deduplication and
    double-count every Copilot Studio agent.
    """
    value = (value or "").strip().lower()
    if "/" in value:
        value = value.rstrip("/").rsplit("/", 1)[-1]
    return value


def governed_agent_names() -> set[str]:
    """Agent names with an approved AGENT_ACCESS request in BalkanID.

    Paged: a tenant with more approved requests than one page would otherwise start
    reporting long-governed agents as ungoverned.
    """
    url, key_id, secret = api_client_from_env()
    page_size = int(env_optional("REQUEST_PAGE_SIZE", "200"))

    names: set[str] = set()
    approved_total = 0
    unnamed = 0
    cursor: str | None = None

    while True:
        variables: dict = {
            "filter": {"requestType": {"_eq": "AGENT_ACCESS"}},
            "first": page_size,
        }
        if cursor:
            variables["after"] = cursor

        data = gql(url, key_id, secret, AGENT_REQUESTS, variables)
        requests = (data.get("requests") or {})
        edges = requests.get("edges") or []

        for edge in edges:
            node = (edge or {}).get("node") or {}
            approval = (node.get("requestApprovalStatus") or node.get("status") or "").lower()
            if "approv" not in approval or "pending" in approval or "partial" in approval:
                continue

            approved_total += 1
            metadata = node.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            name = ""
            if isinstance(metadata, dict):
                agent_access = metadata.get("agentAccess") or metadata
                if isinstance(agent_access, dict):
                    name = (agent_access.get("name") or "").strip()

            if name:
                names.add(name.lower())
            else:
                unnamed += 1

        page_info = requests.get("pageInfo") or {}
        if page_info.get("hasNextPage") and page_info.get("endCursor"):
            cursor = page_info["endCursor"]
            continue
        if len(edges) == page_size and not page_info:
            log_stderr(
                f"warning: read {page_size} AGENT_ACCESS requests and the API returned no "
                "pageInfo, so there may be more. Raise REQUEST_PAGE_SIZE if agents you "
                "approved are reported as ungoverned."
            )
        break

    if approved_total and not names:
        # Every approved request lacked a readable agent name. Without this the operator
        # sees "everything is ungoverned" with no indication why.
        log_stderr(
            f"warning: found {approved_total} approved AGENT_ACCESS requests but could not "
            "read an agent name from any of them. Reconciliation will report every agent as "
            "ungoverned. Check the shape of request metadata on your tenant."
        )
    elif unnamed:
        log_stderr(f"warning: {unnamed} approved requests carried no readable agent name")

    return names


def merge(rows: list[dict]) -> list[dict]:
    """Collapse rows describing the same agent across the three inventory planes.

    The same agent legitimately appears in more than one source - the Inventory API
    and the Dataverse `bots` table both see every Copilot Studio agent. Counting it
    twice would overstate the problem, so rows are keyed on name plus environment and
    the richer fields are merged, with every source that saw it recorded.
    """
    merged: dict[tuple[str, str], dict] = {}

    for row in rows:
        row = dict(row)
        source = row.pop("source", "")
        key = ((row.get("name") or row.get("id") or "").strip().lower(), row.get("environment_id") or "")

        existing = merged.get(key)
        if existing is None:
            row["sources"] = [source] if source else []
            merged[key] = row
            continue

        if source and source not in existing["sources"]:
            existing["sources"].append(source)
        # Prefer a populated value over an empty one; never overwrite what we have.
        for field, value in row.items():
            if value in ("", None, []) or field == "sources":
                continue
            existing.setdefault(field, value)

    return list(merged.values())


def main() -> int:
    load_dotenv()

    sources: list[dict] = []
    log_stderr("== Power Platform Inventory API ==")
    sources.extend(inventory_agents())

    org_url = env_optional("DATAVERSE_ORG_URL")
    if org_url:
        log_stderr(f"== Dataverse {org_url} ==")
        sources.extend(dataverse_agents(org_url))
    else:
        log_stderr("DATAVERSE_ORG_URL not set - skipping Dataverse bots enumeration")

    if env_optional("DISCOVER_GRAPH_PACKAGES", "true").lower() not in ("0", "false", "no"):
        log_stderr("== Graph copilot package catalog ==")
        sources.extend(graph_agents())

    agents = merge(sources)

    governed: set[str] = set()
    if env_optional("API_KEY_ID") and env_optional("BALKANID_PUBLIC_API_URL"):
        log_stderr("== BalkanID approved AGENT_ACCESS requests ==")
        governed = governed_agent_names()
    else:
        log_stderr("BalkanID API env not set - skipping governance reconciliation")

    for agent in agents:
        agent["governed"] = (agent.get("name") or "").lower() in governed

    ungoverned = [a for a in agents if not a["governed"]]

    report = {
        "discovered": len(agents),
        "governed": len(agents) - len(ungoverned),
        "ungoverned": len(ungoverned),
        "raw_rows": len(sources),
        "complete": not PLANE_ERRORS,
        "plane_errors": PLANE_ERRORS,
        "agents": agents,
    }
    print(json.dumps(report, indent=2))

    log_stderr("")
    log_stderr(
        f"discovered={report['discovered']} governed={report['governed']} "
        f"ungoverned={report['ungoverned']} complete={report['complete']}"
    )
    for agent in ungoverned:
        seen_in = ",".join(agent["sources"])
        log_stderr(f"  UNGOVERNED  {seen_in:<48} {agent.get('name') or agent.get('id')}")

    if PLANE_ERRORS:
        log_stderr("")
        log_stderr("this inventory is INCOMPLETE; one or more planes could not be read:")
        for message in PLANE_ERRORS:
            log_stderr(f"  {message}")

    strict = env_optional("FAIL_ON_UNGOVERNED", "false").lower() in ("1", "true", "yes")
    if strict:
        if PLANE_ERRORS:
            # A plane that could not be read is not a clean tenant. Exiting 0 here would
            # turn a broken credential into a green monitor.
            log_stderr("FAIL_ON_UNGOVERNED set and the inventory is incomplete")
            return 3
        if ungoverned:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
