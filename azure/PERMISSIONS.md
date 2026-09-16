# Permissions

Three separate permission surfaces, because Microsoft splits Copilot across three
control planes that do not share an authorization model. Grant the minimum for the
paths you actually run.

## 1. The pipeline service principal (Power Platform)

Used by: `terraform/copilot-studio`, `pac copilot publish`, the Inventory API call in
`scripts/discover_agents.py`.

| Requirement | How to grant |
|---|---|
| Registered as a Power Platform management app | `New-PowerAppManagementApp -ApplicationId <client_id>`, or the `powerplatform_admin_management_application` resource |
| Power Platform Administrator or Dynamics 365 Administrator | Entra directory role |
| Dataverse application user in the target environment, with a security role | `powerplatform_environment_application_admin`, or Power Platform admin center → Environment → S2S apps |

Known gap: `powerplatform_copilot_studio_application_insights` does not support
service principal auth. It is left disabled in this repo for that reason. Use an
interactive `az login` session if you need it.

## 2. The pipeline service principal (Microsoft Graph)

Application permissions, admin-consented:

| Permission | Needed for |
|---|---|
| `Application.ReadWrite.All` | `terraform/entra-agent-identity` creates the agent's app registration |
| `AppRoleAssignment.ReadWrite.All` | admin-consenting the agent's own Graph permissions |
| `CopilotPackages.Read.All` | `scripts/discover_agents.py` reads the tenant agent catalog |
| `AuditLogsQuery-Entra.Read.All` | optional - correlating agent creation events in Purview |

`CopilotPackages.Read.All` additionally requires the caller to hold AI Administrator
or Global Administrator, and the tenant to have an Agent 365 license. Without
that license the package catalog returns empty rather than erroring, so discovery will
silently under-report. `scripts/discover_agents.py` logs a warning in that case.

## 3. The human publishing a declarative agent

This one cannot be delegated to a service principal, and it is worth understanding
before promising an unattended pipeline.

`POST /appCatalogs/teamsApps` supports delegated permissions only. Application
permissions are not supported. So a declarative agent is always published by a signed-in
human holding:

| Permission | Effect |
|---|---|
| `AppCatalog.Submit` | submits the app for administrator review |
| `AppCatalog.ReadWrite.All` | publishes directly to the org catalog |

`scripts/publish_declarative_agent.py` requests the scope matching
`AGENT_REQUIRES_REVIEW`: `AppCatalog.Submit` when submitting for review,
`AppCatalog.ReadWrite.All` when publishing directly.

plus the Global Administrator role for direct publish.

`scripts/publish_declarative_agent.py` therefore uses the device-code flow by default.

## 4. Entra Agent ID - what you cannot automate yet

Copilot Studio agents created from May 2026 are issued an Entra Agent ID
automatically by the platform. That identity is a `servicePrincipal` of subtype Agent,
instantiated from an `agentIdentityBlueprint` application.

There is no Terraform support for either object. `hashicorp/azuread` has no
`azuread_agent_identity` resource, and the blueprint is created by POSTing to
`/applications` with an `@odata.type` discriminator that `azuread_application` cannot
emit. Creating one means calling Graph directly:

```
POST /v1.0/applications/
  { "@odata.type": "Microsoft.Graph.AgentIdentityBlueprint", "displayName": "...",
    "sponsors@odata.bind": [...], "owners@odata.bind": [...] }

POST /beta/serviceprincipals/Microsoft.Graph.AgentIdentity
  { "displayName": "...", "agentIdentityBlueprintId": "..." }
```

Directory roles: AI Reader to view, Agent ID Administrator to manage. Applying
Conditional Access or identity governance to agent identities requires Entra Agent ID
licensing; viewing does not.

This repository manages the classic app registration path instead, which is what
Terraform can do today and what pre-May-2026 agents and API-plugin declarative agents
actually use. For platform-issued Agent IDs, run the identity gate for review and
reconcile the resulting identity through the BalkanID Entra integration.

## 5. SailPoint (optional)

| Mode | Requirement |
|---|---|
| ISC | OAuth client with `sp:scopes:default`; the client's identity needs rights to submit access requests on behalf of others |
| IdentityIQ | A service account with rights to launch the configured LCM workflow over `/identityiq/rest/workflows` |

## References

- [Power Platform Terraform provider](https://registry.terraform.io/providers/microsoft/power-platform/latest)
- [Power Platform Inventory API](https://learn.microsoft.com/en-us/power-platform/admin/inventory-api)
- [Publish teamsApp (Graph)](https://learn.microsoft.com/en-us/graph/api/teamsapp-publish?view=graph-rest-1.0)
- [Copilot package catalog (Graph)](https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/api/admin-settings/package/overview)
- [Entra Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/agent-identities)
- [Copilot Studio agent identities](https://learn.microsoft.com/en-us/microsoft-copilot-studio/govern-agents-identities-overview)
