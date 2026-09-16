variable "run_agent_gate" {
  type        = bool
  default     = true
  description = "When false, skip the gate local-exec (CD runs the gate script in the workflow instead)."
}

variable "gate_backend" {
  type        = string
  default     = "balkanid"
  description = "Which system reviews the request: balkanid (reference) or sailpoint (alternate gate backend)."

  validation {
    condition     = contains(["balkanid", "sailpoint"], var.gate_backend)
    error_message = "gate_backend must be balkanid or sailpoint."
  }
}

variable "copilot_surface" {
  type        = string
  default     = "copilot-studio"
  description = "copilot-studio (Dataverse bot in a Power Platform solution) or declarative-agent (M365 app package)."

  validation {
    condition     = contains(["copilot-studio", "declarative-agent"], var.copilot_surface)
    error_message = "copilot_surface must be copilot-studio or declarative-agent."
  }
}

variable "agent_name" {
  type        = string
  default     = "example-trial-intake-agent"
  description = "Agent name used in the access request and in the provisioned resource."
}

variable "agent_type" {
  type        = string
  default     = "copilot-studio"
  description = "Agent classification passed to createRequest AGENT_ACCESS."
}

variable "agent_purpose" {
  type        = string
  default     = "Copilot agent provisioned with BalkanID approval"
  description = "Business justification shown to the approver."
}

variable "agent_schema_name" {
  type        = string
  default     = ""
  description = "Copilot Studio schema name (stable across environments, unlike the bot GUID)."
}

variable "solution_unique_name" {
  type        = string
  default     = ""
  description = "Power Platform solution carrying the agent, shown on the access request."
}

variable "power_platform_environment_id" {
  type        = string
  default     = ""
  description = "Target Power Platform environment id, shown on the access request."
}

variable "intended_agent_identity_id" {
  type        = string
  default     = ""
  description = "Entra app id / agent identity the agent will run as, when known before creation."
}

# --- BalkanID -----------------------------------------------------------------

variable "balkanid_public_api_url" {
  type        = string
  default     = "https://your-tenant.balkanid.app/api/public"
  description = "BalkanID Public API base URL for your tenant."
}

variable "api_key_id" {
  type        = string
  sensitive   = true
  default     = ""
  description = "BalkanID employee API key id."
}

variable "api_key_secret" {
  type        = string
  sensitive   = true
  default     = ""
  description = "BalkanID employee API key secret."
}

variable "agent_owner_email" {
  type        = string
  sensitive   = true
  description = "Email of the employee who will own the agent (createRequest employeeEmail)."
}

variable "integration_id" {
  type        = string
  sensitive   = true
  default     = ""
  description = "BalkanID integration id (Entra / M365 / Power Platform) associated with the agent."
}

# --- SailPoint (optional alternate gate) --------------------------------------

variable "sailpoint_mode" {
  type        = string
  default     = "isc"
  description = "isc (Identity Security Cloud) or iiq (IdentityIQ on-premises)."
}

variable "sailpoint_base_url" {
  type        = string
  default     = ""
  description = "ISC tenant API base URL, for example https://acme.api.identitynow.com."
}

variable "sailpoint_client_id" {
  type      = string
  sensitive = true
  default   = ""
}

variable "sailpoint_client_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "sailpoint_access_item_id" {
  type        = string
  default     = ""
  description = "ISC access profile / role / entitlement id representing permission to create a Copilot agent."
}

variable "sailpoint_access_item_type" {
  type        = string
  default     = "ACCESS_PROFILE"
  description = "ACCESS_PROFILE, ROLE, or ENTITLEMENT."
}

# --- Polling ------------------------------------------------------------------

variable "poll_seconds" {
  type        = number
  default     = 5
  description = "Seconds between approval status polls."
}

variable "poll_timeout_seconds" {
  type        = number
  default     = 900
  description = "Fail the apply if approval has not landed within this window."
}
