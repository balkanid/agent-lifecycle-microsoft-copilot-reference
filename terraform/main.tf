# Gate-only root module.
#
# Runs the approval gate and nothing else, so the pattern can be demonstrated with no
# Microsoft credentials at all. Point your existing Copilot pipeline at this module,
# or call scripts/gate.py directly from whatever runner you already use.
#
# The provisioning stacks live in ./copilot-studio and ./entra-agent-identity.

locals {
  gate_script = var.gate_backend == "sailpoint" ? "sailpoint_gate.py" : "gate.py"
}

resource "null_resource" "agent_gate" {
  count = var.run_agent_gate ? 1 : 0

  triggers = {
    agent_name = var.agent_name
    surface    = var.copilot_surface
    # bump to force a new request on re-apply
    run_nonce = timestamp()
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-lc"]
    command     = "python3 \"${path.module}/../scripts/${local.gate_script}\""

    environment = {
      BALKANID_PUBLIC_API_URL    = var.balkanid_public_api_url
      API_KEY_ID                 = var.api_key_id
      API_KEY_SECRET             = var.api_key_secret
      BALKANID_AGENT_OWNER_EMAIL = var.agent_owner_email
      INTEGRATION_ID             = var.integration_id

      COPILOT_SURFACE = var.copilot_surface
      AGENT_NAME      = var.agent_name
      AGENT_TYPE      = var.agent_type
      AGENT_PURPOSE   = var.agent_purpose

      POWER_PLATFORM_ENVIRONMENT_ID = var.power_platform_environment_id
      SOLUTION_UNIQUE_NAME          = var.solution_unique_name
      AGENT_SCHEMA_NAME             = var.agent_schema_name
      INTENDED_AGENT_IDENTITY_ID    = var.intended_agent_identity_id

      SAILPOINT_MODE             = var.sailpoint_mode
      SAILPOINT_BASE_URL         = var.sailpoint_base_url
      SAILPOINT_CLIENT_ID        = var.sailpoint_client_id
      SAILPOINT_CLIENT_SECRET    = var.sailpoint_client_secret
      SAILPOINT_ACCESS_ITEM_ID   = var.sailpoint_access_item_id
      SAILPOINT_ACCESS_ITEM_TYPE = var.sailpoint_access_item_type

      POLL_SECONDS         = tostring(var.poll_seconds)
      POLL_TIMEOUT_SECONDS = tostring(var.poll_timeout_seconds)
    }
  }
}
