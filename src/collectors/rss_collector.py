"""
rss_collector.py - RSS 订阅抓取器
====================================
v2: 两阶段抓取——每 feed 先多抓（max_items_per_feed_initial）条标题，
    由 AI 批量筛选后再按 max_items_per_feed 上限进摘要阶段。
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DailyRadarBot/1.0)"
    )
}


def _parse_entry_date(entry) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if t:
            try:
                import calendar
                ts = calendar.timegm(t)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass

    for field in ("published", "updated"):
        raw = getattr(entry, field, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass

    return None


def _extract_content(entry) -> str:
    content_list = getattr(entry, "content", [])
    if content_list:
        raw = content_list[0].get("value", "")
        if raw:
            import re
            text = re.sub(r"<[^>]+>", "", raw)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:2000]

    summary = getattr(entry, "summary", "")
    if summary:
        import re
        text = re.sub(r"<[^>]+>", "", summary)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:2000]

    return ""


def _fetch_feed(feed_url: str, days_back: int, max_items: int) -> list[dict]:
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        resp = requests.get(feed_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        logger.warning(f"RSS 源拉取失败 {feed_url}: {e}")
        return []

    feed_title = getattr(feed.feed, "title", feed_url)
    count = 0

    for entry in feed.entries:
        if count >= max_items:
            break

        pub_date = _parse_entry_date(entry)
        if pub_date and pub_date < cutoff:
            continue

        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()
        if not title or not url:
            continue

        content_snippet = _extract_content(entry)

        items.append({
            "title": title,
            "url": url,
            "description": content_snippet[:500],
            "content_snippet": content_snippet,
            "published_at": pub_date.isoformat() if pub_date else "",
            "feed_title": feed_title,
            "source": "rss",
        })
        count += 1

    logger.info(f"RSS: {feed_title} → {len(items)} 篇文章")
    return items


def collect_rss(config: dict, max_total: Optional[int] = None) -> list[dict]:
    """
    抓取所有配置的 RSS 源。

    每个 feed 先抓 max_items_per_feed_initial 条（默认 20）用于 AI 标题批量筛选；
    筛选后由 summarizer 按 max_items_per_feed（默认 3）做每 feed 上限。

    Args:
        config: AppConfig dict（来自 config_loader.load_config()）
    """
    rss_cfg = config.get("collectors", {}).get("rss", {})
    if not rss_cfg.get("enabled", True):
        return []

    days_back = rss_cfg.get("days_lookback", 2)
    # 初始多抓，供 AI 批量筛选
    max_per_feed_initial = rss_cfg.get("max_items_per_feed_initial", 20)
    feeds = rss_cfg.get("feeds", [])

    collected: list[dict] = []
    seen_urls: set[str] = set()

    for feed in feeds:
        feed_url = feed.get("url", "") if isinstance(feed, dict) else feed
        if not feed_url:
            continue
        items = _fetch_feed(feed_url, days_back, max_per_feed_initial)
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                collected.append(item)

    if max_total:
        collected = collected[:max_total]

    logger.info(f"RSS: 共收集 {len(collected)} 篇文章（每 feed 最多 {max_per_feed_initial} 条标题入池）")
    return collected
