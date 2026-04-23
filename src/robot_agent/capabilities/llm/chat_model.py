"""
capabilities/llm/chat_model.py — LLM 聊天模型封装

通过 LangChain ChatOpenAI 统一封装 LLM 调用。
支持普通文本模型和多模态模型切换。

用法:
    model = get_chat_model(multimodal=False)
    reply = await model.ainvoke(messages)
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from src.robot_agent.settings import settings


@lru_cache(maxsize=2)
def get_chat_model(multimodal: bool = False) -> ChatOpenAI:
    """
    获取 ChatOpenAI 实例（带缓存，避免重复创建）。

    Args:
        multimodal: True 时使用多模态模型（支持图像输入）

    Returns:
        ChatOpenAI 实例
    """
    model_name = settings.llm.multimodal_model if multimodal else settings.llm.model

    return ChatOpenAI(
        api_key=settings.llm.api_key,
        base_url=settings.llm.api_url,
        model=model_name,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
        timeout=settings.llm.timeout,
    )
