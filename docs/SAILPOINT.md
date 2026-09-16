# Raising the request in SailPoint

The gate in this reference is a process that exits 0 on approval and non-zero otherwise.
Nothing else in the repository depends on which system made the decision, so the gate can
be pointed at an existing IGA platform instead of BalkanID.

`scripts/sailpoint_gate.py` implements that for SailPoint. Set `GATE_BACKEND=sailpoint`
and the rest of the pipeline is unchanged.

This exists because many organisations already run an access request workflow with
established approvers, routing rules and audit practice, and introducing a second
approval queue for agents is a real cost. Reusing the queue that already exists is often
the right first step.

## What this gate does and does not carry across

`sailpoint_gate.py` submits an access request and blocks until it is decided. That is the
preventive control, and it behaves the same as `gate.py`: same exit codes, same
fail-closed semantics.

What it does not do is carry the agent's identity across. The script records the request
against an access item id and the agent name in `clientMetadata`. It does not create an
object in SailPoint representing the agent, so `discover_agents.py` has nothing to
correlate its tenant inventory against, and the reconciliation half of the architecture
has no counterpart on that side.

That is a property of this script rather than a limit of SailPoint. ISC can model
non-human identities, and many organisations already model service accounts there. If you
want reconciliation against SailPoint, the work is to represent each agent as an object
in your ISC model and extend this gate to write to it. The three planes in
[`scripts/discover_agents.py`](../scripts/discover_agents.py) supply the tenant-side
data either way.

Also not carried across: the two-gate split. There is no equivalent request type for the
agent's Entra identity in either SailPoint product, and mapping one onto an access item
would misrepresent what the approver reviewed. Under this backend only the agent gate
runs.

Both gates can run in the same pipeline, if you want the approval workflow your
organisation already trusts alongside agent-level inventory. They are independent
processes.

## Two products, two APIs

SailPoint's two products share a brand and little else at the API layer.

### Identity Security Cloud

```bash
SAILPOINT_MODE=isc
SAILPOINT_BASE_URL=https://acme.api.identitynow.com
SAILPOINT_CLIENT_ID=...
SAILPOINT_CLIENT_SECRET=...
SAILPOINT_ACCESS_ITEM_ID=...
SAILPOINT_ACCESS_ITEM_TYPE=ACCESS_PROFILE
```

Flow:

```
POST {tenant}/oauth/token          client_credentials
POST /v3/search                    resolve the requester's identity id from their email
POST /v3/access-requests           submit
GET  /v3/access-request-status     poll until approved or rejected
```

Setup in ISC before this will work:

1. Create an access profile, role or entitlement representing permission to create a
   Copilot agent. It does not need to provision anything. It exists to carry an approval
   workflow.
2. Attach an approval workflow to it, so the request waits on a human.
3. Create an OAuth client whose identity may request access on behalf of others.

Two API details worth knowing if you write your own integration.

`/v3/access-requests` returns 202 with an empty body, so there is no request id to poll.
The gate polls the status endpoint by requester and access item instead. That endpoint
returns every request this identity has raised for that item, including ones decided
days ago, so a naive implementation matches an old approval and returns immediately
without waiting for anyone. This gate additionally requires the matched row to carry the
current agent name in `clientMetadata` and to have been created after submission.

A submitted request also sits in state `EXECUTING` from the moment it is created, well
before anyone has approved it. Treating that state as approval would make the gate pass
without a human. The implementation inspects `approvalDetails` and requires every
approval on the request to have finished.

### IdentityIQ

```bash
SAILPOINT_MODE=iiq
SAILPOINT_IIQ_URL=https://iiq.internal.example.com
SAILPOINT_IIQ_WORKFLOW=LCM Provisioning
SAILPOINT_IIQ_USERNAME=...
SAILPOINT_IIQ_PASSWORD=...
```

Flow:

```
POST /identityiq/rest/workflows/{workflow}/launch    returns a TaskResult id
GET  /identityiq/rest/workflows/{taskResultId}       poll completionStatus
```

The workflow name and its input variables are deployment-specific. `LCM Provisioning` is
a common default, but verify against your instance. The gate passes `identityName`,
`agentName`, `copilotSurface`, `environmentId` and `comments` as workflow inputs; a
custom workflow will expect different ones.

The REST API must be enabled and the service account needs the `WebServiceExecutor`
capability, or the launch returns 403.

The equivalent trap applies here: a workflow with no approval step completes
immediately. The gate refuses to read that as an approval unless
`SAILPOINT_IIQ_REQUIRE_APPROVAL_ATTRIBUTE=false` is set explicitly, so a
misconfigured workflow fails loudly rather than approving everything.

## Running it

```bash
GATE_BACKEND=sailpoint ./scripts/terraform-local.sh gate
```

Or through Terraform:

```bash
cd terraform
terraform apply -var="gate_backend=sailpoint" ...
```

As noted above, the agent identity gate does not run under this backend.

## Adapting this to another platform

The same approach works for any system with an access request API. A gate needs to:

1. Submit a request carrying the agent name, owner, target environment and requested
   permissions.
2. Poll until a decision is final, distinguishing "submitted" from "approved". Most APIs
   make this harder than it looks.
3. Exit 0 on approval, 1 on denial, 2 on timeout, 3 when the API is unreachable.

`scripts/sailpoint_gate.py` is about 300 lines and is a reasonable template. ServiceNow,
Saviynt and Omada all expose comparable request APIs.
