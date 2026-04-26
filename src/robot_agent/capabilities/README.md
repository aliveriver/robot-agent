# Capabilities 模块指南

`capabilities` 目录是当前机器人 Agent 的**核心能力层（算法与模型服务）**。如果说 `graph` 是指挥流转的大脑，`interfaces` 是接触物理世界的感官，那么 `capabilities` 则是大脑中不同区域的**专业神经中枢**（如语言区、听觉区、视觉区）。这里封装了系统所依赖的所有核心 AI 模型技术与数据处理管线。

---

## 目录结构与模块说明

本目录按照具体的“能力领域”划分为多个子模块：

### 1. `asr/` (自动语音识别)
**职责**：将人类的原始声音转为文本。
- **组成部分**：离线模型适配器（如基于 Sherpa-ONNX 的 SenseVoice 模型加载 `sherpa_adapter.py`），以及伴随的文本后处理流（如去除标点、剥离模型自带的情感标签 `<|happy|>` 的 `text_cleaner.py`）。

### 2. `tts/` (文本到语音)
**职责**：将大模型生成的文本转化为合成音频。
- **组成部分**：云端流式语音合成适配器（如对接大厂接口的 `minimax_adapter.py`）。负责建立 WebSocket 或 HTTP 连接流式拉取音频数据，并实现极低延迟的缓冲播放（包含被用户打断时立即停播的熔断逻辑）。

### 3. `llm/` (大语言模型封装)
**职责**：提供文本推理与多模态感知能力基础。
- **组成部分**：统一的大模型客户端封装（如基于 LangChain 的 `chat_model.py`）。它隐藏了参数配置细节（温度、Token 限制、API Key 读取），对外直接吐出标准的 `ChatOpenAI` 或大模型实例供上层（`graph`）直接使用。

### 4. `memory/` (长短期记忆)
**职责**：管理和检索与用户相关的知识、画像或对话历史。
- **组成部分**：向量数据库（如 ChromaDB）或 SQLite（`sqlite_store.py`）存储封装。向上层提供通用的 `search_memories()` 或 `save_profile_fact()` 的高级语义接口。

### 5. `sentiment/` (情感计算)
**职责**：判断用户传入文本或音频的情绪极性。
- **组成部分**：本地 NLP 模型适配器（如 `roberta_analyzer.py`），提供对文本情绪（喜怒哀乐）的细粒度检测。

### 6. `vision/` (机器视觉) & `voice/` (声音克隆)
**职责**：封装特定领域的专属能力。如声音采集重训练管线（`voice_cloner.py`）或对视觉图像的特征提取抽象。

---

## 什么样的功能应该放到这里？

**应该放入 `capabilities` 的功能（What belongs here）：**
1. **AI 模型适配层**：所有涉及 `import torch`、`import onnxruntime`、对接阿里/百度/Minimax 闭源 AI API 的代码，必须封闭在这里，对外部隐藏细节。
2. **重型算法与数据管线**：比如给图片做 Resize 和 Normalization 预处理，或者复杂的音频重采样（Resampling）与降噪（AEC/NS）算法。
3. **通用的业务领域逻辑块（Domain Logic）**：一套复杂的 RAG（知识库检索）查询拼装、重排（Rerank）逻辑。

**不应该放入这里的功能（反模式）：**
- ❌ **硬件连接层与驱动**：虽然 `tts` 要发声，但它不应该直接手写底层声卡驱动配置。它生成的 numpy 数组应该交由（或通过基于）`interfaces/audio/` 相关的统一口径播放。录音麦克风读取同理，只在 `interfaces` 进行。
- ❌ **供 LLM 调用的“工具函数”**：比如查天气、查日历的业务动作工具。那个是大模型主动发起的，应放在 `tools/`。`capabilities` 里是系统的被动技能（基础设施）。
- ❌ **工作流图的调度与跳转节点**：决定先看图片还是先说话的决策，应放在 `graph`（控制流），`capabilities` 只提供“单点功能”（如只管翻译图片）。

---

## 如何修改以及二次开发 (二开指南)

如果系统需要接入一套**新的大厂 TTS（如科大讯飞 TTS）**，请按照以下规范进行扩展，避免污染外层代码：

### 第一步：在对应分类下新建实现（继承基础类）
在 `src/robot_agent/capabilities/tts/` 下创建一个 `xunfei_adapter.py`。你可以通过继承 `TTSBase` 或直接鸭子类型（Duck Typing）暴露公共方法，保证对外部节点暴露的 signature 是一致的。

```python
# capabilities/tts/xunfei_adapter.py
from src.robot_agent.capabilities.tts.base import TTSBase

class XunfeiTTSAdapter(TTSBase):
    async def speak(self, text: str, lang: str = "cn"):
        """接入讯飞云接口的具体实现"""
        # 1. 签名鉴权
        # 2. 建立 WebSocket 拉流
        # 3. 推送音频缓冲供播放
        pass
```

### 第二步：在依赖注入或工厂中切换
外部代码不应该强制硬编码引用 `MinimaxTTS`，你应该在 `capabilities/tts/__init__.py` 或专门的依赖管理层增加根据配置文件推断的逻辑：

```python
# capabilities/tts/__init__.py
from src.robot_agent.settings import settings

def get_tts():
    if settings.tts.provider == "xunfei":
        from .xunfei_adapter import XunfeiTTSAdapter
        return XunfeiTTSAdapter()
    elif settings.tts.provider == "minimax":
        from .minimax_adapter import MinimaxTTSAdapter
        return MinimaxTTSAdapter()
    # 默认 fallback 等...
```

### 第三步：外层（Graph）依然保持无感调用
基于以上良好的解耦，外层只需要在使用的地方拉取配置所指定的统一类型：
```python
# graph/nodes/speak_output.py
from src.robot_agent.capabilities.tts import get_tts

async def speak_output(state: AgentState):
    tts = get_tts() # 这里拿到的是 minimax 还是 xunfei 外层不必关心
    await tts.speak(state.response_text)
    return {}
```

设计理念：**“高内聚低耦合”**，任何一个 API 停服或者本地模型被替换升级，你只需要改写 `capabilities` 里这短短的几百行适配代码，上层的 Agent 逻辑完全不需要妥协和更改。