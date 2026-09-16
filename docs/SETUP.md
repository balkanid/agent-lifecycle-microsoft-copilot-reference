# Preparing a tenant and toolchain

Written for macOS. The Linux steps are the same without Homebrew.

The tooling is straightforward. The prerequisite that takes time is a tenant with
Copilot Studio licensing, a Dataverse environment, and enough permission to create app
registrations. A personal Microsoft account is not sufficient.

Stage 1 of [`EVALUATION.md`](EVALUATION.md) needs none of this. Stages 2 and 3 need
sections 2 to 4 below: the tooling, the service principal, and the Dataverse application
user. Stage 4 needs all of it.

## 1. A tenant

If you do not have a non-production tenant with Copilot licensing, the
[Microsoft 365 Developer Program](https://developer.microsoft.com/microsoft-365/dev-program)
offers a sandbox tenant. Eligibility and the terms have changed more than once, so check
the current requirements before planning around it.

Copilot Studio is a separate license. Start the Copilot Studio trial from
[copilotstudio.microsoft.com](https://copilotstudio.microsoft.com) inside that tenant.

What you need to end up holding, in the tenant:

- Global Administrator, or at minimum Power Platform Administrator plus AI Administrator
- A Power Platform environment with Dataverse provisioned
- Copilot Studio enabled in that environment

Microsoft 365 Copilot licensing is a separate purchase again, and is only needed for the
declarative agent path. The Copilot Studio path does not require it.

## 2. Tooling

```bash
brew install --cask dotnet-sdk
brew install node python@3.11 terraform azure-cli

# Power Platform CLI
dotnet tool install --global Microsoft.PowerApps.CLI.Tool
echo 'export PATH="$PATH:$HOME/.dotnet/tools"' >> ~/.zshrc
exec zsh
pac --version

# Microsoft 365 Agents Toolkit, only for declarative agents
npm install -g @microsoft/m365agentstoolkit-cli
atk --version
```

Sign in:

```bash
az login --tenant <tenant-id>
pac auth create --name eval --environment https://yourorg.crm.dynamics.com
pac org list
```

If `pac org list` returns your environment, the Copilot Studio path will work.

## 3. The service principal

Terraform needs a service principal, and Power Platform will not accept an ordinary one.
It has to be registered as a management application first, which is a PowerShell step
with no portal equivalent.

```bash
az ad app create --display-name balkanid-agent-lifecycle
az ad sp create --id <app-id>
az ad app credential reset --id <app-id> --years 1
```

Then, from PowerShell (works on macOS via `pwsh`):

```powershell
Install-Module -Name Microsoft.PowerApps.Administration.PowerShell -Scope CurrentUser
Add-PowerAppsAccount
New-PowerAppManagementApp -ApplicationId <app-id>
```

Grant the Graph application permissions from
[`azure/PERMISSIONS.md`](../azure/PERMISSIONS.md) and admin-consent them:

```bash
az ad app permission add --id <app-id> \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions 1bfefb4e-e0b5-418b-a88f-73c46d2cc8e9=Role
az ad app permission admin-consent --id <app-id>
```

Finally, add the service principal as a Dataverse application user in the target
environment. Power Platform admin center, Environments, your environment, Settings,
Users and permissions, Application users, New app user. Give it System Administrator for
evaluation, and something narrower for anything beyond it.

## 4. Verify

```bash
cp env.example .env
# fill in AZURE_*, POWER_PLATFORM_ENVIRONMENT_ID, DATAVERSE_ORG_URL, BalkanID keys

set -a && source .env && set +a
python3 scripts/preflight.py
python3 scripts/discover_agents.py
```

`discover_agents.py` succeeding against a real tenant confirms all three authentication
paths work. It is the fastest end-to-end check, and it is read-only.

## 5. Create test agents

To exercise discovery and reconciliation you need agents to find. Create two or three in
the Copilot Studio portal using an ordinary maker account rather than an administrator.

Doing this by hand is itself informative: it takes well under a minute, requires no
elevated role, and produces no authorization record anywhere. That is the gap the gate
closes.

Then pull one into source control so you have a solution to import:

```bash
pac copilot list --environment $POWER_PLATFORM_ENVIRONMENT_ID
pac copilot clone --bot <schemaname> --output-dir ./copilot-studio/src
pac copilot pack \
  --publisher-prefix bkn \
  --solution-name BalkanIDAgentLifecycle \
  --project-dir ./copilot-studio/src \
  --output-path ./build/BalkanIDAgentLifecycle.zip

# pac copilot pack emits an UNMANAGED solution, and there is no offline conversion.
# Import it, then export it back as managed. See copilot-studio/README.md.
pac solution import --path ./build/BalkanIDAgentLifecycle.zip --publish-changes
pac solution export --name BalkanIDAgentLifecycle \
  --path ./build/BalkanIDAgentLifecycle_managed.zip --managed true
```

## Known friction

- Dataverse provisioning on a new environment takes 10 to 20 minutes. Start it before
  you need it.
- `New-PowerAppManagementApp` has no portal equivalent and no az CLI equivalent. It is
  PowerShell or nothing.
- A Developer Program tenant expires. Renew it before you need it.
- Copilot Studio trial licensing sometimes takes an hour to propagate to a new
  environment. If the portal says the feature is unavailable, wait rather than
  reprovisioning.
