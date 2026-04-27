"""
logging.py — 结构化日志初始化

使用 structlog 提供带上下文绑定的结构化日志。
在 app.py 启动时调用 setup_logging() 完成全局配置。

用法:
    from src.robot_agent.bootstrap.logging import get_logger
    logger = get_logger(__name__)
    logger.info("event", key="value")
"""

import logging
import sys

import structlog

from src.robot_agent.settings import settings


def setup_logging() -> None:
    """配置 structlog 全局处理管道，需在应用启动时调用一次"""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # 配置标准库日志
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # structlog 处理器链
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.set_exc_info,
    ]

    if settings.env == "development":
        # 开发环境：彩色控制台输出
        renderer = structlog.dev.ConsoleRenderer()
    else:
        # 生产环境：JSON 输出（方便日志收集）
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """获取带名称绑定的 logger 实例"""
    return structlog.get_logger(name)
