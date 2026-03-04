"""
digest.py - 生成「今日要点」整体摘要
"""

from __future__ import annotations

import logging
import os

from src.ai.cli_backend import _call_ai

logger = logging.getLogger(__name__)


def generate_digest_summary(
    news_items: list[dict],
    config: dict,
    focus: str = "",
) -> str:
    """
    对已筛选的 news_items 生成「今日要点」整体总结。
    单次 AI 调用，token 消耗极小。

    Returns:
        总结文本（纯文本，含换行），失败时返回空字符串。
    """
    if not news_items:
        return ""

    ai_cfg = config.get("ai", {})
    model    = os.environ.get("AI_MODEL")    or ai_cfg.get("model", "openai/gpt-4o-mini")
    api_base = os.environ.get("AI_API_BASE") or ai_cfg.get("api_base") or None
    backend  = os.environ.get("AI_BACKEND") or ai_cfg.get("backend", "litellm")
    api_key  = os.environ.get("AI_API_KEY", "")
    language = config.get("app", {}).get("language", "zh")

    if backend == "litellm" and not api_key:
        return ""

    call_kwargs: dict = dict(model=model, api_key=api_key, max_tokens=600)
    if api_base:
        call_kwargs["api_base"] = api_base

    lang_label = "中文" if language == "zh" else "English"
    focus_line = f"本次关注方向：{focus}\n\n" if focus else ""

    items_text = ""
    for i, item in enumerate(news_items, 1):
        source  = item.get("source", "").upper()
        title   = item.get("title", "")
        summary = item.get("ai_summary", "")
        score   = item.get("ai_score", "?")
        items_text += f"{i}. [{source}][{score}/10] {title}\n"
        if summary:
            items_text += f"   {summary}\n"

    user_message = (
        f"{focus_line}以下是今日精选的 {len(news_items)} 条内容：\n\n"
        f"{items_text}\n"
        f"请用{lang_label}撰写「今日要点」总结：\n"
        "- 提炼 3-5 条最值得关注的主题或趋势\n"
        "- 每条要点 1-2 句，言简意赅\n"
        "- 覆盖不同领域（AI/科技/金融/政治等）\n"
        "- 直接输出要点列表，每条以「• 」开头，不需要标题或其他说明文字"
    )

    try:
        messages = [
            {"role": "system", "content": f"你是专业的信息分析师，擅长跨领域提炼要点，请用{lang_label}输出。"},
            {"role": "user", "content": user_message},
        ]
        return _call_ai(messages, backend, call_kwargs)
    except Exception as e:
        logger.warning(f"生成今日要点失败: {e}")
        return ""
