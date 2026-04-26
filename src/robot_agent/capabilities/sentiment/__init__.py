"""Sentiment analysis capability exports."""

from src.robot_agent.capabilities.sentiment.roberta_analyzer import (
    RobertaSentimentAnalyzer,
    get_sentiment_analyzer,
)

__all__ = [
    "RobertaSentimentAnalyzer",
    "get_sentiment_analyzer",
]
