"""
nodes/response_gen.py - 响应生成节点

生成最终的助手回复。如果 LLM 路径失败，此节点将回退到
简短的、感知情绪的固定回复，以便机器人仍能发声。
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

PROMPT_DIR = Path(__file__).resolve().parents[4] / "configs" / "prompts"

FALLBACK_RESPONSES: dict[str, dict[str, str]] = {
    "cn": {
        "happy": "听起来很不错。",
        "sad": "我在这儿，你可以继续说。",
        "angry": "先别着急，我们慢慢说。",
        "neutral": "收到，你继续说。",
        "fearful": "别怕，我在这儿。",
        "disgusted": "明白了，这确实让人不舒服。",
        "surprised": "哇，这还真有点意外。",
    },
    "en": {
        "happy": "That sounds nice.",
        "sad": "I'm here. You can keep talking.",
        "angry": "Let's slow down and talk it through.",
        "neutral": "Got it. Please go on.",
        "fearful": "It's okay. I'm here with you.",
        "disgusted": "I understand. That does sound unpleasant.",
        "surprised": "Wow, that is surprising.",
    },
}

GENERIC_FALLBACK: dict[str, str] = {
    "cn": "抱歉，我刚刚没连上大模型，但我还在。你可以再说一遍。",
    "en": "Sorry, I could not reach the model just now, but I'm still here. Please say it again.",
}


def _load_prompt(filename: str) -> str:
    """加载 prompt 文件；如果文件缺失则返回空字符串。"""
    path = PROMPT_DIR / filename
    if not path.exists():
        logger.warning("response_gen: prompt file not found", path=str(path))
        return ""
    return path.read_text(encoding="utf-8")


def _build_context_block(state: AgentState) -> str:
    """为 LLM 构建最近的上下文。"""
    parts: list[str] = []

    if state.recent_messages:
        parts.append("Recent messages:")
        for msg in state.recent_messages[-6:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                parts.append(f"- {role}: {content}")

    if state.recalled_memories:
        parts.append("Recalled memories:")
        for memory in state.recalled_memories:
            text = memory.get("memory_text", "")
            if text:
                parts.append(f"- {text}")

    if state.kb_chunks:
        parts.append("Knowledge:")
        for chunk in state.kb_chunks:
            text = chunk.get("content", "")
            if text:
                parts.append(f"- {text}")

    if state.tool_results:
        parts.append("Tool results:")
        for result in state.tool_results:
            tool = result.get("tool", "tool")
            if result.get("ok"):
                parts.append(f"- {tool}: {result.get('data', '')}")
            else:
                parts.append(f"- {tool} failed: {result.get('error', '')}")

    return "\n".join(parts)


def _build_user_message(state: AgentState, context: str) -> str:
    """构建传递给 model 的 user 消息。"""
    parts: list[str] = []

    if context:
        parts.append(context)

    if state.scene_summary:
        parts.append(f"Scene summary: {state.scene_summary}")

    parts.append(f"Emotion: {state.emotion or 'neutral'}")
    parts.append(f"User input: {state.normalized_text or state.input_text}")
    return "\n\n".join(parts)


def _build_messages(state: AgentState, system_prompt: str, user_message: str) -> list[Any]:
    """为文本或多模态生成构建 LangChain 消息。"""
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
    """从 LangChain reply 对象中提取纯文本。"""
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


def _emotion_fallback_text(language: str, emotion: str | None) -> str:
    """根据情绪返回简短的备用 fallback 语句。"""
    lang = language if language in FALLBACK_RESPONSES else "cn"
    emotion_key = emotion or "neutral"
    lang_map = FALLBACK_RESPONSES[lang]
    return lang_map.get(emotion_key, lang_map["neutral"])


def _llm_failure_fallback(state: AgentState) -> str:
    """当模型生成失败时，返回最安全的 fallback 响应。"""
    base = _emotion_fallback_text(state.language, state.emotion)
    generic = GENERIC_FALLBACK.get(state.language, GENERIC_FALLBACK["cn"])
    return f"{base} {generic}"


async def response_gen(state: AgentState) -> dict:
    """生成最终的一般响应文本。"""
    if state.response_text and state.response_text not in {"__SKIP__", "__STOP__", "__EXIT__"}:
        logger.info("response_gen: using prebuilt response", length=len(state.response_text))
        return {
            "response_text": state.response_text,
            "response_meta": {
                "source": "tool",
                "multimodal": bool(state.scene_image_b64),
            },
        }

    lang = state.language
    has_image = bool(state.scene_image_b64)
    system_prompt = _load_prompt(f"multimodal_{lang}.txt" if has_image else f"base_{lang}.txt")

    context = _build_context_block(state)
    user_message = _build_user_message(state, context)

    logger.debug(
        "response_gen: generating reply",
        lang=lang,
        has_image=has_image,
        context_len=len(context),
    )

    try:
        model = get_chat_model(multimodal=has_image)
        messages = _build_messages(state, system_prompt, user_message)
        reply = await model.ainvoke(messages)
        response_text = _extract_response_text(reply)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "response_gen: model generation failed, using fallback",
            error=str(exc),
            has_image=has_image,
            emotion=state.emotion or "neutral",
        )
        fallback_text = _llm_failure_fallback(state)
        return {
            "response_text": fallback_text,
            "response_meta": {
                "source": "fallback",
                "reason": "llm_error",
                "multimodal": has_image,
            },
        }

    if not response_text:
        logger.warning(
            "response_gen: empty model response, using fallback",
            has_image=has_image,
            emotion=state.emotion or "neutral",
        )
        response_text = _llm_failure_fallback(state)
        source = "fallback"
    else:
        source = "llm"

    logger.info("response_gen: reply generated", length=len(response_text), source=source)
    return {
        "response_text": response_text,
        "response_meta": {
            "source": source,
            "model": getattr(model, "model_name", None),
            "multimodal": has_image,
        },
    }
