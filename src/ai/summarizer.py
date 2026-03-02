"""
summarizer.py - AI 摘要与品味过滤引擎
==========================================
改编自 obsidian-daily-digest/summarizer.py
主要改动：
  - 用传入的 config dict 替换 import config 模块
  - 使用 LiteLLM 替代 Anthropic SDK，支持任意 OpenAI 兼容接口
  - API 配置从 AI_API_KEY / AI_MODEL / AI_API_BASE 环境变量读取
"""

import json
import logging
import os
import re
from typing import Optional

import litellm

from src.ai.feedback import load_taste_examples

# 静默 LiteLLM 冗余日志
litellm.suppress_debug_info = True

logger = logging.getLogger(__name__)


def _build_system_prompt(taste_examples: list[dict], language: str = "zh") -> str:
    lang_label = "中文" if language == "zh" else "English"
    base = f"""你是一个专业的内容策展助手，负责为用户筛选和摘要每日信息流。

用户的偏好语言是：{lang_label}

你的任务是对每一条内容进行：
1. **相关性评分**（1-10 分）：结合用户历史喜好判断这条内容对用户的价值
2. **生成摘要**：用 2-4 句话提炼核心价值，说明"为什么值得关注"

评分标准：
- 9-10：极度相关，用户几乎肯定感兴趣
- 7-8：较相关，有一定参考价值
- 5-6：一般，勉强值得一看
- 1-4：不相关或质量低，不推荐
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


def _make_item_text(item: dict) -> str:
    source = item.get("source", "unknown")
    lines = [f"**来源**: {source.upper()}", f"**标题**: {item.get('title', '')}"]

    if item.get("url"):
        lines.append(f"**链接**: {item['url']}")

    if source == "github":
        if item.get("stars"):
            lines.append(f"**Stars**: {item['stars']:,}")
        if item.get("language"):
            lines.append(f"**语言**: {item['language']}")
        if item.get("description"):
            lines.append(f"**简介**: {item['description']}")
        if item.get("readme_snippet"):
            lines.append(f"**README 片段**:\n{item['readme_snippet'][:800]}")

    elif source == "youtube":
        if item.get("channel"):
            lines.append(f"**频道**: {item['channel']}")
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


def summarize_items(
    raw_items: list[dict],
    config: dict,
    min_score: Optional[int] = None,
    max_output: Optional[int] = None,
) -> list[dict]:
    """
    对原始采集内容批量打分+摘要。

    Args:
        raw_items: 来自各 collector 的原始数据列表
        config:    AppConfig dict
        min_score: 低于此分数的条目被过滤（默认读 config）
        max_output: 最多返回条目数（默认读 config）

    Returns:
        过滤并排序后的列表，每项新增：
            - ai_score: int (1-10)
            - ai_summary: str
            - ai_reason: str
    """
    if not raw_items:
        return []

    ai_cfg = config.get("ai", {})
    min_score = min_score or ai_cfg.get("min_relevance_score", 5)
    max_output = max_output or ai_cfg.get("max_items_per_digest", 15)
    # 优先 env var，其次 config.yaml
    model     = os.environ.get("AI_MODEL")     or ai_cfg.get("model", "openai/gpt-4o-mini")
    api_base  = os.environ.get("AI_API_BASE")  or ai_cfg.get("api_base") or None
    max_tokens = ai_cfg.get("max_tokens", 512)
    taste_limit = ai_cfg.get("taste_examples_limit", 8)
    language = config.get("app", {}).get("language", "zh")

    api_key = os.environ.get("AI_API_KEY", "")
    if not api_key:
        logger.error("AI_API_KEY 未配置，跳过 AI 摘要")
        return raw_items[:max_output]

    # 通过 LiteLLM 统一调用，兼容 OpenAI / Claude / Gemini / 本地模型等
    call_kwargs: dict = dict(
        model=model,
        api_key=api_key,
        max_tokens=max_tokens,
    )
    if api_base:
        call_kwargs["api_base"] = api_base

    taste_examples = load_taste_examples(config, limit=taste_limit)
    system_prompt = _build_system_prompt(taste_examples, language)

    results: list[dict] = []

    for i, item in enumerate(raw_items):
        logger.info(f"  摘要进度: {i+1}/{len(raw_items)} - {item.get('title', '')[:50]}")
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
            response = litellm.completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                **call_kwargs,
            )
            raw_text = response.choices[0].message.content.strip()

            json_match = re.search(r"\{[\s\S]*\}", raw_text)
            if not json_match:
                logger.warning(f"AI 返回格式异常: {raw_text[:100]}")
                continue

            parsed = json.loads(json_match.group())
            score = int(parsed.get("score", 0))
            summary = parsed.get("summary", "")
            reason = parsed.get("reason", "")

            if score < min_score:
                logger.debug(f"  跳过低分内容 score={score}: {item.get('title', '')[:40]}")
                continue

            enriched = dict(item)
            enriched["ai_score"] = score
            enriched["ai_summary"] = summary
            enriched["ai_reason"] = reason
            results.append(enriched)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")
        except Exception as e:
            logger.error(f"AI 摘要失败: {e}")

    results.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

    # 按来源分桶（已按分数排序）
    source_buckets: dict[str, list] = {}
    for item in results:
        src = item.get("source", "unknown")
        if src not in source_buckets:
            source_buckets[src] = []
        source_buckets[src].append(item)

    # 每个来源保底条数
    per_source_min = max_output // len(source_buckets) if source_buckets else max_output

    selected: list[dict] = []
    leftover: list[dict] = []
    for items in source_buckets.values():
        selected.extend(items[:per_source_min])
        leftover.extend(items[per_source_min:])

    # 剩余名额按分数补充
    remaining = max_output - len(selected)
    if remaining > 0 and leftover:
        leftover.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
        selected.extend(leftover[:remaining])

    selected.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
    return selected
