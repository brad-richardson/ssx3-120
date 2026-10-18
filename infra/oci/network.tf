# VCN, public subnet, internet gateway, route table, security list.
# SSH (22), the lobby TCP ports and the Rust relay UDP range are open
# to separate admin/player lists. With the default empty lists, no ingress
# rule exists: nothing is open.

locals {
  # One security-list rule per (port, cidr) pair.
  lobby_ingress = flatten([
    for port in var.lobby_tcp_ports : [
      for cidr in var.player_cidrs : {
        port = port
        cidr = cidr
      }
    ]
  ])
}

resource "oci_core_vcn" "lobby" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "ssx3-lobby-vcn"
  dns_label      = "ssx3lobby"
}

resource "oci_core_internet_gateway" "lobby" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lobby.id
  display_name   = "ssx3-lobby-igw"
  enabled        = true
}

resource "oci_core_route_table" "lobby" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lobby.id
  display_name   = "ssx3-lobby-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.lobby.id
  }
}

resource "oci_core_security_list" "lobby" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lobby.id
  display_name   = "ssx3-lobby-seclist"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  dynamic "ingress_security_rules" {
    for_each = toset(var.admin_cidrs)
    content {
      source   = ingress_security_rules.value
      protocol = "6" # TCP
      tcp_options {
        min = 22
        max = 22
      }
    }
  }

  dynamic "ingress_security_rules" {
    for_each = {
      for r in local.lobby_ingress : "${r.port}-${r.cidr}" => r
    }
    content {
      source   = ingress_security_rules.value.cidr
      protocol = "6" # TCP
      tcp_options {
        min = ingress_security_rules.value.port
        max = ingress_security_rules.value.port
      }
    }
  }

  # Rust lobby relay UDP range uses the player allowlist only.
  dynamic "ingress_security_rules" {
    for_each = toset(var.player_cidrs)
    content {
      source   = ingress_security_rules.value
      protocol = "17" # UDP
      udp_options {
        min = var.relay_udp_start
        max = var.relay_udp_end
      }
    }
  }

  # Quick Play STATE/results UDP ports (fixed 3657 below the relay range
  # plus the legacy 3757), player allowlist only.
  dynamic "ingress_security_rules" {
    for_each = {
      for r in flatten([
        for port in var.state_udp_ports : [
          for cidr in var.player_cidrs : {
            port = port
            cidr = cidr
          }
        ]
      ]) : "${r.port}-${r.cidr}" => r
    }
    content {
      source   = ingress_security_rules.value.cidr
      protocol = "17" # UDP
      udp_options {
        min = ingress_security_rules.value.port
        max = ingress_security_rules.value.port
      }
    }
  }
}

resource "oci_core_subnet" "lobby" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.lobby.id
  cidr_block                 = "10.0.1.0/24"
  display_name               = "ssx3-lobby-subnet"
  dns_label                  = "lobby"
  route_table_id             = oci_core_route_table.lobby.id
  security_list_ids          = [oci_core_security_list.lobby.id]
  prohibit_public_ip_on_vnic = false
}
