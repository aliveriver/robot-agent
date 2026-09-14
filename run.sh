#!/bin/bash
# run.sh - robot-agent 启动脚本
#
# 自动 source ROS2 workspace（bodyctrl_msgs 等自定义消息包），
# 再在 conda agent 环境中运行 agent。
#
# 用法：
#   bash ~/robot-agent/run.sh
#   # 或加到 systemd / supervisor 里

set -e

# ── 1. ROS2 基础环境 ────────────────────────────────────────────
if [ -f /opt/ros/humble/setup.bash ]; then
    source /opt/ros/humble/setup.bash
    echo "[run.sh] ROS2 humble sourced"
fi

# ── 2. 天轶自定义消息 workspace ─────────────────────────────────
ROS2_WS="/opt/PARTITIONS/A/ros2ws"
if [ -f "${ROS2_WS}/install/setup.bash" ]; then
    source "${ROS2_WS}/install/setup.bash"
    echo "[run.sh] ros2ws sourced: ${ROS2_WS}"
else
    echo "[run.sh] WARNING: ros2ws not found at ${ROS2_WS}"
fi

# ── 3. 切换到 agent 目录并运行 ──────────────────────────────────
AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${AGENT_DIR}"
echo "[run.sh] Starting robot-agent from ${AGENT_DIR}"

# systemd/nohup 等非交互 shell 不会自动定义 `conda activate`，
# 显式加载 conda.sh，确保总是使用机器人已配置的 agent 环境。
CONDA_SH="${CONDA_SH:-/home/nvidia/miniconda3/etc/profile.d/conda.sh}"
if [ -f "${CONDA_SH}" ]; then
    source "${CONDA_SH}"
fi

if ! conda activate agent 2>/dev/null; then
    echo "[run.sh] ERROR: cannot activate conda env: agent" >&2
    exit 1
fi
echo "[run.sh] conda env: agent"

exec python -m src.robot_agent.app "$@"
