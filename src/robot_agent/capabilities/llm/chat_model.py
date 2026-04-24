"""
capabilities/llm/chat_model.py - LLM 聊天模型封装

统一创建 LangChain `ChatOpenAI` 实例，并根据场景选择文本模型
或多模态模型。这个模块不负责拼装 prompt，只负责模型初始化、
配置校验和实例缓存。

用法:
    from src.robot_agent.capabilities.llm.chat_model import get_chat_model

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
    获取 ChatOpenAI 实例，并按是否多模态进行缓存。

    Args:
        multimodal: True 时使用多模态模型，False 时使用纯文本模型。

    Returns:
        ChatOpenAI 实例。
    """
    model_name = settings.llm.multimodal_model if multimodal else settings.llm.model

    if not settings.llm.api_key:
        raise RuntimeError("LLM 配置缺失: 请设置 LLM_API_KEY")
    if not model_name:
        raise RuntimeError("LLM 配置缺失: 请设置 LLM_MODEL 或 LLM_MULTIMODAL_MODEL")

    return ChatOpenAI(
        api_key=settings.llm.api_key,
        base_url=settings.llm.api_url,
        model=model_name,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
        timeout=settings.llm.timeout,
    )
