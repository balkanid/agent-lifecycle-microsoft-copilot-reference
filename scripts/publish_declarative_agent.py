#!/usr/bin/env python3
"""Publish a Microsoft 365 declarative agent package to the org app catalog.

POST https://graph.microsoft.com/v1.0/appCatalogs/teamsApps  (Content-Type: application/zip)

Important constraint, and the reason this script exists separately from the Terraform
path: Graph supports delegated permissions only for app-catalog publishing
(AppCatalog.Submit or AppCatalog.ReadWrite.All). Application permissions are not
supported, so an unattended service principal cannot publish a declarative agent. This
script therefore uses the device-code flow by default, or accepts a token you already
hold via GRAPH_ACCESS_TOKEN.

This is a platform constraint to design around rather than work around: a pipeline can
build and validate the package, but a signed-in administrator publishes it. Plan the
approval before the packaging step, since that is the last point a pipeline controls.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from balkanid_api import env, env_optional, load_dotenv, log_stderr

LOGIN_HOST = "https://login.microsoftonline.com"
GRAPH_API = "https://graph.microsoft.com/v1.0"
# Request the narrower scope when the package is only being submitted for review.
SCOPE_PUBLISH = "AppCatalog.ReadWrite.All offline_access"
SCOPE_SUBMIT = "AppCatalog.Submit offline_access"
# Microsoft's first-party "Microsoft Graph Command Line Tools" client, which is
# pre-consented in most tenants. Override with AZURE_PUBLIC_CLIENT_ID to use your own.
DEFAULT_PUBLIC_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"


def device_code_token(scope: str) -> str:
    tenant = env("AZURE_TENANT_ID")
    client_id = env_optional("AZURE_PUBLIC_CLIENT_ID", DEFAULT_PUBLIC_CLIENT_ID)

    body = urllib.parse.urlencode({"client_id": client_id, "scope": scope}).encode()
    req = urllib.request.Request(
        f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/devicecode",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        flow = json.loads(resp.read().decode())

    log_stderr("")
    log_stderr(flow.get("message", "complete the device code sign-in in your browser"))
    log_stderr("")

    interval = int(flow.get("interval", 5))
    deadline = time.time() + int(flow.get("expires_in", 900))
    poll_body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": flow["device_code"],
        }
    ).encode()

    while time.time() < deadline:
        time.sleep(interval)
        poll = urllib.request.Request(
            f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/token",
            data=poll_body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(poll, timeout=60) as resp:
                return json.loads(resp.read().decode())["access_token"]
        except urllib.error.HTTPError as err:
            detail = json.loads(err.read().decode() or "{}") if err.fp else {}
            error = detail.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise SystemExit(f"device code sign-in failed: {error}: {detail.get('error_description', '')}")

    raise SystemExit("device code sign-in timed out")


def graph_token(*, requires_review: bool) -> str:
    existing = env_optional("GRAPH_ACCESS_TOKEN")
    if existing:
        log_stderr("using GRAPH_ACCESS_TOKEN from env")
        return existing
    scope = SCOPE_SUBMIT if requires_review else SCOPE_PUBLISH
    log_stderr(f"requesting delegated scope: {scope}")
    return device_code_token(scope)


def publish(package_path: str, token: str, *, requires_review: bool) -> dict:
    if not os.path.isfile(package_path):
        raise SystemExit(f"app package not found: {package_path}")

    with open(package_path, "rb") as fh:
        payload = fh.read()

    url = f"{GRAPH_API}/appCatalogs/teamsApps"
    if requires_review:
        url += "?requiresReview=true"

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/zip"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        detail = err.read().decode() if err.fp else ""
        raise SystemExit(f"publish failed: HTTP {err.code} {detail[:800]}") from err


def main() -> int:
    load_dotenv()
    package_path = env_optional(
        "DECLARATIVE_AGENT_PACKAGE",
        "declarative-agent/appPackage/build/appPackage.zip",
    )
    requires_review = env_optional("AGENT_REQUIRES_REVIEW", "false").lower() in ("1", "true", "yes")

    # Fail before the device-code sign-in rather than after it. Nothing is more annoying
    # than completing a browser flow only to be told the file is missing.
    if not os.path.isfile(package_path):
        raise SystemExit(
            f"app package not found: {package_path}\n"
            "Build it first with `atk package`, or set DECLARATIVE_AGENT_PACKAGE."
        )

    token = graph_token(requires_review=requires_review)
    result = publish(package_path, token, requires_review=requires_review)

    out = {
        "teams_app_id": result.get("id", ""),
        "external_id": result.get("externalId", ""),
        "display_name": result.get("displayName", ""),
        "distribution_method": result.get("distributionMethod", ""),
        "requires_review": requires_review,
    }
    print(json.dumps(out, indent=2))
    log_stderr(f"published teamsAppId={out['teams_app_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
