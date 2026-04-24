"""
nodes/response_gen.py - 回复生成节点

负责汇总用户输入、短期上下文、记忆检索、知识检索和 Tool 结果，
构造消息后调用 LLM 生成最终回复，并将文本写回 `state.response_text`。
"""

from __future__ import annotations

from base64 import b64decode
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.llm.chat_model import get_chat_model
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)

PROMPT_DIR = Path(__file__).resolve().parents[5] / "configs" / "prompts"


def _load_prompt(filename: str) -> str:
    """读取 prompt 模板文件。"""
    path = PROMPT_DIR / filename
    if not path.exists():
        logger.warning("response_gen: prompt file not found", path=str(path))
        return ""
    return path.read_text(encoding="utf-8")


def _build_context_block(state: AgentState) -> str:
    """拼接对话上下文、记忆、知识块和 Tool 结果。"""
    parts: list[str] = []

    if state.recent_messages:
        parts.append("最近对话：")
        for msg in state.recent_messages[-6:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                parts.append(f"- {role}: {content}")

    if state.recalled_memories:
        parts.append("召回记忆：")
        for memory in state.recalled_memories:
            text = memory.get("memory_text", "")
            if text:
                parts.append(f"- {text}")

    if state.kb_chunks:
        parts.append("知识检索：")
        for chunk in state.kb_chunks:
            text = chunk.get("content", "")
            if text:
                parts.append(f"- {text}")

    if state.tool_results:
        parts.append("工具结果：")
        for result in state.tool_results:
            if not result.get("ok"):
                continue
            parts.append(f"- {result.get('tool', 'tool')}: {result.get('data', '')}")

    return "\n".join(parts)


def _build_user_message(state: AgentState, context: str) -> str:
    """构造发送给模型的用户消息文本。"""
    parts: list[str] = []

    if context:
        parts.append(context)

    if state.scene_summary:
        parts.append(f"场景摘要：{state.scene_summary}")

    parts.append(f"用户情绪：{state.emotion or 'neutral'}")
    parts.append(f"用户输入：{state.normalized_text or state.input_text}")
    return "\n\n".join(parts)


def _build_messages(state: AgentState, system_prompt: str, user_message: str) -> list[Any]:
    """根据当前状态构造 LangChain 消息列表。"""
    messages: list[Any] = [SystemMessage(content=system_prompt)]

    if state.scene_image_b64:
        image_bytes = b64decode(state.scene_image_b64, validate=True)
        logger.debug("response_gen: attaching image", image_bytes=len(image_bytes))
        messages.append(
            HumanMessage(
                content=[
                    {"type": "text", "text": user_message},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{state.scene_image_b64}"},
                    },
                ]
            )
        )
        return messages

    messages.append(HumanMessage(content=user_message))
    return messages


def _extract_response_text(reply: Any) -> str:
    """从 LangChain 返回对象中提取文本内容。"""
    content = getattr(reply, "content", "")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    return str(content).strip()


async def response_gen(state: AgentState) -> dict:
    """调用 LLM 生成回复文本。"""
    lang = state.language
    has_image = bool(state.scene_image_b64)

    if has_image:
        system_prompt = _load_prompt(f"multimodal_{lang}.txt")
    else:
        system_prompt = _load_prompt(f"base_{lang}.txt")

    context = _build_context_block(state)
    user_message = _build_user_message(state, context)

    logger.debug(
        "response_gen: generating reply",
        lang=lang,
        has_image=has_image,
        context_len=len(context),
    )

    model = get_chat_model(multimodal=has_image)
    messages = _build_messages(state, system_prompt, user_message)
    reply = await model.ainvoke(messages)
    response_text = _extract_response_text(reply)

    if not response_text:
        response_text = "我暂时没有生成有效回复。"

    logger.info("response_gen: reply generated", length=len(response_text))
    return {
        "response_text": response_text,
        "response_meta": {
            "model": getattr(model, "model_name", None),
            "multimodal": has_image,
        },
    }
