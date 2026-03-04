"""
summarizer.py - AI 摘要与品味过滤引擎（主流程编排）

子模块职责：
  - dedup.py   : URL/标题归一化、历史去重、跨源去重
  - filter.py  : 批量标题筛选、来源保底逻辑
  - scorer.py  : 单条内容 AI 评分与摘要
  - digest.py  : 生成「今日要点」整体总结
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from src.ai.dedup import (
    ai_dedup_across_candidates,
    ai_dedup_against_history,
    item_key,
)
from src.ai.digest import generate_digest_summary
from src.ai.feedback import load_recent_history_records, load_taste_examples
from src.ai.filter import (
    ai_pick_fill_candidates,
    batch_select_by_titles,
    ensure_source_candidates,
    enforce_source_minimums,
    normalize_source_minimums,
)
from src.ai.scorer import build_scoring_system_prompt, score_single_item

# Re-export generate_digest_summary so existing callers don't need to change imports
__all__ = ["summarize_items", "generate_digest_summary"]

logger = logging.getLogger(__name__)


def _safe_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def summarize_items(
    raw_items: list[dict],
    config: dict,
    min_score: Optional[int] = None,
    max_output: Optional[int] = None,
    focus: str = "",
) -> list[dict]:
    """
    两阶段处理：
      阶段 A : 历史去重（title/url）
      阶段 1 : AI 仅看标题+简介，一次调用批量筛选入围条目
      阶段 B : 跨源去重（精读前）
      阶段 2 : 仅对入围条目逐条调用 AI，生成完整评分+摘要

    Args:
        raw_items: 来自各 collector 的原始数据列表
        config:    AppConfig dict
        min_score: 低于此分数的条目被过滤（默认读 config）
        max_output: 最多返回条目数（默认读 config）
        focus:     本次筛选方向（来自 schedule.focus），传给 AI 做相关度评分

    Returns:
        过滤并排序后的列表，每项新增：
            - ai_score: int (1-10)
            - ai_summary: str
            - ai_reason: str
    """
    if not raw_items:
        return []

    ai_cfg = config.get("ai", {})
    rss_cfg = config.get("collectors", {}).get("rss", {})

    min_score_default = _safe_positive_int(ai_cfg.get("min_relevance_score", 5), 5)
    min_score = (
        _safe_positive_int(min_score, min_score_default) if min_score is not None else min_score_default
    )

    default_cap = 15
    raw_config_cap = ai_cfg.get("max_items_per_digest", default_cap)
    config_cap = _safe_positive_int(raw_config_cap, default_cap)
    if config_cap != raw_config_cap:
        logger.warning(f"ai.max_items_per_digest 非法({raw_config_cap!r})，回退默认值 {default_cap}")

    if max_output is None:
        requested_max = config_cap
    else:
        requested_max = _safe_positive_int(max_output, config_cap)
    effective_max_output = min(requested_max, config_cap)

    logger.info(
        f"  新闻筛选开始: raw_count={len(raw_items)} "
        f"requested_max={requested_max} config_cap={config_cap} "
        f"effective_cap={effective_max_output} min_score={min_score}"
    )

    rss_per_feed_limit = _safe_positive_int(rss_cfg.get("max_items_per_feed", 3), 3)
    model = os.environ.get("AI_MODEL") or ai_cfg.get("model", "openai/gpt-4o-mini")
    api_base = os.environ.get("AI_API_BASE") or ai_cfg.get("api_base") or None
    backend = os.environ.get("AI_BACKEND") or ai_cfg.get("backend", "litellm")
    api_key = os.environ.get("AI_API_KEY", "")

    if backend == "litellm" and not api_key:
        logger.error("AI_API_KEY 未配置，跳过 AI 摘要（backend=litellm 需要 API key）")
        return raw_items[:effective_max_output]

    call_kwargs: dict = {"model": model, "api_key": api_key, "max_tokens": ai_cfg.get("max_tokens", 512)}
    if api_base:
        call_kwargs["api_base"] = api_base

    taste_limit = ai_cfg.get("taste_examples_limit", 8)
    source_minimums = normalize_source_minimums(ai_cfg.get("min_items_per_source"))
    language = config.get("app", {}).get("language", "zh")
    max_workers = ai_cfg.get("max_workers", 5)

    taste_examples = load_taste_examples(config, limit=taste_limit)
    history_records = load_recent_history_records(config, days=7, limit=600)

    history_titles: list[str] = []
    seen_history_titles: set[str] = set()
    for rec in history_records:
        title = str(rec.get("title", "")).strip()
        if not title or title in seen_history_titles:
            continue
        seen_history_titles.add(title)
        history_titles.append(title)
        if len(history_titles) >= 120:
            break

    # ── 阶段 A：历史去重 ──────────────────────────────────────────────────────
    kept_indices = ai_dedup_against_history(
        raw_items, history_records, call_kwargs=call_kwargs, language=language, backend=backend
    )
    items_after_history = [raw_items[i] for i in kept_indices]
    logger.info(f"  after_history_dedup_count={len(items_after_history)}")
    if not items_after_history:
        logger.info(f"  新闻筛选完成: final_count=0 effective_cap={effective_max_output}")
        return []

    # ── 阶段 1：标题批量筛选 ──────────────────────────────────────────────────
    max_keep = min(effective_max_output * 2, len(items_after_history))
    selected_indices = batch_select_by_titles(
        items_after_history, focus, taste_examples, call_kwargs, language,
        max_keep, backend=backend, history_titles=history_titles,
    )
    selected_indices = ensure_source_candidates(
        items_after_history, selected_indices, source_minimums, max_keep
    )
    candidates = [items_after_history[i] for i in selected_indices]
    logger.info(f"  after_stage1_select_count={len(candidates)}")
    if not candidates:
        logger.info(f"  新闻筛选完成: final_count=0 effective_cap={effective_max_output}")
        return []

    # ── 阶段 B：跨源去重 ──────────────────────────────────────────────────────
    candidates = ai_dedup_across_candidates(
        candidates, focus=focus, call_kwargs=call_kwargs, language=language, backend=backend
    )
    logger.info(f"  after_cross_source_dedup_count={len(candidates)}")
    if not candidates:
        logger.info(f"  新闻筛选完成: final_count=0 effective_cap={effective_max_output}")
        return []

    # ── RSS 每 feed 封顶 ──────────────────────────────────────────────────────
    feed_counts: dict[str, int] = {}
    capped: list[dict] = []
    for item in candidates:
        if item.get("source") == "rss":
            feed_key = item.get("feed_title", item.get("url", "unknown"))
            feed_counts[feed_key] = feed_counts.get(feed_key, 0) + 1
            if feed_counts[feed_key] > rss_per_feed_limit:
                continue
        capped.append(item)
    if len(capped) < len(candidates):
        logger.info(f"  RSS per-feed 封顶：{len(candidates)} → {len(capped)} 条进入摘要")
    candidates = capped

    # ── 候选不足时从剩余池补全 ────────────────────────────────────────────────
    if len(candidates) < effective_max_output:
        need_fill = effective_max_output - len(candidates)
        existing_keys = {item_key(item) for item in candidates}
        remaining_pool = [item for item in items_after_history if item_key(item) not in existing_keys]
        fill_indices = ai_pick_fill_candidates(
            candidates, remaining_pool, need_fill, focus, call_kwargs, language, backend=backend
        )
        filled = 0
        for idx in fill_indices:
            if not (0 <= idx < len(remaining_pool)):
                continue
            item = remaining_pool[idx]
            key = item_key(item)
            if key in existing_keys:
                continue
            if item.get("source") == "rss":
                feed_key = item.get("feed_title", item.get("url", "unknown"))
                if feed_counts.get(feed_key, 0) >= rss_per_feed_limit:
                    continue
                feed_counts[feed_key] = feed_counts.get(feed_key, 0) + 1
            candidates.append(item)
            existing_keys.add(key)
            filled += 1
            if len(candidates) >= effective_max_output:
                break
        logger.info(f"  after_fill_candidates_count={len(candidates)} (filled={filled}, need={need_fill})")

    if not candidates:
        logger.info(f"  新闻筛选完成: final_count=0 effective_cap={effective_max_output}")
        return []

    # ── 精读前：为 YouTube 视频补充字幕 ──────────────────────────────────────
    yt_candidates = [item for item in candidates if item.get("source") == "youtube"]
    if yt_candidates:
        logger.info(f"  拉取 YouTube 字幕（{len(yt_candidates)} 个视频）...")
        try:
            from src.collectors.youtube_collector import _get_transcript
            for item in yt_candidates:
                video_id = item.get("video_id", "")
                if not video_id and "v=" in item.get("url", ""):
                    video_id = item["url"].split("v=")[-1].split("&")[0]
                if video_id:
                    item["transcript_snippet"] = _get_transcript(video_id)
        except Exception as e:
            logger.warning(f"YouTube 字幕补充失败: {e}")

    # ── 阶段 2：并行完整评分+摘要 ─────────────────────────────────────────────
    system_prompt = build_scoring_system_prompt(taste_examples, language, focus=focus)
    results: list[dict] = []
    low_score_pool: list[dict] = []
    total = len(candidates)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(score_single_item, item, system_prompt, backend, call_kwargs, min_score, i, total): item
            for i, item in enumerate(candidates)
        }
        for future in as_completed(futures):
            high, low = future.result()
            if high is not None:
                results.append(high)
            elif low is not None:
                low_score_pool.append(low)

    logger.info(f"  after_stage2_score_count={len(results)}")
    results.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

    # 按来源分桶，每源均匀分配
    source_buckets: dict[str, list] = {}
    for item in results:
        source_buckets.setdefault(item.get("source", "unknown"), []).append(item)

    per_source_min = effective_max_output // len(source_buckets) if source_buckets else effective_max_output
    selected: list[dict] = []
    leftover: list[dict] = []
    for items in source_buckets.values():
        selected.extend(items[:per_source_min])
        leftover.extend(items[per_source_min:])

    remaining = effective_max_output - len(selected)
    if remaining > 0 and leftover:
        leftover.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
        selected.extend(leftover[:remaining])

    selected = enforce_source_minimums(
        selected=selected,
        high_score_items=results,
        low_score_items=low_score_pool,
        source_minimums=source_minimums,
        max_output=effective_max_output,
    )
    final_items = selected[:effective_max_output]
    final_items.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

    logger.info(
        f"  新闻筛选完成: final_count={len(final_items)} "
        f"effective_cap={effective_max_output} requested_max={requested_max} config_cap={config_cap}"
    )
    return final_items
