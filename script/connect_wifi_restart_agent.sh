#!/bin/bash
# 手动连接指定 Wi-Fi，然后通过项目 run.sh 重启 robot-agent。
# 用法：bash script/connect_wifi_restart_agent.sh "Wi-Fi 名" "Wi-Fi 密码"

set -euo pipefail

SSID="${1:-}"
PASSWORD="${2:-}"

if [ -z "${SSID}" ] || [ -z "${PASSWORD}" ]; then
    echo "用法: $0 \"Wi-Fi 名\" \"Wi-Fi 密码\"" >&2
    exit 2
fi

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${AGENT_DIR}/robot-agent.log"

echo "[wifi] 正在连接: ${SSID}"
sudo nmcli device wifi rescan || true
sudo nmcli device wifi connect "${SSID}" password "${PASSWORD}"

# 保留连接配置以便手动重连，但禁止开机自动抢占其他 Wi-Fi。
sudo nmcli connection modify "${SSID}" connection.autoconnect no

echo "[wifi] 已连接，IPv4 地址:"
nmcli -g IP4.ADDRESS device show | sed '/^$/d'

echo "[agent] 正在重启"
pkill -f '[s]rc.robot_agent.app' 2>/dev/null || true
sleep 2
cd "${AGENT_DIR}"
nohup bash ./run.sh > "${LOG_FILE}" 2>&1 < /dev/null &
echo "[agent] 已启动，PID=$!，日志=${LOG_FILE}"
