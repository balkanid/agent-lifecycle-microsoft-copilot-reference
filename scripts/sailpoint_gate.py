#!/usr/bin/env python3
"""Raise the Copilot agent access request in SailPoint instead of BalkanID.

The gate is a process that exits 0 on approval and non-zero otherwise. Nothing else in
this repository depends on which system made the decision, so it can be pointed at an
existing IGA platform. Swap `gate.py` for this script and the rest is unchanged.

Useful when an organisation already runs an access request workflow with established
approvers and routing rules, and a second approval queue for agents would be a real
cost.

Two modes, because the two SailPoint products have nothing in common at the API layer:

  SAILPOINT_MODE=isc  (default) - Identity Security Cloud / IdentityNow, SaaS
      POST {tenant}/oauth/token                  client_credentials
      POST /v3/access-requests                   raise the request
      GET  /v3/access-request-status             poll until approved or rejected

  SAILPOINT_MODE=iiq            - IdentityIQ, on-premises
      POST /identityiq/rest/workflows/{name}/launch   launch an LCM workflow
      GET  /identityiq/rest/workflows/{taskResultId}  poll the TaskResult

Exit codes match gate.py:
  0  approved
  1  denied
  2  the approval window elapsed with no decision
  3  the SailPoint API could not be reached

Stdlib only.

Scope note: a SailPoint access request grants an access item to an identity. There is no
agent object for the approval to attach to, so `discover_agents.py` has nowhere to record
a governance state when it reconciles the tenant. The preventive control works; the
after-approval linkage does not. See docs/SAILPOINT.md for the comparison.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from balkanid_api import (
    ApprovalTimeout,
    TransportError,
    env,
    env_optional,
    load_dotenv,
    log_stderr,
    poll_settings,
)

# Deliberately narrow. ISC marks a request EXECUTING as soon as it is submitted, well
# before any approval happens, and IdentityIQ reports a workflow with no approval step
# as complete immediately. Treating either as approval would make the gate pass without
# a human, which is worse than having no gate at all.
APPROVED = {"approved", "provisioned"}
DENIED = {"rejected", "denied", "cancelled", "canceled", "terminated", "failed", "expired"}
# Seen while the request is still in flight. Keep polling.
PENDING = {"pending", "executing", "submitted", "in_progress", "inprogress", "running", "queued"}


def http(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    payload: dict | None = None,
    form: dict | None = None,
) -> dict:
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        content_type = "application/x-www-form-urlencoded"
    elif payload is not None:
        data = json.dumps(payload).encode()
        content_type = "application/json"
    else:
        data = None
        content_type = None

    hdrs = {"Accept": "application/json", **(headers or {})}
    if content_type:
        hdrs["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode() if err.fp else ""
        raise TransportError(f"{method} {url} failed: HTTP {err.code} {detail[:600]}") from err
    except urllib.error.URLError as err:
        raise TransportError(f"{method} {url} unreachable: {err}") from err


# --------------------------------------------------------------------------- ISC


def isc_base() -> str:
    return env("SAILPOINT_BASE_URL").rstrip("/")


def isc_token() -> str:
    base = isc_base()
    token_url = env_optional("SAILPOINT_TOKEN_URL") or f"{base}/oauth/token"
    body = http(
        token_url,
        method="POST",
        form={
            "grant_type": "client_credentials",
            "client_id": env("SAILPOINT_CLIENT_ID"),
            "client_secret": env("SAILPOINT_CLIENT_SECRET"),
        },
    )
    token = body.get("access_token")
    if not token:
        raise SystemExit(f"no access_token in SailPoint token response: {body}")
    return token


def isc_identity_id(token: str, email: str) -> str:
    """Resolve the requester's SailPoint identity id from their email."""
    override = env_optional("SAILPOINT_IDENTITY_ID")
    if override:
        return override

    results = http(
        f"{isc_base()}/v3/search",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        payload={
            "indices": ["identities"],
            "query": {"query": f'email:"{email}"'},
            "sort": ["id"],
        },
    )
    rows = results if isinstance(results, list) else results.get("items") or []
    if not rows:
        raise SystemExit(f"no SailPoint identity found for {email!r}; set SAILPOINT_IDENTITY_ID")
    return rows[0].get("id") or ""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_timestamp(value: str) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def isc_create_request(token: str, identity_id: str, agent: str) -> str:
    item_id = env("SAILPOINT_ACCESS_ITEM_ID")
    item_type = env_optional("SAILPOINT_ACCESS_ITEM_TYPE", "ACCESS_PROFILE").upper()

    payload = {
        "requestedFor": [identity_id],
        "requestType": "GRANT_ACCESS",
        "requestedItems": [
            {
                "type": item_type,
                "id": item_id,
                "comment": env_optional(
                    "AGENT_PURPOSE",
                    f"Create Microsoft Copilot agent {agent}",
                )[:500],
                "clientMetadata": {
                    "agentName": agent,
                    "copilotSurface": env_optional("COPILOT_SURFACE", "copilot-studio"),
                    "environmentId": env_optional("POWER_PLATFORM_ENVIRONMENT_ID"),
                    "requestedBy": "balkanid-copilot-agent-lifecycle-reference",
                },
            }
        ],
    }

    http(
        f"{isc_base()}/v3/access-requests",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        payload=payload,
    )
    # /v3/access-requests returns 202 with an empty body; the request is tracked by
    # requester + item, so poll access-request-status rather than an id.
    log_stderr("access request submitted (202 accepted)")
    return item_id


def approval_state(item: dict) -> str:
    """Collapse an ISC request's approval details into approved / rejected / pending.

    `state` alone is not enough: a request sits in EXECUTING from the moment it is
    submitted. The approval decision lives in approvalDetails / approvalStatus.
    """
    details = item.get("approvalDetails") or item.get("approvalInfo") or []
    if isinstance(details, dict):
        details = [details]

    statuses = [
        (d.get("status") or d.get("approvalStatus") or "").strip().upper()
        for d in details
        if isinstance(d, dict)
    ]
    statuses = [s for s in statuses if s]

    if not statuses:
        return "pending"
    if any(s in ("REJECTED", "DENIED") for s in statuses):
        return "rejected"
    if all(s in ("APPROVED", "FINISHED", "COMPLETED") for s in statuses):
        return "approved"
    return "pending"


def isc_poll(
    token: str,
    identity_id: str,
    item_id: str,
    agent: str,
    submitted_at: dt.datetime,
    poll_s: int,
    timeout_s: int,
) -> dict:
    query = urllib.parse.urlencode(
        {"requested-for": identity_id, "limit": 50, "count": "true"}
    )
    url = f"{isc_base()}/v3/access-request-status?{query}"
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        rows = http(url, headers={"Authorization": f"Bearer {token}"})
        items = rows if isinstance(rows, list) else rows.get("items") or []
        for item in items:
            if (item.get("requestedItemId") or item.get("id")) != item_id:
                continue

            # The status endpoint returns every request this identity has raised for
            # this access item, including ones decided days ago for other agents.
            # Without both checks below, the gate would return an old approval
            # immediately and never wait for a human at all.
            metadata = item.get("clientMetadata") or {}
            item_agent = (metadata.get("agentName") or "").strip()
            if item_agent and item_agent != agent:
                continue

            created = parse_timestamp(
                item.get("created") or item.get("createdDate") or item.get("requestCreated") or ""
            )
            if created is not None and created < submitted_at:
                continue
            if created is None and not item_agent:
                # Nothing identifies this row as ours. Refusing to match is the only
                # safe reading: a stale approval must never satisfy this gate.
                log_stderr(
                    "SAILPOINT: a matching access item was found but carries neither "
                    "clientMetadata.agentName nor a creation timestamp, so it cannot be "
                    "attributed to this request. Continuing to poll."
                )
                continue
            state = (item.get("state") or item.get("requestState") or "").strip().lower()
            approval = approval_state(item)
            log_stderr(f"SAILPOINT: state={state!r} approval={approval!r}")

            if state in DENIED or approval == "rejected":
                log_stderr("SailPoint request denied")
                raise SystemExit(1)

            # EXECUTING means submitted, not approved. Accept only once every approval
            # on the request has finished - whatever state ISC has moved on to by then.
            if approval == "approved" or state in APPROVED:
                return item

            if state and state not in PENDING and state not in APPROVED:
                log_stderr(f"unrecognised SailPoint state {state!r}; continuing to poll")
        time.sleep(poll_s)

    raise ApprovalTimeout(f"timed out after {timeout_s}s waiting on SailPoint request")


# --------------------------------------------------------------------------- IIQ


def iiq_auth_header() -> dict:
    token = env_optional("SAILPOINT_IIQ_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    pair = f"{env('SAILPOINT_IIQ_USERNAME')}:{env('SAILPOINT_IIQ_PASSWORD')}"
    encoded = base64.b64encode(pair.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def iiq_launch(agent: str, owner: str) -> str:
    base = env("SAILPOINT_IIQ_URL").rstrip("/")
    workflow = env_optional("SAILPOINT_IIQ_WORKFLOW", "LCM Provisioning")
    payload = {
        "input": [
            {"key": "identityName", "value": owner},
            {"key": "agentName", "value": agent},
            {"key": "copilotSurface", "value": env_optional("COPILOT_SURFACE", "copilot-studio")},
            {"key": "environmentId", "value": env_optional("POWER_PLATFORM_ENVIRONMENT_ID")},
            {"key": "comments", "value": env_optional("AGENT_PURPOSE", f"Create Copilot agent {agent}")},
        ]
    }
    encoded_workflow = urllib.parse.quote(workflow, safe="")
    result = http(
        f"{base}/identityiq/rest/workflows/{encoded_workflow}/launch",
        method="POST",
        headers=iiq_auth_header(),
        payload=payload,
    )
    task_result_id = result.get("id") or result.get("taskResultId") or ""
    if not task_result_id:
        raise SystemExit(f"IdentityIQ workflow launch returned no TaskResult id: {result}")
    return task_result_id


def iiq_poll(task_result_id: str, poll_s: int, timeout_s: int) -> dict:
    base = env("SAILPOINT_IIQ_URL").rstrip("/")
    url = f"{base}/identityiq/rest/workflows/{task_result_id}"
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        result = http(url, headers=iiq_auth_header())
        state = (result.get("completionStatus") or result.get("status") or "").strip().lower()
        log_stderr(f"SAILPOINT-IIQ: completionStatus={state!r}")

        if state in DENIED or "error" in state:
            log_stderr("IdentityIQ workflow ended in a failed or denied state")
            raise SystemExit(1)

        if state in ("success", "complete", "completed"):
            # Confirm an approval actually happened. A workflow with no approval step
            # completes instantly, and accepting that would approve everything.
            attrs = result.get("attributes") or {}
            decision = str(
                attrs.get("approvalDecision") or attrs.get("approvalStatus") or ""
            ).strip().lower()
            if decision in ("rejected", "denied"):
                log_stderr("IdentityIQ approval was rejected")
                raise SystemExit(1)
            if decision in ("approved", "finished"):
                return result
            if env_optional("SAILPOINT_IIQ_REQUIRE_APPROVAL_ATTRIBUTE", "true").lower() in ("1", "true", "yes"):
                raise SystemExit(
                    f"IdentityIQ workflow {task_result_id} completed with completionStatus={state!r} "
                    "but reported no approval decision. Either the workflow has no approval step, "
                    "or it does not expose one of approvalDecision / approvalStatus. Confirm the "
                    "workflow gates on a human, then set SAILPOINT_IIQ_REQUIRE_APPROVAL_ATTRIBUTE=false "
                    "to accept completion alone."
                )
            return result
        time.sleep(poll_s)

    raise ApprovalTimeout(
        f"timed out after {timeout_s}s waiting on IdentityIQ TaskResult {task_result_id}"
    )


# -------------------------------------------------------------------------- main


def main() -> int:
    load_dotenv()
    mode = env_optional("SAILPOINT_MODE", "isc").lower()
    agent = env_optional("AGENT_NAME", "example-trial-intake-agent")
    owner = env("BALKANID_AGENT_OWNER_EMAIL")
    poll_s, timeout_s = poll_settings()

    log_stderr(f"SailPoint gate mode={mode!r} agent={agent!r}")

    try:
        if mode == "isc":
            token = isc_token()
            identity_id = isc_identity_id(token, owner)
            log_stderr(f"requester identity_id={identity_id!r}")
            # Recorded before submitting, so a request decided earlier cannot match.
            submitted_at = utc_now() - dt.timedelta(seconds=30)
            item_id = isc_create_request(token, identity_id, agent)
            isc_poll(token, identity_id, item_id, agent, submitted_at, poll_s, timeout_s)
            print(
                json.dumps(
                    {"mode": "isc", "identity_id": identity_id, "status": "approved", "agent": agent}
                )
            )
            return 0

        if mode == "iiq":
            task_result_id = iiq_launch(agent, owner)
            log_stderr(f"task_result_id={task_result_id}")
            iiq_poll(task_result_id, poll_s, timeout_s)
            print(
                json.dumps(
                    {
                        "mode": "iiq",
                        "task_result_id": task_result_id,
                        "status": "approved",
                        "agent": agent,
                    }
                )
            )
            return 0
    except ApprovalTimeout as exc:
        log_stderr(exc.message)
        print(json.dumps({"status": "timeout", "agent": agent}), file=sys.stderr)
        return 2
    except TransportError as exc:
        log_stderr(exc.message)
        print(json.dumps({"status": "unreachable", "agent": agent}), file=sys.stderr)
        return 3

    raise SystemExit(f"invalid SAILPOINT_MODE={mode!r}; expected 'isc' or 'iiq'")


if __name__ == "__main__":
    sys.exit(main())
