output "gate_completed" {
  description = "Gate resource id when the approval ran, or \"skipped\"."
  value       = var.run_agent_gate ? null_resource.agent_gate[0].id : "skipped"
}

output "gate_backend" {
  description = "System that reviewed the request."
  value       = var.gate_backend
}
