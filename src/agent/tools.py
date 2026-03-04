"""
Tool registry for SignalNest local agent.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.ai.summarizer import generate_digest_summary, summarize_items
from src.collectors.github_collector import collect_github
from src.collectors.rss_collector import collect_rss
from src.collectors.youtube_collector import collect_youtube
from src.notifications.dispatcher import dispatch
from src.personal.ai_reader import read_active_projects, read_today_schedule

ToolHandler = Callable[[dict[str, Any], "ToolRuntime"], dict[str, Any]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    side_effect: bool
    handler: ToolHandler


@dataclass
class ToolRuntime:
    config: dict
    state: dict[str, Any]
    dry_run: bool
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()

    def tz(self) -> ZoneInfo:
        tz_name = self.config.get("app", {}).get("timezone", "Asia/Shanghai")
        return ZoneInfo(tz_name)


def _item_key(item: dict[str, Any]) -> str:
    source = str(item.get("source", "")).strip().lower()
    url = str(item.get("url", "")).strip().lower()
    title = str(item.get("title", "")).strip().lower()
    if url:
        return f"{source}:{url}"
    return f"{source}:{title}"


def _merge_items(existing: list[dict], added: list[dict]) -> list[dict]:
    merged = list(existing)
    seen = {_item_key(i) for i in merged}
    for item in added:
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _compact_news_preview(items: list[dict], limit: int = 5) -> list[str]:
    return [str(item.get("title", "")) for item in items[:limit]]


def _tool_collect_github(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    cfg = copy.deepcopy(rt.config)
    gh_cfg = cfg.setdefault("collectors", {}).setdefault("github", {})
    if "since" in args:
        gh_cfg["trending_since"] = args["since"]
    if "languages" in args:
        gh_cfg["trending_languages"] = args["languages"]

    items = collect_github(cfg, max_repos=args.get("max_repos"))
    rt.state["raw_items"] = _merge_items(rt.state.get("raw_items", []), items)

    return {
        "source": "github",
        "fetched_count": len(items),
        "raw_items_total": len(rt.state["raw_items"]),
        "sample_titles": _compact_news_preview(items),
    }


def _tool_collect_rss(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    cfg = copy.deepcopy(rt.config)
    rss_cfg = cfg.setdefault("collectors", {}).setdefault("rss", {})
    if "days_back" in args:
        rss_cfg["days_lookback"] = args["days_back"]

    items = collect_rss(cfg, max_total=args.get("max_total"))
    rt.state["raw_items"] = _merge_items(rt.state.get("raw_items", []), items)

    return {
        "source": "rss",
        "fetched_count": len(items),
        "raw_items_total": len(rt.state["raw_items"]),
        "sample_titles": _compact_news_preview(items),
    }


def _tool_collect_youtube(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    items = collect_youtube(
        rt.config,
        focus=args.get("focus", ""),
        max_total=args.get("max_total"),
    )
    rt.state["raw_items"] = _merge_items(rt.state.get("raw_items", []), items)

    return {
        "source": "youtube",
        "fetched_count": len(items),
        "raw_items_total": len(rt.state["raw_items"]),
        "sample_titles": _compact_news_preview(items),
    }



def _tool_summarize_news(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    raw_items = rt.state.get("raw_items", [])
    if not raw_items:
        raise ValueError("state.raw_items is empty; run a collect tool first")

    ai_cfg = rt.config.get("ai", {})
    raw_cap = ai_cfg.get("max_items_per_digest", 15)
    try:
        config_cap = int(raw_cap)
        if config_cap <= 0:
            raise ValueError("non-positive cap")
    except Exception:
        config_cap = 15
        logger.warning("summarize_news: invalid ai.max_items_per_digest=%r, fallback=15", raw_cap)

    focus = args.get("focus", "")

    news_items = summarize_items(
        raw_items,
        rt.config,
        min_score=args.get("min_score"),
        max_output=None,
        focus=focus,
    )
    if len(news_items) > config_cap:
        logger.warning(
            "summarize_news: defensive clamp triggered %s -> %s by config cap",
            len(news_items),
            config_cap,
        )
        news_items = news_items[:config_cap]

    digest_summary = generate_digest_summary(news_items, rt.config, focus=focus)

    rt.state["news_items"] = news_items
    rt.state["digest_summary"] = digest_summary

    return {
        "news_count": len(news_items),
        "top_titles": _compact_news_preview(news_items),
        "digest_summary": digest_summary,
    }


def _tool_read_today_schedule(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    personal_dir = Path(rt.config.get("_personal_dir", ""))
    schedule_path = personal_dir / "schedule.md"
    entries = read_today_schedule(str(schedule_path), rt.today, rt.config)
    rt.state["schedule_entries"] = entries

    return {
        "date": str(rt.today),
        "entry_count": len(entries),
        "sample": entries[:5],
    }


def _tool_read_active_projects(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    personal_dir = Path(rt.config.get("_personal_dir", ""))
    projects_path = personal_dir / "projects.md"
    lookahead_days = args.get(
        "lookahead_days",
        rt.config.get("storage", {}).get("todo_lookahead_days", 3),
    )
    projects = read_active_projects(
        str(projects_path),
        rt.today,
        rt.config,
        lookahead_days=lookahead_days,
    )
    rt.state["projects"] = projects

    return {
        "date": str(rt.today),
        "project_count": len(projects),
        "sample_project_titles": [p.get("title", "") for p in projects[:5]],
    }


def _tool_build_digest_payload(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    now = datetime.now(rt.tz())
    today = now.date()

    schedule_entries = rt.state.get("schedule_entries")
    projects = rt.state.get("projects")
    news_items = rt.state.get("news_items", [])
    digest_summary = rt.state.get("digest_summary", "")

    content_blocks: list[str] = []
    if schedule_entries is not None:
        content_blocks.append("schedule")
    if projects is not None:
        content_blocks.append("todos")
    if news_items:
        content_blocks.append("news")

    payload = {
        "schedule_name": args["schedule_name"],
        "subject_prefix": args["subject_prefix"],
        "focus": args.get("focus", ""),
        "date": today,
        "datetime": now,
        "schedule_entries": schedule_entries,
        "projects": projects,
        "news_items": news_items,
        "digest_summary": digest_summary,
        "content_blocks": content_blocks,
    }
    rt.state["payload"] = payload

    return {
        "schedule_name": payload["schedule_name"],
        "subject_prefix": payload["subject_prefix"],
        "content_blocks": content_blocks,
        "schedule_entries_count": len(schedule_entries or []),
        "projects_count": len(projects or []),
        "news_items_count": len(news_items or []),
    }


def _tool_dispatch_notifications(args: dict[str, Any], rt: ToolRuntime) -> dict[str, Any]:
    payload = rt.state.get("payload")
    if not payload:
        raise ValueError("state.payload is empty; run build_digest_payload first")

    # State restored from SQLite stores date/datetime as strings; normalize for senders.
    payload = dict(payload)
    if isinstance(payload.get("date"), str):
        payload["date"] = date.fromisoformat(payload["date"])
    if isinstance(payload.get("datetime"), str):
        payload["datetime"] = datetime.fromisoformat(payload["datetime"])

    if rt.dry_run:
        return {
            "dry_run": True,
            "dispatched": False,
            "reason": "dry_run=true, skipped real notification dispatch",
        }

    dispatch_result = dispatch(payload, rt.config)
    return {
        "dry_run": False,
        "dispatched": True,
        "dispatch_result": dispatch_result,
    }


def build_agent_tools() -> dict[str, ToolSpec]:
    tools: list[ToolSpec] = [
        ToolSpec(
            name="collect_github",
            description=(
                "从 GitHub Trending 抓取热门仓库，结果追加到 state.raw_items。"
                "收集数据时按需调用此工具（可与 collect_rss、collect_youtube 并列使用）。"
                "参数：since 控制时间范围（daily/weekly/monthly），languages 过滤编程语言。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {
                    "max_repos": {"type": "integer", "minimum": 1, "maximum": 100},
                    "since": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                    "languages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                    },
                },
                "additionalProperties": False,
            },
            handler=_tool_collect_github,
        ),
        ToolSpec(
            name="collect_rss",
            description=(
                "从 config.yaml 中配置的 RSS 订阅源抓取文章，结果追加到 state.raw_items。"
                "参数：days_back 控制向前追溯天数（默认按配置），max_total 限制总条数。"
                "调用前无需其他工具，可与 collect_github、collect_youtube 并列使用。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {
                    "max_total": {"type": "integer", "minimum": 1, "maximum": 500},
                    "days_back": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "additionalProperties": False,
            },
            handler=_tool_collect_rss,
        ),
        ToolSpec(
            name="collect_youtube",
            description=(
                "从 YouTube 订阅频道和关键词搜索抓取视频，结果追加到 state.raw_items。"
                "参数：focus 为关键词/主题描述（影响搜索质量），max_total 限制总条数。"
                "调用前无需其他工具，可与 collect_github、collect_rss 并列使用。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {
                    "focus": {"type": "string"},
                    "max_total": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "additionalProperties": False,
            },
            handler=_tool_collect_youtube,
        ),
        ToolSpec(
            name="summarize_news",
            description=(
                "对 state.raw_items 中的原始数据进行两阶段 AI 筛选和评分，"
                "输出高质量的 state.news_items 列表，并生成 state.digest_summary 摘要。"
                "前置条件：至少调用过一次 collect_* 工具，state.raw_items 不为空。"
                "参数：focus 影响筛选偏好，min_score 过滤低分内容（1-10，默认按配置）。"
                "这一步耗时较长，每次日报只需调用一次。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "default": ""},
                    "min_score": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "additionalProperties": False,
            },
            handler=_tool_summarize_news,
        ),
        ToolSpec(
            name="read_today_schedule",
            description=(
                "用 AI 解析 config/personal/schedule.md，提取今日日程条目，"
                "写入 state.schedule_entries。无需参数，直接调用即可。"
                "适用于日报中包含「今日日程」板块时。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=_tool_read_today_schedule,
        ),
        ToolSpec(
            name="read_active_projects",
            description=(
                "用 AI 解析 config/personal/projects.md，提取活跃项目和近期待办，"
                "写入 state.projects。适用于日报中包含「待办/项目」板块时。"
                "参数：lookahead_days 控制截止日期向前预警天数（默认按配置）。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {
                    "lookahead_days": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "additionalProperties": False,
            },
            handler=_tool_read_active_projects,
        ),
        ToolSpec(
            name="build_digest_payload",
            description=(
                "将当前 session state（news_items、schedule_entries、projects、digest_summary）"
                "组装成通知 payload，写入 state.payload。"
                "前置条件：已完成所有需要的数据收集和摘要步骤。"
                "必须显式传入 schedule_name 和 subject_prefix（从任务描述中获取）。"
                "此工具是 dispatch_notifications 的前置步骤。"
            ),
            side_effect=False,
            input_schema={
                "type": "object",
                "properties": {
                    "schedule_name": {"type": "string"},
                    "subject_prefix": {"type": "string"},
                    "focus": {"type": "string", "default": ""},
                },
                "required": ["schedule_name", "subject_prefix"],
                "additionalProperties": False,
            },
            handler=_tool_build_digest_payload,
        ),
        ToolSpec(
            name="dispatch_notifications",
            description=(
                "将 state.payload 发送到已启用的通知渠道（邮件/飞书/企业微信）。"
                "前置条件：必须先调用 build_digest_payload。"
                "这是有副作用的工具，仅在策略允许时可用（正式推送模式）。"
                "dry-run 模式下此工具会跳过真实发送。"
            ),
            side_effect=True,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=_tool_dispatch_notifications,
        ),
    ]
    return {t.name: t for t in tools}
