#!/usr/bin/env python3
"""Create a BalkanID agent access request for a Microsoft Copilot agent and wait.

Uses Public API createRequest (requestType: AGENT_ACCESS). Exit 0 only on approval.

Microsoft exposes no pre-creation control point for Copilot agents: a Copilot Studio
agent is a Dataverse row that any maker with an environment can create, and a
declarative agent is an app package any developer can sideload. This gate is the
control point - it runs before `terraform apply`, `pac solution import`,
`pac copilot publish`, or the Graph app-catalog publish.

Requires env vars from env.example. No third-party packages.

Exit codes:
  0  approved
  1  denied, or partially approved
  2  the approval window elapsed with no decision
  3  the policy service could not be reached
"""

from __future__ import annotations

import json
import os
import sys

from balkanid_api import (
    ApprovalTimeout,
    TransportError,
    api_client_from_env,
    ci_mode,
    create_request,
    env_optional,
    load_dotenv,
    log_stderr,
    poll_request,
    poll_settings,
    redact_identifier,
)

# Copilot surfaces this reference knows how to gate.
SURFACES = {
    "copilot-studio": "Copilot Studio agent (Dataverse bot, shipped in a Power Platform solution)",
    "declarative-agent": "Microsoft 365 Copilot declarative agent (app package published to the org catalog)",
}


def agent_surface() -> str:
    surface = (env_optional("COPILOT_SURFACE", "copilot-studio") or "copilot-studio").lower()
    if surface not in SURFACES:
        raise SystemExit(
            f"invalid COPILOT_SURFACE={surface!r}; expected one of {', '.join(sorted(SURFACES))}"
        )
    return surface


def intended_identity(agent_name: str) -> str:
    """Entra identity the agent is expected to run as, when it is known up front.

    Populated by entra_agent_identity_gate.py / Terraform, or set directly in .env.
    """
    for name in ("INTENDED_AGENT_IDENTITY_ID", "AGENT_APP_ID", "ENTRA_AGENT_APP_ID"):
        value = env_optional(name)
        if value:
            return value
    return ""


def build_agent_access(surface: str, agent_name: str) -> dict:
    agent_access: dict = {
        "action": "CREATE",
        "name": agent_name,
        "agentType": env_optional("AGENT_TYPE", surface),
    }

    integration = env_optional("INTEGRATION_ID")
    if integration:
        agent_access["integrationId"] = integration

    identity = intended_identity(agent_name)
    if identity:
        agent_access["intendedIdentityId"] = identity

    # Surface-specific placement, so the approver sees where the agent will land.
    if surface == "copilot-studio":
        environment_id = env_optional("POWER_PLATFORM_ENVIRONMENT_ID")
        if environment_id:
            agent_access["environmentId"] = environment_id
        solution = env_optional("SOLUTION_UNIQUE_NAME")
        if solution:
            agent_access["solutionUniqueName"] = solution
        schema_name = env_optional("AGENT_SCHEMA_NAME")
        if schema_name:
            agent_access["schemaName"] = schema_name
    else:
        scope = env_optional("AGENT_SCOPE", "tenant")
        agent_access["publishScope"] = scope
        manifest_id = env_optional("TEAMS_APP_ID")
        if manifest_id:
            agent_access["appId"] = manifest_id

    return agent_access



def record_approval(payload: dict) -> None:
    """Append what was approved to the approval scope file.

    The gate and the provisioning step read their configuration from the same
    environment independently. Without a record, changing a variable between the two
    provisions a scope nobody approved while the approval record says otherwise.
    scripts/verify_approved_scope.py compares this file against the environment before
    provisioning runs.
    """
    path = env_optional("APPROVED_SCOPE_FILE", ".approved-scope.json")
    if not path:
        return

    existing: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing.update(payload)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2, sort_keys=True)
    except OSError as exc:
        log_stderr(f"warning: could not write {path}: {exc}")
        return
    log_stderr(f"recorded approved scope in {path}")


def main() -> int:
    load_dotenv()
    url, key_id, secret = api_client_from_env()
    owner = env_optional("BALKANID_AGENT_OWNER_EMAIL")
    if not owner:
        raise SystemExit("missing required env BALKANID_AGENT_OWNER_EMAIL")

    surface = agent_surface()
    agent = env_optional("AGENT_NAME", "example-trial-intake-agent")
    purpose = env_optional(
        "AGENT_PURPOSE",
        f"{SURFACES[surface]} provisioned with BalkanID approval",
    )
    poll_s, timeout_s = poll_settings()

    reason = purpose or f"Copilot agent gate for {agent}"

    if ci_mode():
        log_stderr("public API url=(redacted in CI)")
        log_stderr(
            f"creating agent access request owner={redact_identifier(owner)} "
            f"agent={agent!r} surface={surface!r}"
        )
    else:
        log_stderr(f"public API url={url}")
        log_stderr(f"creating agent access request owner={owner!r} agent={agent!r} surface={surface!r}")

    inp: dict = {
        "requestType": "AGENT_ACCESS",
        "employeeEmail": owner,
        "reason": reason,
        "runAsync": False,
        "payload": {"agentAccess": build_agent_access(surface, agent)},
    }

    # create_request is inside the try as well: a network failure here must report as
    # unreachable, not as a denial.
    request_id = ""
    try:
        request_id = create_request(url, key_id, secret, inp)
        log_stderr(f"request_id={request_id}")
        log_stderr("waiting for approval (fail closed on deny/timeout)")

        poll_request(
            url,
            key_id,
            secret,
            request_id,
            wait_provisioning=False,
            poll_s=poll_s,
            timeout_s=timeout_s,
            label="AGENT_ACCESS",
        )
    except ApprovalTimeout as exc:
        log_stderr(exc.message)
        print(json.dumps({"request_id": request_id, "status": "timeout"}), file=sys.stderr)
        return 2
    except TransportError as exc:
        log_stderr(exc.message)
        print(json.dumps({"request_id": request_id, "status": "unreachable"}), file=sys.stderr)
        return 3
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        status = "denied" if code == 1 else "failed"
        print(json.dumps({"request_id": request_id, "status": status}), file=sys.stderr)
        return code or 1

    record_approval(
        {
            "agent_request_id": request_id,
            "agent_name": agent,
            "copilot_surface": surface,
            "environment_id": env_optional("POWER_PLATFORM_ENVIRONMENT_ID"),
            "solution_unique_name": env_optional("SOLUTION_UNIQUE_NAME"),
        }
    )

    print(json.dumps({"request_id": request_id, "status": "approved", "surface": surface, "agent": agent}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
