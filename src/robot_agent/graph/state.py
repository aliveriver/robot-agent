"""
state.py — LangGraph 全局状态对象定义

AgentState 是贯穿整个 Graph 执行流的共享数据载体。
每个 Graph 节点读取 state、修改部分字段后返回，由 LangGraph 合并。

字段命名原则：
- input_*   原始或清洗后的输入
- recalled_* 从记忆/知识库召回的内容
- tool_*    Tool 调用的请求与结果
- response_* 最终输出
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """LangGraph 状态对象，在所有节点之间传递"""

    # ── 会话标识 ─────────────────────────────────────────────
    session_id: str = ""
    user_id: str = "default"

    # ── 输入 ─────────────────────────────────────────────────
    input_text: str = ""          # ASR 原始输入
    normalized_text: str = ""     # 清洗/去重/回声抑制后
    language: str = "cn"          # cn | en
    emotion: Optional[str] = None # happy | sad | angry | neutral | ...

    # ── 机器人运行状态 ────────────────────────────────────────
    wake_state: str = "sleep"     # sleep | awake
    interrupt_revision: int = 0   # 创建该轮任务时看到的 TTS 中断版本号
    interrupted: bool = False     # 是否被用户打断

    # ── 记忆召回 ─────────────────────────────────────────────
    recent_messages: List[Dict[str, Any]] = Field(default_factory=list)
    recalled_memories: List[Dict[str, Any]] = Field(default_factory=list)

    # ── 知识库检索结果 ────────────────────────────────────────
    kb_chunks: List[Dict[str, Any]] = Field(default_factory=list)

    # ── 视觉 ─────────────────────────────────────────────────
    scene_image_b64: Optional[str] = None   # base64 编码的图像
    scene_summary: Optional[str] = None     # 场景描述文本

    # ── Tool 调用 ────────────────────────────────────────────
    tool_requests: List[Dict[str, Any]] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)

    # ── 响应 ─────────────────────────────────────────────────
    response_text: str = ""
    response_meta: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        # 允许节点返回字典局部更新 state
        arbitrary_types_allowed = True
