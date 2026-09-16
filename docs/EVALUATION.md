# Evaluation guide

A path through this reference that proves the pattern in your own tenant, in order of
increasing commitment. Each stage stands on its own, so you can stop at any point and
still have learned something useful.

Expect stage 1 to take about twenty minutes and stage 4 most of a day, largely because
Dataverse provisioning is slow.

| Stage | What it proves | Microsoft credentials needed |
|---|---|---|
| 1. Gate only | An approval can block agent creation and fail closed | None |
| 2. Inventory | How many Copilot agents exist in your tenant, and who owns them | Read only |
| 3. Reconciliation | Which of those agents has no approval on record | Read only |
| 4. Provisioning | The whole lifecycle, approval through to a live agent | Write |

If you are evaluating against a checklist, [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
describes the design and [`azure/PERMISSIONS.md`](../azure/PERMISSIONS.md) lists exactly
what each stage requires.

## Before you start

```bash
git clone https://github.com/balkanid/agent-lifecycle-copilot-reference.git
cd agent-lifecycle-copilot-reference
cp env.example .env
```

Python 3.11 or newer and a POSIX shell are the only dependencies for stages 1 to 3. The
scripts use the standard library only, so there is nothing to install and nothing to
audit beyond the files in this repository. On Windows, use WSL: the wrapper script and
the Terraform provisioners invoke `/bin/bash`.

## Stage 1: prove the gate

Fill in these values in `.env`:

```
BALKANID_PUBLIC_API_URL=https://your-tenant.balkanid.app/api/public
API_KEY_ID=
API_KEY_SECRET=
BALKANID_AGENT_OWNER_EMAIL=
AGENT_NAME=example-trial-intake-agent

# Optional here, and both appear on the request when set
INTEGRATION_ID=
POWER_PLATFORM_ENVIRONMENT_ID=
```

`gate.py` needs only the first five. `INTEGRATION_ID` becomes mandatory from stage 2
onward, and `preflight.py` treats it as required, so set it now if you have it.

Run the gate:

```bash
set -a && source .env && set +a
python3 scripts/gate.py
```

The script creates an access request and blocks. In BalkanID, the request appears in the
approver's queue showing the agent name and the requesting owner, plus the target
environment and integration when those are set.

Approve it. Expect output close to:

```
waiting on AGENT_ACCESS request_id=...
AGENT_ACCESS: status='APPROVED' approval='APPROVED' provisioning=None requestType='AGENT_ACCESS'
{"request_id": "...", "status": "approved", "surface": "copilot-studio", "agent": "example-trial-intake-agent"}
recorded approved scope in .approved-scope.json
```

Exit code 0.

Now run it again and deny it. Expect:

```
AGENT_ACCESS denied: request_id=...
{"request_id": "...", "status": "denied"}
```

Exit code 1.

Run it a third time and let the approval window elapse without acting
(`POLL_TIMEOUT_SECONDS=60` makes this quick). Expect exit code 2 and
`{"status": "timeout"}`.

For completeness, point `BALKANID_PUBLIC_API_URL` at an unreachable host. Expect exit
code 3 and `{"status": "unreachable"}`.

Four distinct exit codes, because a pipeline needs to tell them apart:

| Code | Meaning |
|---|---|
| 0 | Approved |
| 1 | Denied, or partially approved |
| 2 | The approval window elapsed |
| 3 | The policy service could not be reached |

Only 0 continues, so an unavailable policy service never implies consent. This is the
whole preventive control, and it works before any Microsoft credential has been issued.

A partial approval is deliberately treated as a denial. If the approver grants less than
was requested, provisioning the full requested scope would create an agent nobody
authorised.

To see it blocking a Terraform apply rather than a bare script:

```bash
cd terraform
terraform init
terraform apply \
  -var="api_key_id=$API_KEY_ID" \
  -var="api_key_secret=$API_KEY_SECRET" \
  -var="agent_owner_email=$BALKANID_AGENT_OWNER_EMAIL" \
  -var="agent_name=evaluation-agent"
```

The apply blocks on the approval and fails on a denial.

## Stage 2: inventory your tenant

This is read-only. It answers a question most tenants cannot answer from the admin
centre alone: how many Copilot agents exist, across every environment and every
creation path.

Add to `.env`:

```
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
DATAVERSE_ORG_URL=https://yourorg.crm.dynamics.com
POWER_PLATFORM_ENVIRONMENT_ID=
```

The service principal needs read access only, but it does need to exist and to be
registered with Power Platform. Sections 3 and 4 of [`docs/SETUP.md`](SETUP.md) cover
that, including the Dataverse application user, and
[`azure/PERMISSIONS.md`](../azure/PERMISSIONS.md) lists the roles.

```bash
python3 scripts/discover_agents.py
```

The script reads three inventory planes that do not reference each other, merges rows
describing the same agent, and prints JSON plus a summary:

```
discovered=N governed=0 ungoverned=N
```

At this stage everything is ungoverned, because nothing has an approval yet. The number
that matters is `discovered`. Compare it against what your identity team believed was
running.

Check `complete` in the JSON output before trusting the count. When any inventory plane
fails to read, the script sets `complete: false`, lists the failures under
`plane_errors`, and does not report a clean tenant.

The case to watch for: the Graph package catalog returns an empty result rather than an
error when the tenant lacks Agent 365 licensing or the caller lacks the AI Administrator
role. The script flags a zero-row response from that plane explicitly, because otherwise
missing declarative agents look identical to having none.

The Dataverse query filters to active, published agents. Retired and unpublished
solution components are excluded, so the count reflects agents that actually run.

## Stage 3: reconcile

Approve one request for an agent that already exists in your tenant, using the exact
name it carries in Copilot Studio:

```bash
AGENT_NAME="<name of a real agent>" python3 scripts/gate.py
```

Approve it, then re-run discovery:

```bash
python3 scripts/discover_agents.py
```

That agent now reports `governed=true`, and the counts move. Reconciliation matches on
agent name, case-insensitively. It does not currently scope the match by environment, so
two agents sharing a name in different environments are treated as one; if that matters
in your tenant, key the match on the Dataverse bot id instead.

For a scheduled job, `FAIL_ON_UNGOVERNED=true` turns the script into a monitor:

| Exit | Meaning |
|---|---|
| 0 | Every discovered agent has an approval |
| 1 | Agents exist with no approval |
| 3 | The inventory is incomplete, so no conclusion can be drawn |

Exit 3 matters. A revoked credential would otherwise produce `discovered=0 ungoverned=0`
and a green monitor over a check that saw nothing.

The output of this stage is a work item list: for each ungoverned agent, either assign an
owner and record a retrospective approval, or decommission it.

## Stage 4: provision through the pipeline

This stage writes to your tenant. Use a non-production environment.

Prerequisites are covered in [`docs/SETUP.md`](SETUP.md). The two that catch people out:

- The service principal must be registered as a Power Platform management application
  with `New-PowerAppManagementApp`. There is no portal or Azure CLI equivalent.
- The solution package Terraform imports must be managed. `pac copilot pack` emits
  unmanaged, and no offline conversion exists. See
  [`copilot-studio/README.md`](../copilot-studio/README.md).

Then:

```bash
./scripts/terraform-local.sh apply-lifecycle
```

Two flags control how much of the lifecycle runs, and both are off in `env.example` so
that a first run needs fewer Microsoft permissions. For the full two-gate flow described
below, set them:

```
ENTRA_IDENTITY_GATE=true
PROVISION_AGENT_IDENTITY=true
```

The sequence, each step visible in the output:

1. Identity gate. An approver reviews the Entra identity and the Graph permissions the
   agent is requesting, before the app registration exists. Requires
   `ENTRA_IDENTITY_GATE=true`.
2. Agent gate. An approver reviews the agent, its owner and its target environment.
3. Scope verification. `verify_approved_scope.py` confirms the environment still matches
   what was approved. Change `AGENT_NAME` or add a Graph permission between the gate and
   this step and it refuses to continue.
4. Terraform creates the app registration and admin-consents only the approved
   permissions. Requires `PROVISION_AGENT_IDENTITY=true`.
5. Terraform applies managed environment guardrails, binds the agent identity to the
   environment, and imports the solution. This stack also invokes
   `publish_copilot_agent.py`, which runs `pac copilot publish` and makes the agent live.
   It is skipped silently unless `AGENT_SCHEMA_NAME` is set, which is the most common
   reason an agent imports but never responds.
6. `syncIntegration` causes BalkanID to discover the agent and link it to its owner.

Verify the result in three places:

| Check | Where | Expected |
|---|---|---|
| The agent exists and responds | Copilot Studio | Published, in the target environment |
| The identity holds only approved permissions | Entra ID, API permissions | Exactly the list from the identity request, nothing more |
| The agent is governed | BalkanID | Agent linked to owner, identity, and both approval records |

Then test the negative cases, which are the ones worth showing a security reviewer.

Denial:

```bash
AGENT_NAME=denied-agent ./scripts/terraform-local.sh apply-lifecycle
```

Deny the first gate that appears. Confirm no app registration was created and no solution
was imported. Nothing after the denied gate executed.

Scope drift, which is subtler and more interesting:

```bash
./scripts/terraform-local.sh gate                       # approve this
ENTRA_GRAPH_PERMISSIONS="$ENTRA_GRAPH_PERMISSIONS,Directory.ReadWrite.All"   python3 scripts/verify_approved_scope.py
```

Expect exit 1 and `Graph permissions not approved: Directory.ReadWrite.All`. This is what
stops an approval record describing something other than what was built.

Teardown:

```bash
./scripts/terraform-local.sh destroy-lifecycle
```

## What to look at in the code

If you are reviewing rather than running, these are the files that carry the design:

| File | Why it matters |
|---|---|
| [`scripts/gate.py`](../scripts/gate.py) | The control point. About 150 lines, standard library only |
| [`scripts/verify_approved_scope.py`](../scripts/verify_approved_scope.py) | Binds the approval record to what is actually provisioned |
| [`scripts/balkanid_api.py`](../scripts/balkanid_api.py) | Every API call made to BalkanID, with retry and poll semantics |
| [`scripts/discover_agents.py`](../scripts/discover_agents.py) | The three Microsoft inventory planes and how they are merged |
| [`terraform/copilot-studio/main.tf`](../terraform/copilot-studio/main.tf) | Environment guardrails and solution import |
| [`terraform/entra-agent-identity/main.tf`](../terraform/entra-agent-identity/main.tf) | Agent identity and permission consent |

No script makes a network call other than to your BalkanID tenant, Microsoft endpoints,
and the SailPoint endpoint if you configure that gate. There is no telemetry.

## Common stopping points

| Symptom | Cause |
|---|---|
| Gate times out | `POLL_TIMEOUT_SECONDS` too low, or the request was routed to an approver who is not you |
| Discovery finds no declarative agents | Agent 365 licensing absent; Copilot Studio agents are still reported |
| Discovery finds no agents at all | The service principal lacks Power Platform Administrator, or the token was requested for the wrong scope |
| `pac` authentication fails | The service principal is not registered as a management application |
| Solution import rejected | The package is unmanaged |
| Agent imported but does not respond | It was never published |

[`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) has the full list.

## Questions this reference does not answer

Worth raising with your BalkanID contact rather than inferring from the code:

- Recertification of agents and their owners on a schedule.
- `ASSIGN`, `UNASSIGN` and `DELETE` lifecycle actions. This reference implements
  `CREATE`.
- Webhook-driven pipeline resume for approvals that take days rather than minutes.
- How agent identities appear in access reviews alongside human identities.
