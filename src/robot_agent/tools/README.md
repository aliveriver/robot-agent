# Tools 模块指南

`tools` 目录是机器人 Agent 的**工具库与工具注册中心**。这里的代码定义了 LLM（大语言模型）可以主动调用的“函数/能力”（Tool Calling / Function Calling）。

当用户发出指令（如“现在几点了”或“我要休息了”）时，LLM 会分析意图并决定调用这里的某个工具，从而使大模型具备与物理设备、内存数据库及互联网进行交互的能力。

---

## 目录结构与模块说明

### 1. `registry.py`
**功能说明**：工具注册中心（Tool Registry）。
- 这是一个进程级的单例类（`ToolRegistry`），负责统一管理所有的工具函数及其元数据（参数的 JSON Schema 和功能描述）。
- **供两端使用**：
  1. 给 LLM 提供 JSON Schema 格式的工具列表描述（供 LLM 决策是否调用以及如何传参）。
  2. 当 LLM 决定调用某个工具时，根据工具名（String）映射并执行实际的 Python 异步函数。

### 2. `builtin/` 目录
**功能说明**：存放系统开箱即用的内置工具分类。
- **`control_tools.py`（控制类）**：会直接改变机器人当前运行状态的工具。该类工具执行后通常会返回 `state_updates`，强行重写 Agent 的核心状态（如 `wake_state` 等）。
  - *示例*：`sleep_robot`（休眠退出）、`start_voice_clone`（触发声音克隆录音流程）。
- **`device_tools.py`（设备信息类）**：只读类型的查询工具，获取机器人本体或当前环境的状态信息。
  - *示例*：`get_time`（获取系统当前时间）、`get_robot_status`（获取硬件或 Agent 的状态）。
- **`memory_tools.py`（记忆与存储类）**：处理长期记忆、用户画像检索与写入的工具。
  - *示例*：`search_memory`（检索sqlite库里的历史记忆）、`save_profile_fact`（保存发现的用户偏好/事实）。

---

## 什么样的功能应该放到这里？

**应该放入 `tools` 的功能（What belongs here）：**
任何**需要大模型（LLM）通过意图理解来主动触发的独立功能**，都应该封装成 Tool 放在这里。典型场景包括：
1. **外部 API 查询**：查天气、查股票、搜索维基百科。
2. **硬件/系统交互**：控制机器人移动（向前走、挥手）、控制智能家居（开关灯）、查询设备电量。
3. **长期记忆与知识库操作**：向数据库或向量检索库中搜索特定的专有知识。
4. **特定的业务流拦截**：比如用户要求“播放某首歌”，大语言模型不需要自己写歌出来，而是调用 `play_music` 工具即可。

**不应该放入这里的功能（反模式）：**
- ❌ **Agent 的底层流转逻辑**：如 ASR（语音识别）、TTS（语音合成）、意图分类器。这些属于核心管线或能力组件，应该放在 `capabilities` 目录或编排在 `graph` 模块里。
- ❌ **纯算法/工具类数学函数**：如果是给其他 Python 代码调用的基础工具（比如字符串切分、音频数组转换），应该放入 `utils` 目录。这里只放**由 LLM 触发**的业务执行器。

---

## 如何修改以及二次开发 (二开指南)

如果你想赋予机器人大模型一个新的能力（例如：“查询天气”），请按照以下步骤进行二开：

### 第一步：编写工具函数
在 `builtin/` 目录下（或针对新业务新建一个 `.py` 文件，例如 `weather_tools.py`），编写你的工具函数。
**基本约定**：
- 函数必须是异步（`async def`）。
- 第一个参数必须接收 `state: AgentState`，以便你可以访问当前的聊天上下文和用户ID。
- 其他参数要通过 `**kwargs` 或显式命名来接收（这些参数由大模型根据你提供的 Schema 传入）。
- 返回值需是一个 `dict`，通常包含该工具执行的结果信息。

```python
# src/robot_agent/tools/builtin/weather_tools.py
from src.robot_agent.graph.state import AgentState

async def get_weather(state: AgentState, city: str, **kwargs) -> dict:
    """获取指定城市的天气信息 (供 LLM 参考)"""
    # 你的业务逻辑：调用气象API...
    weather_info = f"{city}今天是晴天，25度"
    return {"weather": weather_info, "ok": True}
```

### 第二步：修改 `registry.py` 注册该工具
打开 `src/robot_agent/tools/registry.py`，在 `ToolRegistry._register_builtins()` 方法中注册你的函数，并详细编写 `description` 和 `parameters`（JSON Schema 格式）。

```python
# registry.py 内部的 _register_builtins 方法
from src.robot_agent.tools.builtin.weather_tools import get_weather

# ...
self.register(
    "get_weather",
    get_weather,
    description="当用户询问某个城市的天气时使用这个工具获取。必须提供城市名称。",
    parameters={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "要查询天气的城市名称，例如'北京'、'上海'。"
            }
        },
        "required": ["city"],
        "additionalProperties": False,
    },
)
```

**⚠️ 注意事项：**
1. **Schema 描述极其重要**：`description` 和 `parameters` 的 `description` 将直接作为 Prompt 发给模型。写得越清晰、界限越明确，大模型越不容易调错（Hallucination）。
2. **状态重写机制**：如果你的工具希望直接把自己的结果通过语音播报出来（不再让 LLM 生成废话），可以在返回的 dict 中增加 `"state_updates": {"response_text": "你想播报的话"}`，这会直接拦截后续的节点输出（可参考 `control_tools.py` 中的 `sleep_robot` 实现）。