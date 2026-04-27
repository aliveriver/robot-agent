#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_NAME="agent"
PYTHON_VERSION="3.10"

print_banner() {
  echo "========================================="
  echo "      robot-agent 安装脚本"
  echo "========================================="
}

print_step() {
  echo
  echo "[$1] $2"
}

init_conda() {
  if ! command -v conda >/dev/null 2>&1; then
    echo "[错误] 未在 PATH 中找到 conda" >&2
    exit 1
  fi

  local conda_base
  conda_base="$(conda info --base)"

  if [ -f "$conda_base/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1090
    source "$conda_base/etc/profile.d/conda.sh"
  else
    eval "$(conda shell.bash hook)"
  fi
}

ensure_env() {
  if conda env list | awk 'NR>2 {print $1}' | grep -qx "$ENV_NAME"; then
    echo "Conda 环境 '$ENV_NAME' 已存在"
  else
    echo "正在创建 conda 环境 '$ENV_NAME'，Python 版本为 $PYTHON_VERSION"
    conda create -n "$ENV_NAME" "python=$PYTHON_VERSION" -y
  fi

  conda activate "$ENV_NAME"
  echo "当前激活的 conda 环境: $CONDA_DEFAULT_ENV"
}

install_python_deps() {
  python -m pip install --upgrade pip setuptools wheel
  python -m pip install -r requirements.txt

  python -m pip install \
    "sherpa-onnx" \
    "soundfile" \
    "opencv-python<4.10"
}

prepare_runtime_dirs() {
  mkdir -p data/chroma data/camera data/voice_clone
}

smoke_check() {
  python -m compileall src >/dev/null
}

print_summary() {
  echo
  echo "========================================="
  echo "安装完成"
  echo "========================================="
  echo "下一步："
  echo "1. conda activate $ENV_NAME"
  echo "2. ./start_agent.sh"
  echo
  echo "说明："
  echo "- 本脚本不会安装系统级的音频、ROS、摄像头依赖。"
  echo "- 如果你的机器人还需要额外的设备专用依赖，请自行安装。"
}

print_banner
print_step "1/5" "初始化 conda"
init_conda

print_step "2/5" "检查并准备 conda 环境"
ensure_env

print_step "3/5" "安装 Python 依赖"
install_python_deps

print_step "4/5" "准备运行目录"
prepare_runtime_dirs

print_step "5/5" "执行冒烟检查"
smoke_check

print_summary
