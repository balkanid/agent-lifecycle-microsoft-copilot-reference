# Agent source - Copilot Studio

This folder holds the agent itself, in the form Terraform can consume: a Power Platform
solution package.

## Why the agent is not defined in HCL

A Copilot Studio agent is a Dataverse record. `bot` holds the agent; `botcomponent`
holds its topics, knowledge sources, triggers and settings. Neither has a Terraform
resource, and the provider maintainers have not announced one. The agent moves between
environments the way every other Dataverse component does - inside a solution `.zip`.

So the pipeline is:

```
author in Copilot Studio (or the VS Code extension)
  → pac copilot clone / pull        pull the agent into source control as YAML
  → git                             review, diff, approve the change itself
  → pac copilot pack                build the solution .zip
  → pac solution check              static analysis
  → powerplatform_managed_solution  import into the target environment  ← Terraform
  → pac copilot publish             make it live
```

Terraform owns the environment, the guardrails, the connections and the import.
`pac` owns the agent's content and the publish. Trying to force the agent's topics into
HCL would fight the platform.

## Getting the agent into this folder

Install the CLI once:

```bash
dotnet tool install --global Microsoft.PowerApps.CLI.Tool
pac auth create --name dev --environment https://yourorg.crm.dynamics.com
```

Then either start a new agent from a template:

```bash
pac copilot init \
  --name "Trial Intake Assistant" \
  --publisher-prefix bkn \
  --project-dir ./src
```

or clone one that already exists in the tenant, which is the more common case:

```bash
pac copilot list --environment $POWER_PLATFORM_ENVIRONMENT_ID
pac copilot clone --bot <schemaname> --output-dir ./src
```

Iterate, then pull changes back down and pack:

```bash
pac copilot pull --project-dir ./src
pac copilot pack \
  --publisher-prefix bkn \
  --solution-name BalkanIDAgentLifecycle \
  --project-dir ./src \
  --output-path ./build/BalkanIDAgentLifecycle.zip
```

### That package is unmanaged. Terraform needs a managed one.

`pac copilot pack` produces an unmanaged solution. `powerplatform_managed_solution` rejects it with "managed solution
required".

There is no offline conversion. Microsoft is direct about this: the packaging tool
cannot convert one type into the other. The only supported route is to import the
unmanaged solution into a Dataverse environment and export it again as managed. That is
the standard dev-to-build-to-prod pattern, and it is why teams keep a dedicated build
environment.

```bash
# 1. Import the unmanaged solution into your development environment
pac solution import \
  --path ./build/BalkanIDAgentLifecycle.zip \
  --publish-changes

# 2. Export it back out as managed - this is the artefact Terraform consumes
pac solution export \
  --name BalkanIDAgentLifecycle \
  --path ./build/BalkanIDAgentLifecycle_managed.zip \
  --managed true

# 3. Static analysis before it goes anywhere
pac solution check \
  --path ./build/BalkanIDAgentLifecycle_managed.zip \
  --outputDirectory ./build/checker
```

Point `SOLUTION_ZIP_PATH` at the managed `.zip`.

Set `import_solution = false` on the Terraform module to apply the environment
guardrails without importing anything.

## Known export constraints

These are easy to hit and the error messages are not always clear:

- Only unmanaged solutions can be exported from the maker portal.
- A solution containing an agent whose topic names contain a `.` cannot be exported.
- Authentication settings, channel configuration and the agent icon do not travel
  with the solution. Reconfigure them after import.
- Custom connectors must be imported before the connection references and the agent.
- An imported agent is not live until it is published.
- More than 250 topics or 100 entities requires the standard harness export path.

Source: [Export and import agents using solutions](https://learn.microsoft.com/en-us/microsoft-copilot-studio/authoring-export-import-bots)
