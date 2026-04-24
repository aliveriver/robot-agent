#!/bin/bash
set -e  # 遇到错误立即退出

# 定义颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}      Robit 一键部署脚本 (Install Script)${NC}"
echo -e "${GREEN}=========================================${NC}"

# 检查是否安装了 conda
if ! command -v conda &> /dev/null; then
    echo -e "${RED}错误: 未找到 conda。请先安装 Miniconda 或 Anaconda。${NC}"
    exit 1
fi

# 1. Conda 环境配置
echo -e "${YELLOW}[1/7] 配置 Conda 环境...${NC}"

# 尝试找到 conda 的初始化脚本
CONDA_BASE=$(conda info --base)
if [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    source "$CONDA_BASE/etc/profile.d/conda.sh"
else
    echo -e "${RED}无法找到 conda.sh，尝试直接使用当前 shell...${NC}"
fi

# 创建环境（如果不存在）
if conda info --envs | grep -q "^robit "; then
    echo "环境 'robit' 已存在，准备激活..."
else
    echo "创建 conda 环境 'robit' (python=3.10)..."
    conda create -n robit python=3.10 -y
fi

# 激活环境
conda activate robit
if [ "$CONDA_DEFAULT_ENV" != "robit" ]; then
    echo -e "${RED}错误: 无法激活环境 'robit'。请检查 conda 配置。${NC}"
    exit 1
fi
echo "当前环境: $CONDA_DEFAULT_ENV"

# 2. 安装主要依赖
echo -e "${YELLOW}[2/7] 安装主要依赖...${NC}"
pip install -r requirements.txt
pip install transformers
pip install opencv-python
pip install torchvision==0.18.0

# 3. 安装系统依赖 (主要针对 Linux)
echo -e "${YELLOW}[3/7] 安装系统依赖...${NC}"
if command -v apt-get &> /dev/null; then
    echo "检测到 apt-get，正在安装 build-essential, cmake, python3-dev, libspeexdsp-dev, portaudio19-dev..."
    sudo apt-get update
    sudo apt-get install -y build-essential cmake python3-dev libspeexdsp-dev portaudio19-dev
else
    echo -e "${YELLOW}警告: 未检测到 apt-get，跳过系统依赖安装。非 Debian/Ubuntu 系统请手动确认已安装 build-essential, cmake, libspeexdsp-dev 和 portaudio19-dev。${NC}"
fi

# 4. 安装 pysilero 和 pyrnnoise
echo -e "${YELLOW}[4/7] 配置 pysilero...${NC}"
ARCH=$(uname -m)
echo "当前架构: $ARCH"

if [ "$ARCH" = "x86_64" ]; then
    echo "检测到 x86_64 架构，直接通过 pip 安装 pysilero..."
    pip install pysilero==0.1.1
else
    echo "检测到非 x86_64 架构 (如 ARM)，开始编译安装 pyrnnoise..."
    
    # 清理可能存在的旧文件
    if [ -d "pyrnnoise" ]; then
        echo "发现旧的 pyrnnoise 目录，正在清理..."
        rm -rf pyrnnoise
    fi
    
    # 克隆源码
    echo "正在克隆 pyrnnoise 源码..."
    git clone --recursive https://github.com/pengzhendong/pyrnnoise.git
    
    # 编译
    echo "开始编译 pyrnnoise..."
    cd pyrnnoise
    cmake -B pyrnnoise/build -DCMAKE_BUILD_TYPE=Release
    cmake --build pyrnnoise/build --target install
    
    # 安装 Python 包
    echo "安装 pyrnnoise python 包..."
    python -m pip install . --no-cache-dir -v
    
    cd ..
    
    # 安装 pysilero
    echo "安装 pysilero..."
    pip install pysilero==0.1.1
fi

# 5. 安装 PortAudio 和 SoundDevice
echo -e "${YELLOW}[5/7] 安装 PortAudio 和 SoundDevice...${NC}"
if [ -f "install_portaudio.sh" ]; then
    chmod +x install_portaudio.sh
    ./install_portaudio.sh
else
    echo -e "${RED}错误: 未找到 install_portaudio.sh 脚本。尝试手动安装...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y portaudio19-dev python3-pyaudio libasound2-dev
    fi
    pip uninstall -y sounddevice
    pip install sounddevice
fi

# 6. 解决版本冲突并安装额外库
echo -e "${YELLOW}[6/7] 解决版本冲突并安装额外库...${NC}"
# 修复 numpy 和 opencv 版本冲突
pip install numpy==1.26.4
pip install "opencv-python<4.10"

# 其他依赖
pip install pyaudio
pip install websocket-client
pip install sherpa_onnx
pip install rospkg
pip install dashscope
pip install speexdsp

# 7. 安装 RKNN 模型推理相关依赖
echo -e "${YELLOW}[7/8] 安装 RKNN 模型推理相关依赖...${NC}"
# 安装 rknn-toolkit2 (需要 --no-deps 避免冲突)
# 假设 rknn-wheels 目录在当前脚本同级目录下
if [ -d "rknn_wheels" ]; then
    echo "发现 rknn_wheels 目录，开始安装 RKNN 相关依赖..."
    
    # 安装 rknn_toolkit2
    pip install --no-deps ./rknn_wheels/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
    
    # 限制 onnx 版本
    pip install "onnx==1.16.1"
    pip install onnxsim
    
    # 安装 rknn_toolkit_lite2
    pip install --no-deps ./rknn_wheels/rknn_toolkit_lite2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
    
    # 其他 RKNN 依赖
    pip install ruamel.yaml
    pip install "fast-histogram>=0.11"
    pip install onnxoptimizer==0.3.8
    
    # 复制库文件 (需要 sudo 权限)
    if command -v sudo &> /dev/null; then
        echo "正在复制 librknnrt.so 到系统目录..."
        sudo cp ./rknn_wheels/librknnrt.so /usr/lib/
        
        echo "正在复制 rknn_server 到系统目录..."
        sudo cp ./rknn_wheels/bin/* /usr/bin/
    else
        echo -e "${RED}错误: 未找到 sudo 命令，无法复制系统库文件。请手动执行以下命令：${NC}"
        echo "cp ./rknn_wheels/librknnrt.so /usr/lib/"
        echo "cp ./rknn_wheels/bin/* /usr/bin/"
    fi
else
    echo -e "${RED}警告: 未找到 rknn_wheels 目录，跳过 RKNN 相关依赖安装。${NC}"
    echo -e "${YELLOW}请确保已下载 rknn_wheels 并放置在当前目录下。${NC}"
fi

# 8. 安装 SenseVoice 推理依赖
echo -e "${YELLOW}[8/8] 安装 SenseVoice 推理依赖...${NC}"
pip install soundfile librosa

# 9. 完成
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}      部署完成！ (Installation Complete)${NC}"
echo -e "${GREEN}=========================================${NC}"
echo "请按照以下步骤启动程序："
echo -e "1. 激活环境: ${YELLOW}conda activate robit${NC}"
echo -e "2. 运行程序: ${YELLOW}python robit_v11.py${NC}"
echo -e "${GREEN}=========================================${NC}"
