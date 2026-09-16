#!/usr/bin/env bash
# Local wrapper: sources .env, runs the gates in order, then the Terraform stacks.
#
#   ./scripts/terraform-local.sh gate                gate only, no Microsoft credentials
#   ./scripts/terraform-local.sh apply-lifecycle     identity gate + agent gate + provision
#   ./scripts/terraform-local.sh apply-declarative   gate + package + publish to the app catalog
#   ./scripts/terraform-local.sh discover            inventory agents, flag ungoverned ones
#   ./scripts/terraform-local.sh destroy-lifecycle   tear the stacks down
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"

if [[ ! -f "$root/.env" ]]; then
  echo "no .env found - copy env.example to .env and fill it in" >&2
  exit 1
fi

# Load .env WITHOUT clobbering anything the caller set inline, so that
# `GATE_BACKEND=sailpoint ./scripts/terraform-local.sh gate` does what it says.
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
  key="${line%%=*}"
  key="${key// /}"
  [[ -z "$key" ]] && continue
  if [[ -z "${!key:-}" ]]; then
    value="${line#*=}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    export "$key=$value"
  fi
done < "$root/.env"

abspath() {
  local path="$1"
  [[ "$path" == /* ]] && { echo "$path"; return; }
  echo "$root/${path#./}"
}

if [[ -n "${SOLUTION_ZIP_PATH:-}" ]]; then
  SOLUTION_ZIP_PATH="$(abspath "$SOLUTION_ZIP_PATH")"
  export SOLUTION_ZIP_PATH
fi
if [[ -n "${DECLARATIVE_AGENT_PACKAGE:-}" ]]; then
  DECLARATIVE_AGENT_PACKAGE="$(abspath "$DECLARATIVE_AGENT_PACKAGE")"
  export DECLARATIVE_AGENT_PACKAGE
fi

gate_script="gate.py"
if [[ "${GATE_BACKEND:-balkanid}" == "sailpoint" ]]; then
  gate_script="sailpoint_gate.py"
  echo "==> gate backend: SailPoint (${SAILPOINT_MODE:-isc})" >&2
fi

run_gates() {
  # Start from a clean record so a previous run's approval cannot satisfy this one.
  rm -f "$root/${APPROVED_SCOPE_FILE:-.approved-scope.json}"

  if [[ "${ENTRA_IDENTITY_GATE:-false}" == "true" ]]; then
    if [[ "${GATE_BACKEND:-balkanid}" == "sailpoint" ]]; then
      echo "==> Entra identity gate skipped (SailPoint backend gates the agent only)" >&2
    else
      echo "==> Entra identity gate" >&2
      python3 "$root/scripts/entra_agent_identity_gate.py"
    fi
  fi

  if [[ "${AGENT_GATE:-true}" == "true" ]]; then
    echo "==> Agent access gate" >&2
    python3 "$root/scripts/$gate_script"
  fi
}

graph_permissions_hcl() {
  # Turn "Sites.Selected,User.Read.All" into ["Sites.Selected","User.Read.All"].
  python3 -c 'import os, json; print(json.dumps([p.strip() for p in os.environ.get("ENTRA_GRAPH_PERMISSIONS", "").split(",") if p.strip()]))'
}

verify_scope() {
  if [[ "${VERIFY_APPROVED_SCOPE:-true}" != "true" ]]; then
    echo "==> Approved-scope verification disabled" >&2
    return 0
  fi
  echo "==> Verifying the environment matches what was approved" >&2
  python3 "$root/scripts/verify_approved_scope.py"
}

terraform_identity() {
  echo "==> Terraform: Entra agent identity" >&2
  local perms
  perms="$(graph_permissions_hcl)"
  terraform -chdir="$root/terraform/entra-agent-identity" init -input=false
  terraform -chdir="$root/terraform/entra-agent-identity" "$1" -auto-approve \
    -var="tenant_id=${AZURE_TENANT_ID}" \
    -var="client_id=${AZURE_CLIENT_ID}" \
    -var="client_secret=${AZURE_CLIENT_SECRET}" \
    -var="agent_name=${AGENT_NAME:-example-trial-intake-agent}" \
    -var="graph_application_permissions=${perms}"
}

terraform_copilot_studio() {
  echo "==> Terraform: Copilot Studio stack" >&2
  local module="$root/terraform/copilot-studio"
  terraform -chdir="$module" init -input=false

  local args=(
    -var="tenant_id=${AZURE_TENANT_ID}"
    -var="client_id=${AZURE_CLIENT_ID}"
    -var="client_secret=${AZURE_CLIENT_SECRET}"
    -var="environment_id=${POWER_PLATFORM_ENVIRONMENT_ID}"
    -var="solution_unique_name=${SOLUTION_UNIQUE_NAME:-BalkanIDAgentLifecycle}"
    -var="solution_version=${SOLUTION_VERSION:-1.0.0.0}"
    -var="agent_schema_name=${AGENT_SCHEMA_NAME:-}"
    -var="publish_agent=${PUBLISH_AGENT:-true}"
  )
  [[ -n "${SOLUTION_ZIP_PATH:-}" ]] && args+=(-var="solution_zip_path=${SOLUTION_ZIP_PATH}")
  [[ -n "${AGENT_APP_ID:-}" ]] && args+=(-var="agent_application_id=${AGENT_APP_ID}")

  terraform -chdir="$module" "$1" -auto-approve "${args[@]}"
}

sync_balkanid() {
  if [[ "${TRIGGER_INTEGRATION_SYNC:-true}" == "true" ]]; then
    echo "==> BalkanID integration sync" >&2
    python3 "$root/scripts/trigger_sync.py"
  fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
  gate)
    run_gates
    echo "==> approved" >&2
    ;;

  apply-lifecycle)
    python3 "$root/scripts/preflight.py"
    run_gates
    verify_scope

    if [[ "${PROVISION_AGENT_IDENTITY:-false}" == "true" ]]; then
      terraform_identity apply
    fi

    # Recover the agent's app id from the identity stack whenever that state exists,
    # not only on the run that created it. Otherwise a second apply-lifecycle drops
    # powerplatform_environment_application_admin and quietly unbinds the identity
    # that was approved.
    if [[ -z "${AGENT_APP_ID:-}" ]]; then
      AGENT_APP_ID="$(terraform -chdir="$root/terraform/entra-agent-identity" output -raw application_id 2>/dev/null || true)"
      export AGENT_APP_ID
    fi
    [[ -n "${AGENT_APP_ID:-}" ]] && echo "==> agent identity app_id=${AGENT_APP_ID}" >&2

    terraform_copilot_studio apply
    sync_balkanid
    ;;

  apply-declarative)
    COPILOT_SURFACE=declarative-agent
    export COPILOT_SURFACE
    python3 "$root/scripts/preflight.py"
    run_gates
    verify_scope

    echo "==> Packaging declarative agent" >&2
    pkg_dir="$root/declarative-agent/appPackage"
    mkdir -p "$pkg_dir/build"

    if command -v atk >/dev/null 2>&1; then
      (cd "$root/declarative-agent" && atk package --env "${TEAMSFX_ENV:-dev}")
      # atk names the artefact appPackage.<env>.zip
      built="$pkg_dir/build/appPackage.${TEAMSFX_ENV:-dev}.zip"
    else
      echo "atk not found - packaging the appPackage folder directly" >&2
      # manifest.json carries ${{TEAMS_APP_ID}}; Graph rejects the package unless it is
      # substituted. atk does this itself.
      if [[ -z "${TEAMS_APP_ID:-}" ]]; then
        echo "TEAMS_APP_ID must be set to package without atk (manifest.json templates it)" >&2
        exit 1
      fi
      staging="$(mktemp -d)"
      trap 'rm -rf "$staging"' EXIT
      cp "$pkg_dir"/{declarativeAgent.json,instruction.txt,color.png,outline.png} "$staging/"
      sed "s/\${{TEAMS_APP_ID}}/${TEAMS_APP_ID}/g" "$pkg_dir/manifest.json" > "$staging/manifest.json"
      built="$pkg_dir/build/appPackage.zip"
      (cd "$staging" && zip -q -r "$built" manifest.json declarativeAgent.json instruction.txt color.png outline.png)
    fi

    echo "==> Publishing to the org app catalog" >&2
    DECLARATIVE_AGENT_PACKAGE="$built" python3 "$root/scripts/publish_declarative_agent.py"
    sync_balkanid
    ;;

  discover)
    python3 "$root/scripts/discover_agents.py"
    ;;

  destroy-lifecycle)
    CD_OPERATION=destroy python3 "$root/scripts/preflight.py"
    terraform_copilot_studio destroy
    if [[ "${PROVISION_AGENT_IDENTITY:-false}" == "true" ]]; then
      terraform_identity destroy
    fi
    ;;

  *)
    cat >&2 <<'USAGE'
usage: ./scripts/terraform-local.sh <command>

  gate                gate only - no Microsoft credentials needed
  apply-lifecycle     identity gate + agent gate + Copilot Studio provisioning
  apply-declarative   gate + package + publish a declarative agent
  discover            inventory Copilot agents and flag ungoverned ones
  destroy-lifecycle   tear the Terraform stacks down
USAGE
    exit 1
    ;;
esac
