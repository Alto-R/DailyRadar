"""
scorer.py - 单条内容 AI 评分 + 摘要
"""

from __future__ import annotations

import json
import logging
import re

from src.ai.cli_backend import _call_ai

logger = logging.getLogger(__name__)


def _make_item_text(item: dict) -> str:
    source = item.get("source", "unknown")
    lines = [f"**来源**: {source.upper()}", f"**标题**: {item.get('title', '')}"]

    if item.get("url"):
        lines.append(f"**链接**: {item['url']}")

    if source == "github":
        if item.get("stars"):
            lines.append(f"**Stars**: {item['stars']:,}")
        if item.get("stars_gained"):
            lines.append(f"**近期新增**: {item['stars_gained']}")
        if item.get("language"):
            lines.append(f"**语言**: {item['language']}")
        if item.get("description"):
            lines.append(f"**简介**: {item['description']}")
        if item.get("readme_snippet"):
            lines.append(f"**README 片段**:\n{item['readme_snippet'][:800]}")

    elif source == "youtube":
        if item.get("channel"):
            lines.append(f"**频道**: {item['channel']}")
        if item.get("view_count"):
            lines.append(f"**播放量**: {item['view_count']:,}")
        if item.get("description"):
            lines.append(f"**视频描述**: {item['description']}")
        if item.get("transcript_snippet"):
            lines.append(f"**字幕片段**:\n{item['transcript_snippet'][:800]}")

    elif source == "rss":
        if item.get("feed_title"):
            lines.append(f"**订阅源**: {item['feed_title']}")
        if item.get("content_snippet"):
            lines.append(f"**正文片段**:\n{item['content_snippet'][:800]}")

    return "\n".join(lines)


def build_scoring_system_prompt(taste_examples: list[dict], language: str = "zh", focus: str = "") -> str:
    lang_label = "中文" if language == "zh" else "English"
    base = f"""你是一个专业的内容策展助手，负责为用户筛选和摘要每日信息流。

用户的偏好语言是：{lang_label}

你的任务是对每一条内容进行：
1. **相关性评分**（1-10 分）：结合用户今日关注方向和历史喜好判断这条内容对用户的价值
2. **生成摘要**：用 2-4 句话提炼核心价值，说明"为什么值得关注"

评分标准：
- 9-10：极度相关，用户几乎肯定感兴趣
- 7-8：较相关，有一定参考价值
- 5-6：一般，勉强值得一看
- 1-4：不相关或质量低，不推荐
"""
    if focus:
        base += f"""
---
## 今日筛选方向

用户今天的关注重点是：**{focus}**

评分时请以此方向为首要参考：
- 与方向高度相关的内容优先给高分，即使历史上没有类似偏好
- 与方向完全无关的内容适当降分，即使内容本身质量不错
---
"""

    if taste_examples:
        base += "\n\n---\n## 用户历史高分内容（品味参考）\n\n"
        base += "以下是用户过去打高分（4-5/5）的内容，请参考这些来判断用户的偏好：\n\n"
        for i, ex in enumerate(taste_examples, 1):
            base += f"**示例 {i}**（用户评分 {ex['score']}/5）\n"
            base += f"- 标题: {ex['title']}\n"
            base += f"- 来源: {ex['source']}\n"
            if ex["summary"]:
                base += f"- 摘要: {ex['summary']}\n"
            if ex["notes"]:
                base += f"- 用户备注: {ex['notes']}\n"
            base += "\n"
        base += "---\n"

    return base


def score_single_item(
    item: dict,
    system_prompt: str,
    backend: str,
    call_kwargs: dict,
    min_score: int,
    idx: int,
    total: int,
) -> tuple[dict | None, dict | None]:
    """
    对单条内容调用 AI 打分+摘要。
    Returns: (high_score_item, low_score_item)，有分数的一侧非 None，失败时均为 None。
    """
    logger.info(f"  摘要进度: {idx+1}/{total} - {item.get('title', '')[:50]}")
    item_text = _make_item_text(item)
    user_message = f"""请对以下内容进行评估，并以 JSON 格式返回结果：

{item_text}

请严格按照以下 JSON 格式返回（不要包含其他文字）：
{{
  "score": <1到10的整数>,
  "summary": "<2-4句话的摘要，说明核心内容>",
  "reason": "<1-2句话说明为什么推荐或不推荐>"
}}
"""
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        raw_text = _call_ai(messages, backend, call_kwargs)
        json_match = re.search(r"\{[\s\S]*\}", raw_text)
        if not json_match:
            logger.warning(f"AI 返回格式异常: {raw_text[:100]}")
            return None, None
        parsed = json.loads(json_match.group())
        score = int(parsed.get("score", 0))
        enriched = dict(item)
        enriched["ai_score"] = score
        enriched["ai_summary"] = parsed.get("summary", "")
        enriched["ai_reason"] = parsed.get("reason", "")
        if score < min_score:
            logger.debug(f"  跳过低分内容 score={score}: {item.get('title', '')[:40]}")
            return None, enriched
        return enriched, None
    except json.JSONDecodeError as e:
        logger.warning(f"JSON 解析失败: {e}")
    except Exception as e:
        logger.error(f"AI 摘要失败: {e}")
    return None, None
