# Microsoft Copilot agent lifecycle: BalkanID reference architecture

Reference architecture and working implementation for governing the creation of
Microsoft Copilot agents: who may create one, who owns it, what identity it runs as, and
what was approved.

Companion to [`agent-lifecycle-terraform-reference`](https://github.com/balkanid/agent-lifecycle-terraform-reference),
which covers the same pattern for AWS Bedrock.

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the design: components, trust
  boundaries, control flows and deployment topologies.
- [`docs/EVALUATION.md`](docs/EVALUATION.md) is a staged path to proving it in your own
  tenant, starting with no Microsoft credentials.

## The control gap

Microsoft provides preventive controls over Copilot agents, but they are coarse:
environment security-group scoping, the Environment Maker role, DLP policies, Managed
Environment sharing limits, the custom app upload tenant setting, and administrative
toggles for Agent Builder. Each is an allow or deny decision about a population of
makers, configured in advance.

What the platform does not provide is an approval step for an individual agent. There is
no supported way to require that a named person authorise this agent, with this owner,
reading these sources, before it exists.

The creation paths make that gap consequential, because Copilot is built for
self-service authoring:

- A Copilot Studio agent is a row in the Dataverse `bot` table. Any maker with access to
  an environment can create one in the portal in under a minute.
- A declarative agent is an app package, which any developer can sideload where custom
  app upload is permitted.
- An Agent Builder agent requires no app registration and no Entra identity at all.

Within an environment a maker already has, agent creation is a data operation rather
than an infrastructure one. It does not pass through change control, and it produces no
authorization record. By the time an identity governance platform sees the agent, it
exists, it has been shared, and it is reading corporate data on behalf of whoever talks
to it.

This reference adds a per-agent approval in front of creation for agents delivered
through a pipeline, and detection for the agents that are not. Both halves are needed:
a gate governs what goes through it, and reconciliation finds what did not. Neither
requires replacing the tooling teams already use to build agents.

```
delivery pipeline
    → createRequest (CREATE_SERVICE_ACCOUNT)   review the agent's Entra identity
    → createRequest (AGENT_ACCESS)             review the agent itself
    → policy evaluation
    → [pending] approver acts
    → approved → provision
        Copilot Studio:     powerplatform_managed_solution → pac copilot publish
        Declarative agent:  atk package → Graph app-catalog publish
    → syncIntegration → the agent is linked to its owner and its approval
    → reconcile the tenant against approvals, on a schedule
```

Denial or timeout stops the pipeline and creates nothing.

This governs whether an agent may exist and what identity it holds. It does not govern
what the agent does at runtime; that is a separate and complementary control.

## What Terraform can and cannot do here

There is no Terraform resource that creates a Copilot Studio agent. As of the
[`microsoft/power-platform` provider](https://registry.terraform.io/providers/microsoft/power-platform/latest/docs)
v4.2.0, the only Copilot-specific resource is
`powerplatform_copilot_studio_application_insights`, which wires telemetry to an agent
that already exists. There is no `powerplatform_bot`. Check the registry for the current
resource list before relying on this.

An agent is a Dataverse record and a solution component, so the supported path is to
package it as a solution and import the package:

| Step | Owned by |
|---|---|
| Environment, managed environment guardrails, DLP, connections | Terraform |
| The agent's Entra identity and its Graph permissions | Terraform (`azuread`) |
| The agent's own content: topics, knowledge sources | `pac copilot` and version control |
| Turning that content into a managed solution | `pac solution import`, then `pac solution export --managed true` |
| Importing the solution that carries the agent | Terraform (`powerplatform_managed_solution`) |
| Making the imported agent live | `pac copilot publish` |
| Publishing a declarative agent to the org catalog | Microsoft Graph, delegated authentication only |

The approval gate sits in front of all of it and does not depend on which path you take,
because it is a policy decision rather than a pipeline step.

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#platform-constraints) lists the platform
constraints in full.

## Repository layout

```
docs/
  ARCHITECTURE.md                the design: components, trust boundaries, control flows
  EVALUATION.md                  staged path to proving this in your own tenant
  SETUP.md                       preparing a Copilot tenant and toolchain
  AGENT_IDENTITY.md              Entra Agent ID, and what is not automatable yet
  REGULATED_ENVIRONMENTS.md      approval evidence, segregation of duties, retention
  SAILPOINT.md                   raising the request in an existing IGA platform
  TROUBLESHOOTING.md             failures you are likely to hit
scripts/
  gate.py                        AGENT_ACCESS approval gate
  entra_agent_identity_gate.py   CREATE_SERVICE_ACCOUNT gate for the agent's identity
  sailpoint_gate.py              alternate gate: ISC or IdentityIQ
  balkanid_api.py                shared Public API helpers, retry and poll
  discover_agents.py             tenant-wide agent inventory and reconciliation
  publish_copilot_agent.py       pac copilot publish wrapper
  publish_declarative_agent.py   Graph app-catalog publish, device-code authentication
  verify_approved_scope.py       confirms the environment matches what was approved
  preflight.py                   environment validation
  trigger_sync.py                syncIntegration after provisioning
  verify_imports.py              CI import smoke test
  terraform-local.sh             local apply and destroy wrapper
terraform/
  .                              gate-only root, no Microsoft credentials required
  copilot-studio/                environment, guardrails, solution import
  entra-agent-identity/          the agent's Entra identity and Graph permissions
copilot-studio/                  agent source, pulled with pac copilot clone
declarative-agent/               Agents Toolkit project: agent schema v1.8, app manifest v1.30
azure/PERMISSIONS.md             the three permission surfaces, and what cannot be automated
env.example
```

## Prerequisites

BalkanID:

- Agent access requests available on your tenant. If the option is not visible in the UI, ask your BalkanID administrator.
- At least one connected integration (Entra, M365 or Power Platform), used in the
  request and in the post-provision sync.
- An employee API key permitted to call `createRequest` on the
  [Public API](https://docs.balkan.id/).
- An access-request policy: require an approver, fail closed on deny.
- Optional: a webhook destination subscribed to `request.actioned`, so a long approval
  does not hold a CI runner open.

Local tooling:

- A POSIX shell. `scripts/terraform-local.sh` and the Terraform `local-exec` provisioners
  invoke `/bin/bash`, so on Windows use WSL.
- Python 3.11 or newer. Standard library only; there is nothing to install.
- Terraform 1.5 or newer.
- Power Platform CLI, for the Copilot Studio path:
  `dotnet tool install --global Microsoft.PowerApps.CLI.Tool`
- Microsoft 365 Agents Toolkit CLI, for the declarative agent path:
  `npm install -g @microsoft/m365agentstoolkit-cli`

Microsoft, only when provisioning:

- A Power Platform environment with Dataverse, and a service principal registered as a
  management application. See [`azure/PERMISSIONS.md`](azure/PERMISSIONS.md).
- Copilot Studio licensing on the tenant.
- For declarative agents, Microsoft 365 Copilot licensing and a Global Administrator to
  publish.

## Quick start: the gate alone

The preventive control demonstrates with nothing but a BalkanID API key.

```bash
cp env.example .env
# Edit .env: Public API URL, API key, agent owner email, integration id

set -a && source .env && set +a
python3 scripts/gate.py
```

Exit code 0 means approved, 1 denied or partially approved, 2 the approval window
elapsed, 3 the policy service was unreachable. Only 0 continues, so an unreachable
policy service never implies consent.

Through Terraform:

```bash
cd terraform
terraform init
terraform apply \
  -var="api_key_id=$API_KEY_ID" \
  -var="api_key_secret=$API_KEY_SECRET" \
  -var="agent_owner_email=$BALKANID_AGENT_OWNER_EMAIL" \
  -var="integration_id=$INTEGRATION_ID" \
  -var="agent_name=trial-intake-assistant"
```

The apply blocks until the request is decided, and fails on a denial.

## Copilot Studio: the full lifecycle

```bash
./scripts/terraform-local.sh apply-lifecycle
```

In order:

1. `entra_agent_identity_gate.py`. An approver reviews the agent's identity and the Graph
   permissions it is requesting, before the app registration exists. Runs only when
   `ENTRA_IDENTITY_GATE=true`, which is off in `env.example`.
2. `gate.py`. An approver reviews the agent itself: name, owner, target environment,
   solution. On by default.
3. `verify_approved_scope.py`. Confirms the environment still matches what was approved
   before anything is created. See [below](#binding-the-approval-to-what-is-created).
4. `terraform/entra-agent-identity`. Creates the app registration and admin-consents only
   the approved permissions. Runs only when `PROVISION_AGENT_IDENTITY=true`, also off by
   default.
5. `terraform/copilot-studio`. Applies managed environment guardrails, binds the agent
   identity to the environment, imports the solution. The package must be managed, and
   `pac copilot pack` emits unmanaged, so export a managed copy first. See
   [`copilot-studio/README.md`](copilot-studio/README.md).
   This stack also publishes the agent, by invoking `publish_copilot_agent.py`: an
   imported agent is not live until published, and this step is skipped silently unless
   `AGENT_SCHEMA_NAME` is set.
6. `trigger_sync.py`. BalkanID discovers the agent and links it to its owner.

To run the full two-gate lifecycle, set both `ENTRA_IDENTITY_GATE=true` and
`PROVISION_AGENT_IDENTITY=true` in `.env`. The defaults ship with the single agent gate
so the first run needs fewer Microsoft permissions.

Teardown:

```bash
./scripts/terraform-local.sh destroy-lifecycle
```

### Binding the approval to what is created

The gates and the provisioning steps read their configuration from the same environment
independently. Nothing in a plain pipeline stops a variable changing between the two, so
an approval record can end up describing something other than what was created. That
matters because the record is only evidence if it describes the actual agent.

The gates therefore write what was approved to `.approved-scope.json`, and
`verify_approved_scope.py` compares it against the environment before anything is
provisioned. It fails if the agent name, target environment, solution or Entra identity
has changed, or if a Graph permission is requested that no approver saw. Permissions that
were approved but are no longer requested are reported and allowed, since narrowing scope
is safe.

Set `VERIFY_APPROVED_SCOPE=false` to skip it, which is occasionally useful when
provisioning from a separate job that did not run the gates. Prefer passing the record
between jobs as an artefact.

## Declarative agents

```bash
COPILOT_SURFACE=declarative-agent ./scripts/terraform-local.sh apply-declarative
```

Gate, then `atk package`, then publish through Graph.

One constraint to plan around: `POST /appCatalogs/teamsApps` supports delegated
permissions only. A service principal cannot publish a declarative agent, so the publish
step uses the device-code flow. A pipeline packages and validates; a person publishes.

## Discovery and reconciliation

```bash
./scripts/terraform-local.sh discover
```

`discover_agents.py` reads the three inventory planes Microsoft exposes, which do not
reference each other:

| Source | Covers |
|---|---|
| Power Platform Inventory API, `microsoft.copilotstudio/agents` | every Copilot Studio agent in the tenant |
| Dataverse `bots` table | per-environment detail, publish state, owner |
| Graph `/copilot/admin/catalog/packages` | declarative and custom-engine agents |

Rows describing the same agent are merged, then compared against approved `AGENT_ACCESS`
requests. Agents with no approval are reported as ungoverned.

Run it on a schedule rather than only in the pipeline: the agents it is designed to find
are precisely the ones that never entered a pipeline. `FAIL_ON_UNGOVERNED=true` turns it
into a monitor.

## CI and CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) validates Python, Terraform, shell
and both agent manifests on every push.

[`.github/workflows/cd.yml`](.github/workflows/cd.yml) runs the gates in GitHub Actions,
then optionally provisions. Setup is in
[`.github/CD_CONFIG.md`](.github/CD_CONFIG.md).

```
Actions → CD → Run workflow
    → preflight
    → identity gate + agent gate         [job waiting on a human]
    → approved → terraform apply (Entra identity, then Copilot Studio)
    → syncIntegration
    → reconciliation
    → denied → job fails, nothing is created
```

The gates poll every 5 seconds until decided or the approval window elapses. That
window is `POLL_TIMEOUT_SECONDS` locally, defaulting to 15 minutes, and
`APPROVAL_WAIT_MINUTES` in the supplied CD workflow, defaulting to 120. For approvals
that take days, resume from a `request.actioned` webhook rather than holding a runner
open.

The same pattern works in Azure DevOps, GitLab CI and Jenkins. The gate is one command
before whatever creates the agent.

## BalkanID Public API usage

| API | Role here |
|---|---|
| `createRequest` (`AGENT_ACCESS`) | the agent gate, `scripts/gate.py` |
| `createRequest` (`CREATE_SERVICE_ACCOUNT`) | the identity gate, `scripts/entra_agent_identity_gate.py` |
| `requests` query | polls until approved or denied |
| `identities` query | resolves the agent's Entra identity after provisioning |
| `syncIntegration` | triggers discovery so the agent appears in BalkanID |

See the [BalkanID Public API documentation](https://docs.balkan.id/) for request shapes,
authentication and webhooks.

## Adapting this

This is a reference, not a deployment template. Before adopting:

- Remote Terraform state with locking, rather than local state.
- Webhook-driven resume instead of long polling in CI.
- Handle `ASSIGN`, `UNASSIGN` and `DELETE` agent access actions, not only `CREATE`.
- A solution versioning strategy. `powerplatform_managed_solution` rejects a version
  lower than the one installed, and a higher version triggers stage-and-upgrade, which
  removes components the new package omits.
- Your own secrets management. The client secret in `terraform/entra-agent-identity` is
  disabled by default for a reason.

If you operate under a formal quality framework, you will most likely reimplement this
inside your own validated pipeline rather than running it directly. The gate is about 150
lines of standard-library Python so that reimplementing and reviewing it are both quick.
See [`docs/REGULATED_ENVIRONMENTS.md`](docs/REGULATED_ENVIRONMENTS.md).

## Out of scope

- Runtime and invoke-time authorization
- Data loss prevention on agent knowledge sources
- Prompt and content safety
- Agent-to-agent delegation
- Azure AI Foundry agents (the pattern applies; the provisioning differs)

## Support and contributing

Questions about this repository: [GitHub Issues](https://github.com/balkanid/agent-lifecycle-copilot-reference/issues).
Questions about BalkanID: your BalkanID contact, or [balkan.id](https://balkan.id).
Security reports: [SECURITY.md](SECURITY.md).

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE). Provided as-is, without warranty.
