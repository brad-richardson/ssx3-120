output "instance_public_ip" {
  description = "Public IPv4 of the lobby host."
  value       = oci_core_instance.lobby.public_ip
}

output "lobby_address" {
  description = "Address game clients point PS2X_NET_SERVER at."
  value       = oci_core_instance.lobby.public_ip
}

output "lobby_ports" {
  description = "TCP ports the lobby listens on (lobby, game, buddy)."
  value       = var.lobby_tcp_ports
}

output "ssh_command" {
  description = "SSH command for the lobby host."
  value       = "ssh ubuntu@${oci_core_instance.lobby.public_ip}"
}
