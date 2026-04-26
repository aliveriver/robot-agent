#!/bin/bash
# PortAudio 库安装脚本 - 适用于不同的 Linux 发行版

echo "开始安装 PortAudio 库..."

# 检测操作系统类型
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    VER=$VERSION_ID
elif type lsb_release >/dev/null 2>&1; then
    OS=$(lsb_release -si)
    VER=$(lsb_release -sr)
elif [ -f /etc/lsb-release ]; then
    . /etc/lsb-release
    OS=$DISTRIB_ID
    VER=$DISTRIB_RELEASE
elif [ -f /etc/debian_version ]; then
    OS=Debian
    VER=$(cat /etc/debian_version)
else
    OS=$(uname -s)
    VER=$(uname -r)
fi

echo "检测到操作系统: $OS $VER"

# 根据不同的发行版安装 PortAudio
case $OS in
    "Ubuntu"*|"Debian"*)
        echo "在 Ubuntu/Debian 系统上安装 PortAudio..."
        sudo apt-get update
        sudo apt-get install -y portaudio19-dev python3-pyaudio
        sudo apt-get install -y libasound2-dev
        ;;
    "CentOS"*|"Red Hat"*|"Fedora"*)
        echo "在 CentOS/RHEL/Fedora 系统上安装 PortAudio..."
        if command -v dnf &> /dev/null; then
            sudo dnf install -y portaudio-devel alsa-lib-devel
        else
            sudo yum install -y portaudio-devel alsa-lib-devel
        fi
        ;;
    "Arch"*)
        echo "在 Arch Linux 系统上安装 PortAudio..."
        sudo pacman -S portaudio
        ;;
    "openSUSE"*)
        echo "在 openSUSE 系统上安装 PortAudio..."
        sudo zypper install portaudio-devel
        ;;
    *)
        echo "未识别的操作系统: $OS"
        echo "请手动安装 PortAudio 开发库"
        echo "常见的包名: portaudio19-dev, portaudio-devel, portaudio"
        exit 1
        ;;
esac

# 验证安装
echo "验证 PortAudio 安装..."
if pkg-config --exists portaudio-2.0; then
    echo "✓ PortAudio 库安装成功"
    pkg-config --modversion portaudio-2.0
else
    echo "⚠ PortAudio 库可能未正确安装"
fi

# 重新安装 sounddevice
echo "重新安装 sounddevice..."
pip uninstall -y sounddevice
pip install sounddevice

echo "安装完成！现在可以尝试运行您的程序了。"