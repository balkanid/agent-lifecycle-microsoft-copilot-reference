# Regulated environments

Notes for organisations operating under a formal quality or control framework, where an
auditor will eventually ask who authorised a system that processes regulated data.

This document describes what the reference produces and where the gaps are. It is not a
compliance claim. Neither this repository nor BalkanID asserts that using it satisfies
any specific regulation, and no software can: compliance is a property of your processes,
your validation and your evidence, not of a tool you install.

## Why agents attract attention

An AI agent with access to a document library is, from a control standpoint, a system
that reads records and produces output people act on. The questions an auditor asks about
such a system are the familiar ones:

- Who authorised it to exist, and on what basis?
- Who is accountable for it now?
- What can it read, and who approved that scope?
- When did it change, and who approved the change?
- How would you know if someone created one outside the process?

Self-service agent creation makes all five hard to answer. An agent created in a portal
in under a minute leaves a Dataverse row, an audit event if transcript recording happens
to be enabled, and no authorization record at all.

## What the gate produces as evidence

Each approved request creates a durable record linking:

| Field | Source | Auditor relevance |
|---|---|---|
| Requester | The named employee who owns the agent | Accountability |
| Approver | A different named employee | Segregation of duties |
| Decision and timestamp | The governance platform | When authorization was granted |
| Justification | `AGENT_PURPOSE`, supplied by the requester | Basis for the decision |
| Requested scope | Graph permissions on the identity request | What was actually authorised |
| Target environment | Power Platform environment id | Where it was authorised to run |
| Resulting agent | Linked by integration sync after creation | Connects the authorization to the thing authorised |

The last row is the one that distinguishes an authorization record from a closed ticket.
A ticket says permission was granted. A linked record lets you start from a running agent
and reach the decision that allowed it, which is the direction an auditor actually
travels.

For that record to be evidence rather than an assertion, it has to describe what was
created rather than what was asked for. `scripts/verify_approved_scope.py` runs between
the gates and provisioning and fails if the agent name, target environment, solution or
requested Graph permissions have changed since approval. Without that step the two halves
read their configuration independently and can diverge silently. Keep
`VERIFY_APPROVED_SCOPE=true`.

## Segregation of duties

The architecture separates four identities, described in
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md). Two separations matter for control purposes:

Requester and approver must be different people. This is enforced by the access request
policy in the governance platform, not by this repository. Configure it there and test
it, because a policy that silently permits self-approval produces evidence that looks
complete and is not.

The pipeline service principal must not be the agent identity. The pipeline can create
app registrations and consent Graph permissions. An agent that held those rights could
create further agents with arbitrary scope, and no subsequent approval would be
meaningful. `terraform/entra-agent-identity` creates a separate identity per agent for
this reason.

## Change control

Agent content changes are not agent creation, and the two warrant different handling.

A Copilot Studio agent's topics and knowledge sources live in `botcomponent` rows and
travel in the solution package. Pulling them into version control with `pac copilot pull`
makes each change a reviewable diff, which is the mechanism most quality systems already
recognise. The environment layering in
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md#environment-layering) maps the promotion
boundary onto the solution import step.

Two changes deserve a fresh authorization rather than a code review:

- A change to the agent's requested permissions. The scope that was approved is no
  longer the scope in effect.
- A change to the knowledge sources it reads. The data the agent can reach has changed
  even if its behaviour has not.

Neither is detected automatically by this reference. Both are visible in a solution diff,
and a pipeline can be configured to re-run the identity gate when the permission list
changes.

## Retention and record keeping

Approval records live in the governance platform and follow its retention configuration.
Confirm that the retention period meets your requirements before relying on these records
as evidence, and confirm how records are exported if you need them outside the platform.

Pipeline logs are a secondary record. They show which gate ran, which request id it
created, and what decision returned. They do not contain the approval itself. Treat them
as corroborating rather than primary evidence, and note that the scripts deliberately
redact identifiers in CI logs, so the pipeline log is not a complete account by design.

Microsoft-side records are a third source. Purview audit covers agent administrative
activity under `AgentAdminActivity` and `AgentSettingsAdminActivity`, and Copilot Studio
transcript recording, which
[`terraform/copilot-studio`](../terraform/copilot-studio/main.tf) enables by default,
makes agent conversations reviewable. Note that Microsoft's agent administrative audit
records currently cover Copilot Studio agents; prebuilt, third-party and SharePoint
agents are not fully represented.

## Periodic review

A creation-time approval decays. An agent approved eighteen months ago may have an owner
who has left, permissions nobody would grant today, and knowledge sources that have since
been reclassified.

`scripts/discover_agents.py` produces the input for a periodic review: the current agent
population, each agent's owner where one is known, and the agents with no approval at
all. Running it on a schedule and treating the ungoverned list as a work queue is the
minimum viable detective control. Formal recertification campaigns over agents and their
owners are a governance platform capability rather than something this reference
implements.

## Validation

If your quality system requires validated computerised systems, note what this repository
is and is not.

It is a reference implementation: sample code demonstrating a pattern, published for
adaptation. It is not a validated system, it carries no qualification documentation, and
it is licensed as-is under MIT with no warranty.

Adopters operating a validated pipeline will generally reimplement this pattern inside
it rather than running this code directly, using the repository as a design reference and
a worked example of the API calls. The gate is roughly 150 lines of standard-library Python
precisely so that reimplementing it is straightforward and reviewing it is quick.

The BalkanID platform itself is a separate question from this repository. Direct
questions about its controls, certifications and audit reports to your BalkanID contact
or the [Trust Center](https://trustcenter.balkan.id/).

## Gaps to plan for

Honest list, so none of these is a surprise during an audit readiness review:

| Gap | Consequence | Mitigation |
|---|---|---|
| Agents created before the gate was introduced | No authorization record exists | Reconcile, assign owners, record retrospective approvals |
| Agent Builder agents | No Entra identity to review | Agent gate is the only control; consider disabling Agent Builder |
| Declarative agent publish is a delegated action | An administrator publishes outside the pipeline | Gate before packaging; treat the administrator as a second control point |
| Permission changes after approval | Scope drift | `verify_approved_scope.py` blocks provisioning; re-run the identity gate |
| Platform-issued Entra Agent IDs | Identity appears with the agent, not before | Review the intent at the gate; reconcile the object afterwards |
| Reconciliation depends on licensing | Declarative agents under-reported without Agent 365 | Confirm licensing, and treat warnings in the reconciler output as material |
