# Troubleshooting

Grouped by where the failure occurs.

## Gates

### `missing required env BALKANID_AGENT_OWNER_EMAIL`

The gate needs an owner that exists in your BalkanID tenant. It is not optional, because
an agent without an accountable human is the thing this repository exists to prevent.

### Gate times out

The default local timeout is 900 seconds. In CD it is `APPROVAL_WAIT_MINUTES`, default
120. If approvals in your organisation take days, do not raise the timeout. Subscribe a
webhook destination to `request.actioned` and resume the pipeline from that instead of
holding a runner open.

### `HTTP 403` with an HTML body

`BALKANID_PUBLIC_API_URL` is wrong, or the API key lacks Public API access. An HTML body
means a proxy or WAF answered, not the API.

### `step-up MFA required on createRequest`

Complete MFA in the BalkanID UI and re-run. An API key alone cannot satisfy step-up.

## Power Platform

### `pac` auth fails during `terraform apply`

The service principal is not registered as a Power Platform management application.
There is no portal or az CLI equivalent for this step:

```powershell
New-PowerAppManagementApp -ApplicationId <client_id>
```

### Solution import fails with a component error

Usually one of:

- A custom connector the solution depends on has not been imported yet. Connectors
  first, then connection references, then the agent.
- A connection reference in the package is not bound.
  `powerplatform_managed_solution.connection_references` must bind every reference the
  package declares, not only the ones you use.
- The solution version is lower than the one already installed. Import is rejected
  before it starts.

### Import fails with "managed solution required"

`pac copilot pack` produces an unmanaged solution. `powerplatform_managed_solution`
only accepts managed, and the packaging tool cannot convert between the two. Import the
unmanaged solution into a Dataverse environment, then `pac solution export --managed
true`. See [`copilot-studio/README.md`](../copilot-studio/README.md).

### Solution export fails and gives no useful reason

A solution containing an agent whose topic names contain a `.` cannot be exported at
all. Rename the topic.

### The agent imported but does not respond

It was not published. An imported agent exists in Dataverse but is not live.

```bash
pac copilot publish --bot <schemaname> --environment <environment-id>
```

`terraform/copilot-studio` does this automatically when `publish_agent` is true and
`agent_schema_name` is set.

### Authentication settings or the agent icon did not survive the import

They do not travel with the solution. Neither does channel configuration. Reconfigure
after import. This is documented Microsoft behaviour, not a bug in the pipeline.

### `powerplatform_copilot_studio_application_insights` fails in CI

That resource does not support service principal authentication. It is disabled by
default here for exactly that reason. Use it from an interactive session or not at all.

## Declarative agents

### Publish returns 403 with a valid token

`POST /appCatalogs/teamsApps` supports delegated permissions only. A service principal
token will not work no matter which application permissions it holds. Use the device-code
flow in `scripts/publish_declarative_agent.py`, signed in as a Global Administrator.

### `atk share` fails on an agent with actions

Declarative agents with actions or API plugins cannot be shared. The scope must be
`personal`. This is a Copilot platform limitation, not a CLI one.

### Manifest validation fails on `$schema`

The declarative agent schema version in `$schema` and the `version` field must agree.
CI checks this. The current version is v1.8.

## Discovery

### `discover_agents.py` returns no declarative agents

The Graph package catalog requires an Agent 365 license on the tenant, plus AI
Administrator or Global Administrator. Without the license it returns empty rather than
erroring, which means discovery silently under-reports. The script logs a warning.

### Inventory API returns nothing

Check that the service principal holds Power Platform Administrator and that the token
was requested for the `https://api.powerplatform.com/.default` scope rather than Graph.

### Dataverse enumeration is skipped

`DATAVERSE_ORG_URL` is not set. Tenant-wide inventory still works without it; you lose
publish state and owner detail for Copilot Studio agents.

### An agent you approved still shows as ungoverned

Reconciliation matches on agent name, case-insensitively. If the name in the access
request does not match the name in Copilot Studio, it will not match. Prefer setting
`AGENT_NAME` from the same source that names the agent.

## SailPoint

### ISC returns 202 and the poll never finds the request

`/v3/access-requests` returns no body, so the script polls by requester plus item id. If
`SAILPOINT_ACCESS_ITEM_ID` does not match the item in the request, nothing will ever
match. Confirm the id in the ISC UI.

### IdentityIQ launch returns 403

The REST API is disabled, or the service account lacks the `WebServiceExecutor`
capability.

### IdentityIQ workflow completes immediately

The workflow launched but has no approval step, so it finished without waiting. Attach
an approval to the workflow, or the gate approves everything instantly, which is worse
than no gate.
