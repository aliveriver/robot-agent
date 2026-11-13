#!/usr/bin/env bash
# Install a nvidia user service; optional hardware must not be an ExecStartPre gate.
set -euo pipefail
agent_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$agent_root" != "$HOME/robot-agent" ]]; then
    echo 'Expected checkout at ~/robot-agent; refusing to install a different path.' >&2
    exit 1
fi
if [[ "$(loginctl show-user "$USER" -p Linger --value)" != yes ]]; then
    echo 'User linger is required for login-independent startup.' >&2
    exit 1
fi
test -x /home/nvidia/miniconda3/envs/agent/bin/python
test -f "$agent_root/src/robot_agent/bootstrap/agent_runtime.py"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
if [[ -f "$unit_dir/robot-agent.service" ]]; then
    cp -p "$unit_dir/robot-agent.service" "$unit_dir/robot-agent.service.before-resilience"
fi
install -m 0644 "$agent_root/deploy/robot-agent.service" "$unit_dir/robot-agent.service"
systemctl --user daemon-reload
systemctl --user disable --now robot-control.service
if systemctl --user enable robot-agent.service && systemctl --user restart robot-agent.service; then
    for attempt in {1..45}; do
        if curl --noproxy '*' --max-time 2 -fsS http://127.0.0.1:8765/health 2>/dev/null |
           /usr/bin/python3 -c 'import json,sys
try:
 h=json.load(sys.stdin)
 sys.exit(not (h.get("mode")=="full-agent" and h.get("components",{}).get("control",{}).get("state")=="ready"))
except (ValueError,KeyError):
 sys.exit(1)'; then
            systemctl --user show robot-agent.service -p ActiveState -p UnitFileState -p MainPID
            exit 0
        fi
        sleep 1
    done
fi
echo 'Agent did not become ready; restoring standalone control.' >&2
systemctl --user disable --now robot-agent.service || true
systemctl --user enable --now robot-control.service
exit 1
