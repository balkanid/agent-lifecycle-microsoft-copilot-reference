#!/usr/bin/env python3
"""Validate CD/local env before gates, Terraform, publish, or sync run."""

from __future__ import annotations

import shutil
import sys

from balkanid_api import env_optional, load_dotenv, log_stderr


def is_true(name: str, default: str = "false") -> bool:
    return env_optional(name, default).lower() in ("1", "true", "yes")


def require(name: str, errors: list[str]) -> None:
    if not env_optional(name):
        errors.append(f"missing {name}")


def check_pac(warnings: list[str], errors: list[str]) -> None:
    candidate = env_optional("PAC_CLI") or "pac"
    if shutil.which(candidate):
        log_stderr("preflight: pac CLI found")
        return
    errors.append(
        "Power Platform CLI (pac) not on PATH - required to publish a Copilot Studio agent. "
        "Install: dotnet tool install --global Microsoft.PowerApps.CLI.Tool"
    )


def check_solution_zip(warnings: list[str], errors: list[str]) -> None:
    import os

    path = env_optional("SOLUTION_ZIP_PATH")
    if not path:
        warnings.append("SOLUTION_ZIP_PATH not set - Terraform will not import a solution")
        return
    if not os.path.isfile(path):
        errors.append(f"SOLUTION_ZIP_PATH does not exist: {path}")
        return
    log_stderr(f"preflight: solution package found ({path})")


def main() -> int:
    load_dotenv()
    operation = env_optional("CD_OPERATION", "apply").lower()
    surface = env_optional("COPILOT_SURFACE", "copilot-studio").lower()
    errors: list[str] = []
    warnings: list[str] = []

    identity_gate = is_true("ENTRA_IDENTITY_GATE")
    agent_gate = is_true("AGENT_GATE", "true")
    provision = is_true("PROVISION_COPILOT_AGENT")
    publish = is_true("PUBLISH_AGENT")
    sync = is_true("TRIGGER_INTEGRATION_SYNC", "true")
    gate_backend = env_optional("GATE_BACKEND", "balkanid").lower()

    if surface not in ("copilot-studio", "declarative-agent"):
        errors.append(f"invalid COPILOT_SURFACE={surface!r}")

    if gate_backend not in ("balkanid", "sailpoint"):
        errors.append(f"invalid GATE_BACKEND={gate_backend!r} (expected balkanid or sailpoint)")

    if operation == "apply":
        if not identity_gate and not agent_gate:
            warnings.append(
                "both ENTRA_IDENTITY_GATE and AGENT_GATE are false - no approvals will run"
            )

        if identity_gate or agent_gate:
            if gate_backend == "balkanid":
                require("BALKANID_PUBLIC_API_URL", errors)
                require("API_KEY_ID", errors)
                require("API_KEY_SECRET", errors)
                require("BALKANID_AGENT_OWNER_EMAIL", errors)
                require("INTEGRATION_ID", errors)
                if identity_gate and not env_optional("ENTRA_GRAPH_PERMISSIONS"):
                    warnings.append(
                        "ENTRA_GRAPH_PERMISSIONS not set - the identity request will omit the "
                        "permission list the approver is meant to review"
                    )
            else:
                require("BALKANID_AGENT_OWNER_EMAIL", errors)
                if env_optional("SAILPOINT_MODE", "isc").lower() == "isc":
                    require("SAILPOINT_BASE_URL", errors)
                    require("SAILPOINT_CLIENT_ID", errors)
                    require("SAILPOINT_CLIENT_SECRET", errors)
                    require("SAILPOINT_ACCESS_ITEM_ID", errors)
                else:
                    require("SAILPOINT_IIQ_URL", errors)
                    if not env_optional("SAILPOINT_IIQ_TOKEN"):
                        require("SAILPOINT_IIQ_USERNAME", errors)
                        require("SAILPOINT_IIQ_PASSWORD", errors)

        if provision or publish:
            require("AZURE_TENANT_ID", errors)
            if surface == "copilot-studio":
                require("POWER_PLATFORM_ENVIRONMENT_ID", errors)
                require("AZURE_CLIENT_ID", errors)
                require("AZURE_CLIENT_SECRET", errors)
                check_solution_zip(warnings, errors)
                if publish:
                    check_pac(warnings, errors)
                    if not (env_optional("AGENT_SCHEMA_NAME") or env_optional("AGENT_BOT_ID")):
                        errors.append(
                            "publishing a Copilot Studio agent needs AGENT_SCHEMA_NAME or AGENT_BOT_ID"
                        )
            else:
                require("DECLARATIVE_AGENT_PACKAGE", errors)
                warnings.append(
                    "Graph app-catalog publish supports delegated permissions only - an "
                    "unattended service principal cannot publish a declarative agent"
                )

        if sync:
            require("INTEGRATION_ID", errors)

    if operation == "destroy" and provision:
        require("AZURE_TENANT_ID", errors)
        require("AZURE_CLIENT_ID", errors)
        require("AZURE_CLIENT_SECRET", errors)

    poll_s = env_optional("POLL_SECONDS", "5")
    if not poll_s.isdigit() or int(poll_s) < 1:
        errors.append(f"invalid POLL_SECONDS={poll_s!r} (must be a positive integer)")

    for msg in warnings:
        log_stderr(f"warning: {msg}")
        print(f"::warning::{msg}", file=sys.stderr)

    if errors:
        for msg in errors:
            log_stderr(f"error: {msg}")
            print(f"::error::{msg}", file=sys.stderr)
        return 1

    log_stderr("preflight ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
