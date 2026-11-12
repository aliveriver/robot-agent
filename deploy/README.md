# BLE hotspot provisioning

The independent system service uses the Jetson's existing `python3-dbus`,
`python3-gi`, BlueZ and NetworkManager packages. It does not load the Conda
voice Agent or require its API keys. No pip packages are installed into system
Python. The optional BLE daemon's Python dependencies are supplied by these
Ubuntu packages rather than the Agent's Conda requirements.

From the robot checkout, install the reviewed package:

```bash
sudo bash ~/robot-agent/deploy/install-provisioner.sh
```

Only final files are copied to `/opt/robot-provisioner`; they are root-owned.
Non-secret interface/port configuration is in `/etc/robot-provisioner.conf`.
Credentials travel over an encrypted, bonded BLE connection and NetworkManager
D-Bus; passwords are neither placed in process arguments nor logged. BlueZ
Just Works pairing encrypts the link, but does not provide a physical ownership
proof or protection against an active pairing impersonator. Use within the
intended trusted setup environment; no ownership system is added by this change.

The service advertises `XL-xxxx` and the UUID defined in `protocol.py`.
The request characteristic takes newline-delimited UTF-8 JSON in 20-byte chunks.
The status characteristic is polled; each phone sees its own operation status.
Only the configured Wi-Fi device is modified. A failed transaction removes its
new profile and attempts to restore the previous Wi-Fi profile; Ethernet and
its ROS2 addresses are never selected. The real failure/rollback path still
requires network integration testing.

Android needs a native APK/development build. Expo Go and simulators cannot
validate the actual BLE connection. Hotspot SSID/password are entered manually;
ordinary Android apps cannot reliably read or silently enable a sharing hotspot.

## Control without the external microphone

`robot-control.service` uses the existing WebSocket handlers without loading
ASR, TTS or microphone capture at startup. Install it as a user service:

```bash
mkdir -p ~/.config/systemd/user
cp ~/robot-agent/deploy/robot-control.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start robot-control.service
```

It conflicts with the full `robot-agent.service` to keep one control endpoint.
Do not enable both profiles for boot. To later use the full voice Agent, stop
and disable robot-control before enabling robot-agent after DJI validation.
Wake/sleep state is process-local; switching service profiles resets it.

## Recovery and rollback

Read provisioning logs with `journalctl -u robot-provisioner.service -b`.
Stop/disable the provisioner to remove its live GATT advertisement. The daemon
unregisters its agent and restores the previous Pairable setting when stopped.
Existing paired microphones are retained. Do not delete pre-existing Wi-Fi
profiles, models, trajectories or databases when reverting this feature.
