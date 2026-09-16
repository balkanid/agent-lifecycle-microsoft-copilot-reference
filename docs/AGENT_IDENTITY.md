# Agent identity in Microsoft, and what you can automate

What an agent runs as, and whether that can be reviewed before it exists, differs by how
the agent was built. That variation is itself the difficulty.

## The four cases

| How the agent was built | Identity | Reviewable before creation |
|---|---|---|
| Copilot Studio, created from May 2026 | Entra Agent ID, issued automatically by the platform | No. The identity appears with the agent |
| Copilot Studio, created before May 2026 | Classic app registration | Yes, if you create it yourself |
| Declarative agent with API plugin actions | Classic app registration you own | Yes |
| Declarative agent with no actions, or Agent Builder | None at all | Nothing to review |

Two of the four cases offer no identity to review before creation, which is why this
architecture gates the agent as well as the identity.

## Entra Agent ID

Entra Agent ID is a two-level model:

- An agent identity blueprint, an `application`-derived type, holds the credentials and
  can be multitenant.
- An agent identity, a `servicePrincipal` of subtype Agent, is instantiated from a
  blueprint, is always single-tenant, and holds no credentials of its own.

Directory roles: AI Reader to view, Agent ID Administrator to manage. Applying
Conditional Access or identity governance to an agent identity requires Entra Agent ID
licensing. Viewing does not.

Microsoft blocks a set of high-risk Graph permissions for agent identities outright.
That is a meaningful platform control, and it operates regardless of how the agent was
provisioned. See
[Entra Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/agent-identities)
for the current list.

## What is not automatable

There is no Terraform support for either object. `hashicorp/azuread` has no
`azuread_agent_identity` resource, and the blueprint is created by POSTing to
`/applications` with an `@odata.type` discriminator that `azuread_application` cannot
emit. Creating one means calling Graph directly, with the `azapi` provider, a
`null_resource`, or a script.

```http
POST /v1.0/applications/
  { "@odata.type": "Microsoft.Graph.AgentIdentityBlueprint",
    "displayName": "trial-intake-assistant",
    "sponsors@odata.bind": [ "..." ],
    "owners@odata.bind": [ "..." ] }

POST /beta/serviceprincipals/Microsoft.Graph.AgentIdentity
  { "displayName": "trial-intake-assistant",
    "agentIdentityBlueprintId": "..." }
```

`terraform/entra-agent-identity` therefore manages the classic app registration path.
That is what Terraform can do today, and it covers the two cases where an identity
actually exists to review.

## A retirement to be aware of

The older Entra `agentRegistry` Graph resource is
[deprecated](https://learn.microsoft.com/en-us/graph/api/resources/agentregistry?view=graph-rest-beta)
in favour of the [Agent 365 registration API](https://learn.microsoft.com/en-us/microsoft-agent-365/admin/graph-api).
Microsoft announced the transition and a retirement timetable for the legacy API during
2026; confirm current dates against the message centre notice for your tenant rather
than relying on this document. Copilot Studio and Foundry-built agents were not affected.

If you encounter `agentRegistry` or `/beta/copilot/agentRegistrations` in existing
tooling, this is the context.

## How this repository handles it

1. Run the identity gate for review regardless of which case applies. The approver is
   reviewing the intent, not only the object.
2. Create the app registration with Terraform where the case allows it, consenting only
   the approved permissions.
3. Where the platform issues the identity itself, reconcile it afterwards through the
   BalkanID Entra integration and attach the approval evidence to the discovered
   identity.

Step 3 is not as good as step 2. It is what the platform permits.

## References

- [Entra Agent ID overview](https://learn.microsoft.com/en-us/entra/agent-id/agent-identities)
- [Create an agent identity blueprint](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/create-blueprint)
- [Agent registry convergence](https://learn.microsoft.com/en-us/entra/agent-id/agent-registry-convergence)
- [Copilot Studio agent identities](https://learn.microsoft.com/en-us/microsoft-copilot-studio/govern-agents-identities-overview)
