# Graph 模块指南

`graph` 目录是当前机器人 Agent 的**“主脑”与“中枢神经系统”**。它基于 [LangGraph](https://python.langchain.com/docs/langgraph) 框架，将机器人一次对话的完整生命周期（唤醒 -> 记忆读取 -> 视觉感知 -> 检索 -> 工具调用 -> 思考 -> 发出声音 -> 记忆固化）编排成了一个有向无环图（DAG）形式的状态机。

---

## 目录结构与核心模块说明

本目录呈现了高度结构化的状态流水线（Pipeline）设计：

### 1. `state.py`
**功能说明**：全链路数据总线（AgentState）。
- 定义了贯穿整个 Graph 执行流的共享数据载体 `AgentState`（基于 Pydantic）。
- 它就像传送带，所有节点函数接收这个 State，读取需要的信息，处理完后返回要修改的字段，由 LangGraph 自动合并状态传递给下一个节点。

### 2. `agent_graph.py`
**功能说明**：图拓扑网络与路由定义。
- 负责实例化 `StateGraph`，把各个“节点（Node）”串联起来，画出执行流向线（Edge）和条件分支（Conditional Edges）。
- 梳理了核心编排顺序：`wake_guard` → `memory_recall` → `scene_capture` → `kb_retrieve` → `tool_route` → (可选 `tool_execute`) → `response_gen` → `speak_output` → `memory_persist` → `END`。

### 3. `nodes/` 目录
**功能说明**：流水线上的所有“加工节点”。每个文件对应图中的一个处理步骤，接收的输入为 `AgentState`，返回值为对 `AgentState` 的局部字典更新。
- **`wake_guard.py`**：决定是否处理这句话（如果是休眠状态且没有唤醒词，则拦截短路）。
- **`memory_recall.py`** & **`memory_persist.py`**：长短期记忆的拉取与对话结束后的落盘保存。
- **`scene_capture.py`**：视觉感知（若对话需要看东西，触发摄像头或读取当前帧）。
- **`kb_retrieve.py`**：RAG（检索增强生成），通过知识库召回相关企业内容提供给大模型。
- **`tool_route.py`** & **`tool_execute.py`**：决定大模型是否要挂载并调用外部 Tools（如日历、天气、设备控制）。
- **`response_gen.py`**：核心模型思考，结合前面所有的上下文，生成最终文本回复（如果被 tool 或 guard 提前截断，这一步会跳过）。
- **`speak_output.py`**：将大模型生成的文本推给下游的 TTS（语音合成）模块并进行播放状态控制。

---

## 什么样的功能应该放到这里？

**应该放入 `graph` 的功能（What belongs here）：**
1. **控制流抽象**：图节点的增删改，边（Edge）的重定向，以及带条件判断的路由规则（如“检查到某个状态就直接结束对话”）。
2. **全局上下文（State）的定义**：如果你要给机器人新增一个全局属性（比如当前的系统剩余电量标示、用户的情绪分析结果），要定义在 `state.py` 中。
3. **流程级的加工逻辑**：将复杂的“思考框架”（如 ReAct 模式、反思自查模式）转化为具体的 Node。

**不应该放入这里的功能（反模式）：**
- ❌ **具体的硬件或网络请求实现**：不要在节点里去用 `cv2.VideoCapture` 或者写长篇的 SQLAlchemy SQL 查询代码。节点应该是“极薄的”，复杂的底层能力如 RAG 检索应该写在 `capabilities/` 中，由节点去调用它们。
- ❌ **大模型的独立工具（Tools）**：如果某个功能是通过大模型 Function Calling 去主动调用的（如查天气），应存放在 `tools/` 目录下。

---

## 如何修改以及二次开发 (二开指南)

如果你想往对话流水线中**插入一个新的环节**（例如：增加一个“用户情绪分析”的步骤，基于情感动态调整回复），请遵循以下三大核心步骤：

### 第一步：在 `state.py` 扩充数据总线
如果要增加情感分析结果，首先打开 `state.py` 增加字段：
```python
class AgentState(BaseModel):
    # ... 原有代码 ...
    user_emotion: str = "neutral"  # 新增：用户当前的情绪判断
```

### 第二步：在 `nodes/` 编写新的 Node
编写一个新的处理节点。进入 `src/robot_agent/graph/nodes/` 创建 `emotion_analysis.py`：
```python
# nodes/emotion_analysis.py
from src.robot_agent.graph.state import AgentState
from src.robot_agent.capabilities.nlp.emotion import analyze_emotion # 假设你在能力层写了这个

async def emotion_analysis(state: AgentState) -> dict:
    """分析用户上了一句话的情绪，并写入状态"""
    text = state.normalized_text
    if not text:
        return {}
        
    detected_emotion = await analyze_emotion(text)
    
    # 只需要返回需要被 LangGraph 去更新（覆盖）的 state 字段键值对
    return {"user_emotion": detected_emotion}  
```

### 第三步：在 `agent_graph.py` 将新节点“连入”管道
打开 `agent_graph.py`，决定你要将这根新管子插在哪里。比如插在 `wake_guard` 和 `memory_recall` 之间：

```python
from src.robot_agent.graph.nodes.emotion_analysis import emotion_analysis

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    
    # 原有逻辑省略 ...
    graph.add_node("wake_guard", wake_guard)
    graph.add_node("emotion_analysis", emotion_analysis) # 1. 注册新节点
    graph.add_node("memory_recall", memory_recall)
    
    # 2. 修改网络连接线（Edge）
    # 原来是 wake_guard 连向 memory_recall
    graph.add_conditional_edges(
        "wake_guard",
        _should_skip,
        {
            "end": END,
            "continue": "emotion_analysis", # 拦截原本走向 memory_recall 的线
        },
    )
    
    # 从 emotion_analysis 连出，导回原本的 memory_recall
    graph.add_edge("emotion_analysis", "memory_recall")
    
    # ... 其余原样 ...
    return graph.compile()
```

这样你就成功重塑了系统的大脑思考网络，无需改动庞大繁杂的旧逻辑接口。