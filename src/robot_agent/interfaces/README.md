# Interfaces 模块指南

`interfaces` 目录是机器人 Agent 的**外部边界与物理接口层**（类似于六边形架构中的“端口和适配器”/“基础设施层”）。

这里封装了所有与底层硬件（传感器、执行器）以及外部通信协议打交道的代码。Agent 核心大脑（大模型、状态图）通过调用或监听这些接口来感知世界并做出物理呈现。

---

## 目录结构与现有模块说明

目前该目录下主要包含 `audio/` 音频硬件相关的实现：

### 1. `audio/device_resolver.py`
**功能说明**：系统的音频设备管家（声卡解析器）。
- 提供跨平台的麦克风和扬声器设备扫描、标号解析功能。
- 处理复杂的声卡名称模糊匹配（如“USB Audio”、“Pulse”），解决了不同操作系统（Linux ALSA/PulseAudio、Windows DirectSound）下设备 ID 经常漂移的问题。

### 2. `audio/microphone.py`
**功能说明**：麦克风采集与 VAD（端点检测）接口。
- 封装了 `sounddevice` 库提供持续的音频流监听（`MicrophoneListener`）。
- 包含基于幅值（Volume/Energy）算法的轻量级 VAD 逻辑，能自动把麦克风输入切分成包含完整说话内容的“语音段（Segments）”，并把纯净的 numpy 数组通过回调抛给上游（如 ASR 识别管线）。

---

## 什么样的功能应该放到这里？

**应该放入 `interfaces` 的功能（What belongs here）：**
任何**与物理世界、底层操作系统硬件、或者特定通信协议直接绑定**的功能边界。典型场景包括：
1. **传感器驱动与监听器**：麦克风监听（现有）、摄像头画面拉取器（如 OpenCV 拉流）、激光雷达（LiDAR）读取桥接。
2. **执行器控制器**：扬声器播放器（Speaker 播放 WAV/PCM）、电机底盘运动控制（控制轮子前进后退）。
3. **外部协议服务器/客户端**：
   - 比如基于 WebSockets 的前端通信接口（接受网页端点按操作）。
   - 特定中间件层（如 ROS 1 / ROS 2 的 Publisher 和 Subscriber 节点适配代码）。

**不应该放入这里的功能（反模式）：**
- ❌ **感知数据的解析逻辑**：比如把语音转成文字（ASR）、把文字转成语音（TTS）。`interfaces` 只负责“录音”和“播音”，把音转成字的大模型逻辑应该放在 `capabilities`（能力层）中。
- ❌ **控制决策与 Agent 流转逻辑**：例如“听到主人的声音就向左转”。`interfaces` 只提供“向左转”的单一函数调用，何时调用由 `graph` (状态图) 层控制。
- ❌ **被 LLM 调用的独立功能包**：如果是通过 Function Calling 被 LLM 提取参数后调用的具体业务方法，应该去 `tools/` 目录。

---

## 如何修改以及二次开发 (二开指南)

如果你希望为机器人加入新感官（如视觉、网络接口等），请按以下规范扩充：

### 1. 扩充音频接口（例如：新增扬声器播放接口）
目前已有 `microphone.py`（听），如果系统不再依赖外部播放服务而是自己接管扬声器，您可以在 `audio/` 内增加 `speaker.py`：
```python
# src/robot_agent/interfaces/audio/speaker.py
import sounddevice as sd
import numpy as np

class SpeakerPlayer:
    def __init__(self, device_id: int):
        self.device_id = device_id
        
    def play_pcm(self, audio_data: np.ndarray, sample_rate: int):
        """播放生成的 TTS 音频数组"""
        sd.play(audio_data, samplerate=sample_rate, device=self.device_id)
        sd.wait() # 阻塞直到播完
```

### 2. 引入视觉抽象（例如：新建 `vision/` 目录）
你可以创建 `src/robot_agent/interfaces/vision/camera.py`，把 OpenCV 的物理摄像头逻辑包死在这里，对外只吐出 `RGB 图像矩阵` 或 `Base64 照片`：
```python
# src/robot_agent/interfaces/vision/camera.py
import cv2

def capture_current_frame() -> bytes:
    """获取一张当前视角的照片供多模态模型查看"""
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    cap.release()
    if ret:
        _, buffer = cv2.imencode('.jpg', frame)
        return buffer.tobytes()
    return b""
```

### 3. 引入 ROS 桥接层（新建 `ros/` 目录）
如果在 `bootstrap` 启动了 ROS 节点，此时你需要一个专门的地方与 ROS Topic 交互。可以在此处增加发布与订阅封装，而向核心业务屏蔽掉 `rospy`。
```python
# src/robot_agent/interfaces/ros/chassis.py
import rospy
from geometry_msgs.msg import Twist

class ChassisInterface:
    def __init__(self):
        self.pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
        
    def move_forward(self, speed: float = 0.5):
        msg = Twist()
        msg.linear.x = speed
        self.pub.publish(msg)
```

**核心设计原则（DI/控制反转）**：所有的 `interfaces` 对外暴露的数据类型应该尽可能通用（`numpy array`、`bytes`、`str`），不要让 Agent 层代码被迫 `import cv2` 或者 `import sounddevice`。