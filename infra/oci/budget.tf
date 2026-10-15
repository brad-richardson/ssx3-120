# Monthly cost guard. The lobby is meant to run inside the Always Free
# allowance; alerts fire at the $10 monthly budget. The recipient lives only in
# the local, gitignored terraform.tfvars.

resource "oci_budget_budget" "guard" {
  compartment_id = var.compartment_ocid # budgets live in the root compartment (the tenancy)
  amount         = var.budget_amount
  reset_period   = "MONTHLY"
  target_type    = "COMPARTMENT"
  targets        = [var.compartment_ocid]
  display_name   = "ssx3-lobby-cost-guard"
  description    = "Alert at $10 monthly spend; the lobby should stay inside Always Free."
}

resource "oci_budget_alert_rule" "actual" {
  budget_id      = oci_budget_budget.guard.id
  type           = "ACTUAL"
  threshold      = 100
  threshold_type = "PERCENTAGE"
  display_name   = "ssx3-actual-spend"
  recipients     = var.budget_alert_email
  message        = "SSX3 lobby: actual Oracle Cloud spend reached the monthly budget. Check the console (Always Free allowance exceeded?)."
}

resource "oci_budget_alert_rule" "forecast" {
  budget_id      = oci_budget_budget.guard.id
  type           = "FORECAST"
  threshold      = 100
  threshold_type = "PERCENTAGE"
  display_name   = "ssx3-forecast-spend"
  recipients     = var.budget_alert_email
  message        = "SSX3 lobby: forecast Oracle Cloud spend will exceed the monthly budget."
}
