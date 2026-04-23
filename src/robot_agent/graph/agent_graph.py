"""
agent_graph.py — LangGraph 主图定义

将所有节点串联成执行图，定义边和条件路由。

执行顺序：
  wake_guard
    ↓（skip → END）
  memory_recall
    ↓
  scene_capture  （可选，有图像关键词才抓图）
    ↓
  kb_retrieve
    ↓
  tool_route
    ↓（有 tool_requests → tool_execute）
  tool_execute
    ↓
  response_gen
    ↓
  speak_output
    ↓
  memory_persist
    ↓
  END
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.robot_agent.graph.nodes.kb_retrieve import kb_retrieve
from src.robot_agent.graph.nodes.memory_persist import memory_persist
from src.robot_agent.graph.nodes.memory_recall import memory_recall
from src.robot_agent.graph.nodes.response_gen import response_gen
from src.robot_agent.graph.nodes.scene_capture import scene_capture
from src.robot_agent.graph.nodes.speak_output import speak_output
from src.robot_agent.graph.nodes.tool_execute import tool_execute
from src.robot_agent.graph.nodes.tool_route import tool_route
from src.robot_agent.graph.nodes.wake_guard import wake_guard
from src.robot_agent.graph.state import AgentState


def _should_skip(state: AgentState) -> str:
    """
    条件路由：wake_guard 之后判断是否跳过后续节点。
    - __SKIP__ / __STOP__ / __EXIT__ → 直接结束（不生成 LLM 回复）
    - 其他 → 继续正常流程
    """
    if state.response_text in ("__SKIP__", "__STOP__", "__EXIT__"):
        return "end"
    return "continue"


def _should_run_tools(state: AgentState) -> str:
    """条件路由：是否有 Tool 需要执行"""
    if state.tool_requests:
        return "run_tools"
    return "skip_tools"


def build_graph() -> StateGraph:
    """构建并编译 LangGraph 状态图"""
    graph = StateGraph(AgentState)

    # ── 注册节点 ─────────────────────────────────────────────
    graph.add_node("wake_guard", wake_guard)
    graph.add_node("memory_recall", memory_recall)
    graph.add_node("scene_capture", scene_capture)
    graph.add_node("kb_retrieve", kb_retrieve)
    graph.add_node("tool_route", tool_route)
    graph.add_node("tool_execute", tool_execute)
    graph.add_node("response_gen", response_gen)
    graph.add_node("speak_output", speak_output)
    graph.add_node("memory_persist", memory_persist)

    # ── 入口 ─────────────────────────────────────────────────
    graph.set_entry_point("wake_guard")

    # ── wake_guard → 条件分叉 ────────────────────────────────
    graph.add_conditional_edges(
        "wake_guard",
        _should_skip,
        {
            "end": END,
            "continue": "memory_recall",
        },
    )

    # ── 线性流程 ─────────────────────────────────────────────
    graph.add_edge("memory_recall", "scene_capture")
    graph.add_edge("scene_capture", "kb_retrieve")
    graph.add_edge("kb_retrieve", "tool_route")

    # ── tool_route → 条件分叉（有无 Tool）───────────────────
    graph.add_conditional_edges(
        "tool_route",
        _should_run_tools,
        {
            "run_tools": "tool_execute",
            "skip_tools": "response_gen",
        },
    )

    graph.add_edge("tool_execute", "response_gen")
    graph.add_edge("response_gen", "speak_output")
    graph.add_edge("speak_output", "memory_persist")
    graph.add_edge("memory_persist", END)

    return graph.compile()


# 模块级别编译好的图实例（单例）
agent_graph = build_graph()
