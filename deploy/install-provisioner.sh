#!/usr/bin/env bash
# Install only the reviewed provisioning package as root-owned system files.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
  echo 'Run with sudo; no password should be sent to the application.' >&2
  exit 1
fi
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$DEPLOY_DIR/../src/robot_agent/interfaces/provisioning" && pwd)"
/usr/bin/python3 -B -c 'import dbus; from gi.repository import GLib'
install -d -m 0755 -o root -g root /opt/robot-provisioner /opt/robot-provisioner/provisioning
for file in __init__.py protocol.py controller.py network.py network_manager.py bluez.py pairing.py service.py; do
  install -m 0644 -o root -g root "$SOURCE_DIR/$file" "/opt/robot-provisioner/provisioning/$file"
done
install -m 0644 -o root -g root "$DEPLOY_DIR/robot-provisioner.service" /etc/systemd/system/robot-provisioner.service
if [[ ! -e /etc/robot-provisioner.conf ]]; then
  install -m 0600 -o root -g root "$DEPLOY_DIR/provisioning.example.env" /etc/robot-provisioner.conf
fi
systemd-analyze verify /etc/systemd/system/robot-provisioner.service
systemctl daemon-reload
systemctl enable robot-provisioner.service
systemctl restart robot-provisioner.service
systemctl --no-pager --full status robot-provisioner.service
