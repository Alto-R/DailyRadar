"""
filter.py - 批量标题筛选 + 来源保底逻辑
"""

from __future__ import annotations

import logging
from collections import Counter

from src.ai.cli_backend import _call_ai
from src.ai.dedup import item_key, parse_json_dict, short_item_line

logger = logging.getLogger(__name__)


def _safe_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def batch_select_by_titles(
    items: list[dict],
    focus: str,
    taste_examples: list[dict],
    call_kwargs: dict,
    language: str,
    max_keep: int,
    backend: str = "litellm",
    history_titles: list[str] | None = None,
) -> list[int]:
    """
    第一阶段：仅凭标题+简介，一次 API 调用批量筛选值得深读的条目。

    Returns:
        值得保留的条目下标列表（0-based）。失败时回退为全量下标。
    """
    lang_label = "中文" if language == "zh" else "English"

    items_text = ""
    for i, item in enumerate(items):
        source = item.get("source", "unknown").upper()
        title = item.get("title", "")
        desc = (item.get("description") or item.get("content_snippet") or "")[:80]
        items_text += f"[{i}] [{source}] {title}"
        if desc:
            items_text += f"  —  {desc}"
        items_text += "\n"

    focus_line = f"用户今日关注方向：**{focus}**\n\n" if focus else ""

    taste_hint = ""
    if taste_examples:
        taste_hint = "用户历史偏好（高分内容样例）：\n"
        for ex in taste_examples[:3]:
            taste_hint += f"- {ex['title']}\n"
        taste_hint += "\n"

    history_hint = ""
    if history_titles:
        capped = history_titles[:200]
        history_hint = "以下是过去7天已推送过的内容标题，请避免推荐语义上相似的内容：\n"
        for t in capped:
            history_hint += f"- {t}\n"
        history_hint += "\n"
        logger.info(f"  历史去重：加载 {len(capped)} 条历史标题到 Stage 1 提示词")

    user_message = (
        f"{focus_line}{taste_hint}{history_hint}"
        f"以下是 {len(items)} 条待筛选内容（格式：[序号] [来源] 标题  —  简介）：\n\n"
        f"{items_text}\n"
        f"请从中选出最多 {max_keep} 条最值得深度阅读的内容。\n\n"
        f"严格按照以下 JSON 格式返回（不要包含其他文字）：\n"
        '{"selected": [0, 3, 7]}\n\n'
        f"selected 数组填入值得保留的条目序号（0-based 整数）。"
    )

    try:
        messages = [
            {"role": "system", "content": f"你是内容筛选助手，请用{lang_label}思考，只输出 JSON。"},
            {"role": "user", "content": user_message},
        ]
        raw_text = _call_ai(messages, backend, {**call_kwargs, "max_tokens": 256})
        parsed = parse_json_dict(raw_text)
        if parsed and isinstance(parsed.get("selected"), list):
            valid = [i for i in parsed["selected"] if isinstance(i, int) and 0 <= i < len(items)]
            logger.info(f"  第一阶段筛选：{len(items)} → {len(valid)} 条入围")
            return valid[:max_keep]
    except Exception as e:
        logger.warning(f"标题批量筛选失败，回退到全量处理: {e}")

    return list(range(len(items)))


def ai_pick_fill_candidates(
    current_candidates: list[dict],
    remaining_pool: list[dict],
    need_count: int,
    focus: str,
    call_kwargs: dict,
    language: str,
    backend: str = "litellm",
) -> list[int]:
    """去重后候选不足时，从剩余池中补选条目。允许返回空列表。"""
    if need_count <= 0 or not remaining_pool:
        return []

    lang_label = "中文" if language == "zh" else "English"
    current_preview = "\n".join(
        f"- {str(item.get('title', '')).strip()}"
        for item in current_candidates[:30]
        if str(item.get("title", "")).strip()
    )
    pool = remaining_pool[:120]
    pool_text = "\n".join(short_item_line(i, item) for i, item in enumerate(pool))
    focus_line = f"用户关注方向：{focus}\n\n" if focus else ""

    user_message = (
        f"{focus_line}当前已入围 {len(current_candidates)} 条内容，仍缺 {need_count} 条。\n"
        f"请在【剩余候选池】中挑选最多 {need_count} 条值得补全的内容；"
        f"如果没有明显值得补全的内容，可以返回空数组。\n\n"
        f"当前已入围标题（用于避免重复）：\n{current_preview or '(none)'}\n\n"
        f"剩余候选池（{len(pool)} 条）：\n{pool_text}\n\n"
        f"请仅输出 JSON：\n"
        '{"supplement": [1, 5, 8], "reason": "..."}\n'
        f"其中 supplement 为剩余候选池的序号（0-based）。"
    )

    try:
        messages = [
            {"role": "system", "content": f"你是内容补全助手，请用{lang_label}思考，只输出 JSON。"},
            {"role": "user", "content": user_message},
        ]
        raw_text = _call_ai(messages, backend, {**call_kwargs, "max_tokens": 500})
        parsed = parse_json_dict(raw_text)
        if parsed and isinstance(parsed.get("supplement"), list):
            selected: list[int] = []
            seen: set[int] = set()
            for idx in parsed.get("supplement", []):
                if not isinstance(idx, int) or not (0 <= idx < len(pool)):
                    continue
                if idx in seen:
                    continue
                seen.add(idx)
                selected.append(idx)
                if len(selected) >= need_count:
                    break
            logger.info(f"  补全候选（AI）：pool={len(pool)} need={need_count} selected={len(selected)}")
            return selected
    except Exception as e:
        logger.warning(f"补全候选 AI 失败，跳过补全: {e}")

    logger.info(f"  补全候选（fallback）：pool={len(pool)} need={need_count} selected=0")
    return []


def normalize_source_minimums(raw_cfg) -> dict[str, int]:
    """解析来源保底配置。默认：github >= 5，youtube >= 2。"""
    minimums: dict[str, int] = {"github": 5, "youtube": 2}
    if not isinstance(raw_cfg, dict):
        return minimums

    for source, value in raw_cfg.items():
        src = str(source).strip().lower()
        if not src:
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            minimums.pop(src, None)
        else:
            minimums[src] = n
    return minimums


def ensure_source_candidates(
    raw_items: list[dict],
    selected_indices: list[int],
    source_minimums: dict[str, int],
    max_keep: int,
) -> list[int]:
    """在阶段一候选池中补齐来源保底，避免某来源在标题筛选阶段被完全筛掉。"""
    if not source_minimums:
        return selected_indices[:max_keep]

    selected: list[int] = []
    seen: set[int] = set()
    for idx in selected_indices:
        if isinstance(idx, int) and 0 <= idx < len(raw_items) and idx not in seen:
            selected.append(idx)
            seen.add(idx)

    added_counts: dict[str, int] = {}
    for source, minimum in source_minimums.items():
        current = sum(1 for idx in selected if raw_items[idx].get("source", "") == source)
        need = max(0, minimum - current)
        if need == 0:
            continue
        for idx, item in enumerate(raw_items):
            if need == 0:
                break
            if idx in seen or item.get("source", "") != source:
                continue
            selected.append(idx)
            seen.add(idx)
            need -= 1
            added_counts[source] = added_counts.get(source, 0) + 1

    if added_counts:
        logger.info(f"  阶段一补齐来源候选: {added_counts}")

    if len(selected) <= max_keep:
        return selected

    protected: list[int] = []
    protected_counts: dict[str, int] = {}
    for idx in selected:
        src = raw_items[idx].get("source", "")
        limit = source_minimums.get(src, 0)
        if limit > 0 and protected_counts.get(src, 0) < limit:
            protected.append(idx)
            protected_counts[src] = protected_counts.get(src, 0) + 1

    if len(protected) >= max_keep:
        trimmed = protected[:max_keep]
    else:
        protected_set = set(protected)
        trimmed = list(protected)
        for idx in selected:
            if len(trimmed) >= max_keep:
                break
            if idx not in protected_set:
                trimmed.append(idx)

    logger.info(f"  阶段一候选裁剪：{len(selected)} → {len(trimmed)} 条")
    return trimmed


def enforce_source_minimums(
    selected: list[dict],
    high_score_items: list[dict],
    low_score_items: list[dict],
    source_minimums: dict[str, int],
    max_output: int,
) -> list[dict]:
    """在最终输出阶段执行来源保底（先用高分条目补，不足时用低分兜底）。"""
    if not source_minimums:
        return selected[:max_output]

    result = list(selected)
    used_keys = {item_key(item) for item in result}

    pools: dict[str, list[dict]] = {}
    for item in high_score_items + low_score_items:
        src = item.get("source", "")
        if src not in source_minimums:
            continue
        pools.setdefault(src, []).append(item)

    for items in pools.values():
        items.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

    supplemented: dict[str, int] = {}

    for source, minimum in source_minimums.items():
        if minimum <= 0:
            continue
        pool = pools.get(source, [])
        pool_idx = 0

        while sum(1 for item in result if item.get("source", "") == source) < minimum:
            if pool_idx >= len(pool):
                break
            candidate = pool[pool_idx]
            pool_idx += 1
            key = item_key(candidate)
            if key in used_keys:
                continue

            if len(result) < max_output:
                result.append(candidate)
                used_keys.add(key)
                supplemented[source] = supplemented.get(source, 0) + 1
                continue

            counts = Counter(item.get("source", "") for item in result)
            evict_idx = None
            evict_score = None
            for idx, existing in enumerate(result):
                existing_src = existing.get("source", "")
                if counts[existing_src] <= source_minimums.get(existing_src, 0):
                    continue
                score = existing.get("ai_score", 0)
                if evict_idx is None or score < evict_score:
                    evict_idx = idx
                    evict_score = score

            if evict_idx is None:
                break

            removed = result.pop(evict_idx)
            used_keys.discard(item_key(removed))
            result.append(candidate)
            used_keys.add(key)
            supplemented[source] = supplemented.get(source, 0) + 1

    if supplemented:
        logger.info(f"  最终来源保底补齐: {supplemented}")

    result.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
    return result[:max_output]
