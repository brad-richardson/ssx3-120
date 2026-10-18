# One Always Free Ampere A1 (arm64) instance. Ubuntu 24.04 aarch64 is looked
# up by data source, never a hardcoded OCID.

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = var.os_version
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  # Full dated server images only (e.g. Canonical-Ubuntu-26.04-aarch64-…):
  # excludes Minimal builds, which lack cloud-init's full feature set.
  filter {
    name   = "display_name"
    values = ["^Canonical-Ubuntu-${var.os_version}-aarch64-.*"]
    regex  = true
  }
}

resource "oci_core_instance" "lobby" {
  compartment_id      = var.compartment_ocid
  availability_domain = var.availability_domain
  display_name        = var.instance_name
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.lobby.id
    assign_public_ip = true
    display_name     = "ssx3-lobby-vnic"
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
    # Ports only. Sources stay in the security list: the host firewall is
    # static; OCI admin_cidrs controls who can reach SSH.
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml", {
      lobby_ports     = var.lobby_tcp_ports
      relay_udp_start = var.relay_udp_start
      relay_udp_end   = var.relay_udp_end
      state_udp_ports = var.state_udp_ports
      ssh_public_key  = trimspace(file(var.ssh_public_key_path))
    }))
  }

  lifecycle {
    # The Ubuntu image data source tracks the newest build, and user_data
    # embeds the rendered CIDR list. Without this, a newer upstream image
    # (or a CIDR edit) would REPLACE the instance, destroying the lobby
    # host and its /srv/ssx3-lobby data. Rebuilds are deliberate:
    # tofu apply -replace=oci_core_instance.lobby
    ignore_changes = [
      source_details[0].source_id,
      metadata["user_data"],
    ]
  }
}
