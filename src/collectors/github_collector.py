"""
github_collector.py - GitHub Trending 爬取器
============================================
通过爬取 github.com/trending 获取真正的 Trending 仓库
（按近期 star 增长速度排序，而非存量 star 数）。
由 AI 按 focus 方向做相关度过滤，无需手动维护关键词。
"""

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_int(text: str) -> int:
    """从 '1,234' 或 '1.2k' 等字符串中提取整数。"""
    text = text.strip().replace(",", "").lower()
    try:
        if text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        return int(text)
    except (ValueError, AttributeError):
        return 0


def _scrape_trending(language: str = "", since: str = "daily", max_repos: int = 25) -> list[dict]:
    """
    爬取 github.com/trending（可选语言过滤）。

    Args:
        language: 编程语言，如 "python"、"typescript"，留空=所有语言
        since:    时间范围，"daily" / "weekly" / "monthly"
        max_repos: 最多返回条目数

    Returns:
        仓库信息列表
    """
    url = f"{TRENDING_URL}/{language.lower()}" if language else TRENDING_URL
    try:
        resp = requests.get(url, params={"since": since}, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"GitHub Trending 页面请求失败 language={language!r}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article.Box-row")
    if not articles:
        logger.warning(f"GitHub Trending 页面未找到仓库条目，页面结构可能已变化 (language={language!r})")
        return []

    repos: list[dict] = []
    for article in articles[:max_repos]:
        # ── 仓库名 & 链接 ──────────────────────────────────────
        h2 = article.find("h2")
        if not h2:
            continue
        link = h2.find("a", href=True)
        if not link:
            continue
        href = link["href"].strip("/")          # "owner/repo"
        full_name = re.sub(r"\s+", "", href)    # 去除空白
        repo_url = f"https://github.com/{full_name}"

        # ── 描述 ───────────────────────────────────────────────
        desc_tag = article.find("p")
        description = desc_tag.get_text(strip=True) if desc_tag else ""

        # ── 编程语言 ───────────────────────────────────────────
        lang_span = article.find("span", {"itemprop": "programmingLanguage"})
        lang = lang_span.get_text(strip=True) if lang_span else ""

        # ── 总 Star 数 ─────────────────────────────────────────
        star_link = article.find("a", href=lambda h: h and h.endswith("/stargazers"))
        total_stars = _parse_int(star_link.get_text()) if star_link else 0

        # ── 今日/本周新增 Star ─────────────────────────────────
        # class 名包含 "float-sm-right" 的 span
        today_span = article.find("span", class_=lambda c: c and "float-sm-right" in c)
        stars_gained = today_span.get_text(strip=True) if today_span else ""

        repos.append({
            "title": full_name,
            "url": repo_url,
            "description": description,
            "stars": total_stars,
            "stars_gained": stars_gained,   # e.g. "123 stars today"
            "language": lang,
            "topics": [],
            "source": "github",
        })

    return repos


def collect_github(config: dict, max_repos: Optional[int] = None) -> list[dict]:
    """
    爬取 GitHub Trending 页面。

    Args:
        config: AppConfig dict（来自 config_loader.load_config()）
    """
    gh_cfg = config.get("collectors", {}).get("github", {})
    if not gh_cfg.get("enabled", True):
        return []

    max_repos = max_repos or gh_cfg.get("max_repos", 25)
    since = gh_cfg.get("trending_since", "daily")
    languages = gh_cfg.get("trending_languages") or []   # 空列表=不限语言

    collected: list[dict] = []
    seen: set[str] = set()

    targets = languages if languages else [""]   # 空字符串=所有语言

    for lang in targets:
        if len(collected) >= max_repos:
            break
        repos = _scrape_trending(language=lang, since=since, max_repos=max_repos)
        for repo in repos:
            if len(collected) >= max_repos:
                break
            if repo["url"] not in seen:
                seen.add(repo["url"])
                collected.append(repo)

    logger.info(f"GitHub: 共收集 {len(collected)} 个仓库（Trending/{since}）")
    return collected
