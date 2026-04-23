"""
nodes/response_gen.py — 回复生成节点

职责：
- 根据 state 中所有上下文（记忆、知识库、Tool 结果、图像）构建完整 prompt
- 调用 LLM 生成回复文本
- 写入 state.response_text

判断使用文本模型还是多模态模型：有图像时用多模态。
"""

from __future__ import annotations

from pathlib import Path

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)

# prompt 文件路径
PROMPT_DIR = Path(__file__).resolve().parents[5] / "configs" / "prompts"


def _load_prompt(filename: str) -> str:
    """从文件加载 prompt 模板"""
    path = PROMPT_DIR / filename
    if not path.exists():
        logger.warning("response_gen: prompt file not found", path=str(path))
        return ""
    return path.read_text(encoding="utf-8")


def _build_context_block(state: AgentState) -> str:
    """将记忆、知识库、Tool 结果拼成上下文文本块"""
    parts = []

    if state.recalled_memories:
        parts.append("【相关记忆】")
        for m in state.recalled_memories:
            parts.append(f"- {m.get('memory_text', '')}")

    if state.kb_chunks:
        parts.append("【知识库参考】")
        for chunk in state.kb_chunks:
            parts.append(f"- {chunk.get('content', '')}")

    if state.tool_results:
        parts.append("【工具调用结果】")
        for r in state.tool_results:
            if r.get("ok"):
                parts.append(f"- {r['tool']}: {r.get('data', '')}")

    return "\n".join(parts)


async def response_gen(state: AgentState) -> dict:
    """
    回复生成节点（异步）。

    TODO: 接入真实 LLM 调用（langchain ChatModel）
    """
    lang = state.language
    has_image = bool(state.scene_image_b64)

    # ── 选择 prompt 文件 ──────────────────────────────────────
    if has_image:
        system_prompt = _load_prompt(f"multimodal_{lang}.txt")
    else:
        system_prompt = _load_prompt(f"base_{lang}.txt")

    # ── 构建用户消息 ──────────────────────────────────────────
    context = _build_context_block(state)
    user_message = f"情感标签：{state.emotion or 'neutral'}\n对方说：{state.normalized_text}"
    if context:
        user_message = f"{context}\n\n{user_message}"

    logger.debug(
        "response_gen: generating reply",
        lang=lang,
        has_image=has_image,
        context_len=len(context),
    )

    # ── TODO: 调用 LLM ────────────────────────────────────────
    # from src.robot_agent.capabilities.llm.chat_model import get_chat_model
    # model = get_chat_model(multimodal=has_image)
    # messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    # if has_image:
    #     messages[-1] = HumanMessage(content=[
    #         {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{state.scene_image_b64}"}},
    #         {"type": "text", "text": user_message},
    #     ])
    # reply = await model.ainvoke(messages)
    # response_text = reply.content.strip()

    response_text = "[TODO: LLM 回复尚未接入]"

    logger.info("response_gen: reply generated", length=len(response_text))
    return {"response_text": response_text}
