# Security

## Reporting security issues

Per the [BalkanID Trust Center](https://trustcenter.balkan.id/), report security
questions, incidents or concerns to [security@balkan.id](mailto:security@balkan.id).

When reporting a finding related to this reference repository (sample code, docs, or
GitHub Actions workflows):

1. Email security@balkan.id with a description, reproduction steps and impact.
2. Do not open a public GitHub issue for credential leaks, tenant-specific data, or
   exploitable vulnerabilities.
3. Do not test against BalkanID production systems or customer tenants without
   authorization.
4. Allow reasonable time for triage before public disclosure.

For non-security bugs or documentation fixes, use
[GitHub Issues](https://github.com/balkanid/agent-lifecycle-copilot-reference/issues).

## Protecting your credentials

This reference handles credentials in three places:

| Credential | Grants |
|---|---|
| BalkanID API key id and secret | Creating access requests on your tenant |
| Entra service principal | Creating app registrations, consenting Graph permissions, importing Power Platform solutions |
| SailPoint OAuth client or IdentityIQ service account | Submitting access requests, only if that gate is used |

The Entra service principal is the highest-value credential in the design. It can create
identities and grant them Graph permissions, which is precisely the capability the gates
exist to control. Treat it accordingly: prefer federated credentials or workload identity
over a client secret, scope it to a single environment where possible, and rotate it on a
schedule.

| Location | Guidance |
|---|---|
| Git | Never commit `.env`, `*.tfvars` or `terraform.tfstate`. All three are gitignored |
| GitHub Actions secrets | API keys, client secrets, tenant URL, owner email, integration id |
| GitHub Actions variables | Non-sensitive toggles: agent name, timeouts, environment id |
| Terraform state | Contains secrets when any are set. Use a remote backend with encryption and locking |
| Actions cache | The CD workflow uses local state. Use a remote backend for anything beyond evaluation |

## GitHub Actions setup

1. Create the environment `agent-lifecycle` and add secrets and variables per
   [`.github/CD_CONFIG.md`](.github/CD_CONFIG.md).
2. Store `BALKANID_PUBLIC_API_URL`, `BALKANID_AGENT_OWNER_EMAIL` and
   `BALKANID_INTEGRATION_ID` as secrets rather than variables, so GitHub masks them in
   logs. The workflow reads `secrets.X || vars.X` for these three, so either works and
   the secret takes precedence.
3. Require reviewers on the environment, so a run cannot start unattended.
4. Prefer OIDC over a client secret. The Power Platform provider accepts
   `use_oidc = true`, and Azure workload identity federation is standard.

## Design notes

The scripts redact identifiers in CI logs and suppress GraphQL error detail when
`GITHUB_ACTIONS` or `CI` is set. An approval failure prints a request id and nothing
else. A consequence worth knowing: the pipeline log is deliberately not a complete
record of what was approved. The approval record in the governance platform is the
primary evidence. See
[`docs/REGULATED_ENVIRONMENTS.md`](docs/REGULATED_ENVIRONMENTS.md#retention-and-record-keeping).

`terraform/entra-agent-identity` does not create a client secret by default. A long-lived
secret in Terraform state is exactly the credential an access review cannot revoke, and
an agent identity is the case where that matters most.

The gates fail closed on denial, on timeout, and when the governance platform is
unreachable. An unavailable policy service must never imply consent.

## Network behaviour

The scripts make outbound calls only to:

- Your BalkanID Public API endpoint
- Microsoft endpoints: `login.microsoftonline.com`, `graph.microsoft.com`,
  `api.powerplatform.com`, and your Dataverse organisation URL
- Your SailPoint endpoint, only when that gate is configured

There is no telemetry, and no third-party services are contacted.

## Scope

This repository is sample code published for adaptation. It is licensed MIT and provided
as-is, without warranty. Questions about the security posture of the BalkanID platform
itself, as distinct from this repository, belong with your BalkanID contact or the
[Trust Center](https://trustcenter.balkan.id/).
