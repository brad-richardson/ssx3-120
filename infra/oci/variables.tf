# Variables for the SSX 3 lobby host on Oracle Cloud (Always Free Ampere A1).
# No credentials here: auth comes from the DEFAULT profile in ~/.oci/config.

variable "config_profile" {
  description = "Profile name in ~/.oci/config used by the OCI provider."
  type        = string
  default     = "DEFAULT"
}

variable "region" {
  description = "OCI region, e.g. us-phoenix-1. Must be the tenancy home region for Always Free."
  type        = string
}

variable "compartment_ocid" {
  description = "OCID of the compartment that holds the lobby VCN and instance."
  type        = string
}

variable "availability_domain" {
  description = "Availability domain name, e.g. \"XXXX-1\". If apply fails with out-of-host-capacity, retry or change this."
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to an SSH public key file. Installed as ubuntu's authorized key."
  type        = string
}

variable "admin_cidrs" {
  description = "CIDRs allowed to reach SSH. Keep this restricted to the operator's home or VPN."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for cidr in var.admin_cidrs : can(cidrhost(cidr, 0)) && !contains(["0.0.0.0/0", "::/0"], trimspace(cidr))])
    error_message = "admin_cidrs must contain valid, non-world-open CIDRs."
  }
}

variable "player_cidrs" {
  description = "CIDRs allowed to reach the lobby TCP and relay UDP ports. Default empty: no player ingress."
  type        = list(string)
  default     = []
}

variable "lobby_tcp_ports" {
  description = "Quick-play TCP ports (lobby, game, buddy)."
  type        = list(number)
  default     = [11000, 10901, 13505]
}

variable "relay_udp_start" {
  description = "First UDP port of the Rust lobby relay range (game well-known P2P port)."
  type        = number
  default     = 3658
}

variable "relay_udp_end" {
  description = "Last UDP port of the Rust lobby relay range."
  type        = number
  default     = 4657
}

variable "state_udp_ports" {
  description = "Quick Play STATE/results UDP ports (fixed 3657 below the relay range, plus the legacy 3757 kept during the transition)."
  type        = list(number)
  default     = [3657, 3757]
}

variable "ocpus" {
  description = "A1 Flex OCPUs. Always Free allows up to 4."
  type        = number
  default     = 1
}

variable "memory_gb" {
  description = "A1 Flex memory in GB. Always Free allows up to 24."
  type        = number
  default     = 6
}

variable "boot_volume_gb" {
  description = "Boot volume size in GB."
  type        = number
  default     = 50
}

variable "os_version" {
  description = "Ubuntu LTS version, e.g. 26.04. The image data source tracks the newest dated full (non-Minimal) aarch64 build."
  type        = string
  default     = "26.04"
}

variable "instance_name" {
  description = "Display name for the lobby instance."
  type        = string
  default     = "ssx3-lobby"
}

variable "budget_amount" {
  description = "Monthly budget alert threshold in the tenancy currency (USD)."
  type        = number
  default     = 10
}

variable "budget_alert_email" {
  description = "Recipient(s) for budget alerts (comma-separated). Set in the local, gitignored terraform.tfvars only."
  type        = string
}
