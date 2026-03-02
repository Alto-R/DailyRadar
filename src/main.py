"""
main.py - DailyRadar 主编排器
===================================
被 Docker entrypoint / supercronic 调用:
  python -m src.main --schedule-name "早间日报"
  python -m src.main --schedule-name "早间日报" --dry-run

执行流程:
  1. 加载配置 (config.yaml + .env)
  2. 找到匹配的 schedule entry
  3. 按 content 列表决定运行哪些模块
  4. 采集 → AI摘要 → 组装payload → 分发通知
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config_loader import load_config

logger = logging.getLogger("dailyradar")

WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def run(schedule_name: str, config: dict, dry_run: bool = False):
    """
    执行一次完整的日报生成和推送流程。

    Args:
        schedule_name: 匹配 config["schedules"] 中的 name 字段
        config:        AppConfig dict
        dry_run:       True 时打印预览，不发送通知
    """
    # ── 读取上次 JSON 中用户填写的分数，写入 feedback.db ─────
    _apply_pending_feedback(config)

    # ── 找到匹配的 schedule entry ─────────────────────────────
    schedule = next(
        (s for s in config.get("schedules", []) if s.get("name") == schedule_name),
        None,
    )
    if schedule is None:
        schedules = config.get("schedules", [])
        if schedules:
            schedule = schedules[0]
            logger.warning(f"Schedule '{schedule_name}' 未找到，使用第一个: '{schedule['name']}'")
        else:
            logger.error("config.yaml 中没有定义任何 schedules")
            sys.exit(1)

    content_blocks = schedule.get("content", ["news"])
    sources        = schedule.get("sources", ["github", "youtube", "rss"])
    subject_prefix = schedule.get("subject_prefix", "DailyRadar")
    focus          = schedule.get("focus", "")
    tz = ZoneInfo(config.get("app", {}).get("timezone", "Asia/Shanghai"))
    now = datetime.now(tz)
    today = now.date()

    logger.info(f"▶ Schedule: '{schedule['name']}' | content={content_blocks} | sources={sources} | focus={focus!r}")

    # ── Section 1: 个人助手 ───────────────────────────────────
    schedule_entries = None
    todos = None

    if "schedule" in content_blocks:
        from src.personal.schedule_reader import read_today_schedule
        personal_dir = config.get("_personal_dir", "/app/config/personal")
        schedule_path = str(Path(personal_dir) / "schedule.yaml")
        schedule_entries = read_today_schedule(schedule_path, today, tz)
        logger.info(f"  日程: {len(schedule_entries)} 条")

    if "todos" in content_blocks:
        from src.personal.todo_reader import read_due_todos
        personal_dir = config.get("_personal_dir", "/app/config/personal")
        todos_path = str(Path(personal_dir) / "todos.yaml")
        lookahead = config.get("storage", {}).get("todo_lookahead_days", 3)
        todos = read_due_todos(todos_path, today, lookahead)
        logger.info(f"  TODO: {len(todos)} 条需提醒")

    # ── Section 2: 新闻采集 + AI 摘要 ──────────────────────────
    news_items = []

    if "news" in content_blocks:
        raw_items = []
        collectors_cfg = config.get("collectors", {})

        if "github" in sources and collectors_cfg.get("github", {}).get("enabled", True):
            logger.info("📦 抓取 GitHub...")
            try:
                from src.collectors.github_collector import collect_github
                items = collect_github(config)
                raw_items.extend(items)
                logger.info(f"   GitHub: {len(items)} 个仓库")
            except Exception as e:
                logger.error(f"   GitHub 失败: {e}")

        if "youtube" in sources and collectors_cfg.get("youtube", {}).get("enabled", False):
            logger.info("📺 抓取 YouTube...")
            try:
                from src.collectors.youtube_collector import collect_youtube
                items = collect_youtube(config, focus=focus)
                raw_items.extend(items)
                logger.info(f"   YouTube: {len(items)} 个视频")
            except Exception as e:
                logger.error(f"   YouTube 失败: {e}")

        if "rss" in sources and collectors_cfg.get("rss", {}).get("enabled", True):
            logger.info("📰 抓取 RSS...")
            try:
                from src.collectors.rss_collector import collect_rss
                items = collect_rss(config)
                raw_items.extend(items)
                logger.info(f"   RSS: {len(items)} 篇文章")
            except Exception as e:
                logger.error(f"   RSS 失败: {e}")

        logger.info(f"采集完成，共 {len(raw_items)} 条原始内容")

        if raw_items:
            logger.info("🤖 AI 摘要中...")
            from src.ai.summarizer import summarize_items
            news_items = summarize_items(raw_items, config, focus=focus)
            logger.info(f"   筛选后: {len(news_items)} 条")

    # ── Section 3: 组装 Payload ──────────────────────────────
    payload = {
        "schedule_name":    schedule["name"],
        "subject_prefix":   subject_prefix,
        "focus":            focus,
        "date":             today,
        "datetime":         now,
        "schedule_entries": schedule_entries,
        "todos":            todos,
        "news_items":       news_items,
        "content_blocks":   content_blocks,
    }

    # ── Section 4: 分发通知 ──────────────────────────────────
    if dry_run:
        _print_dry_run(payload)
    else:
        from src.notifications.dispatcher import dispatch
        dispatch(payload, config)

    # ── Section 5: 保存新闻条目供反馈打分使用 ────────────────
    if news_items:
        _save_last_digest(news_items, today, config)

    logger.info("✅ 完成")


def _apply_pending_feedback(config: dict):
    """
    读取 data/last_digest.json，将用户已填写 user_score（1-5）的条目
    写入 feedback.db，然后将这些条目的 user_score 清空（避免重复写入）。
    """
    import json
    from pathlib import Path
    from src.ai.feedback import save_feedback, init_db

    data_dir = Path(config.get("storage", {}).get("data_dir", "/app/data"))
    path = data_dir / "last_digest.json"
    if not path.exists():
        return

    try:
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
    except Exception as e:
        logger.warning(f"读取 last_digest.json 失败: {e}")
        return

    init_db(config)
    applied = 0
    for r in records:
        score = r.get("user_score")
        if score is not None and isinstance(score, int) and 1 <= score <= 5:
            save_feedback(
                config,
                date_str=r.get("date", ""),
                source=r.get("source", ""),
                title=r.get("title", ""),
                url=r.get("url", ""),
                score=score,
                ai_summary=r.get("ai_summary", ""),
                notes=r.get("user_notes", ""),
            )
            r["user_score"] = None   # 清空，避免下次重复写入
            r["user_notes"] = ""
            applied += 1

    if applied:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        logger.info(f"✨ 已将 {applied} 条用户反馈写入偏好数据库")


def _save_last_digest(news_items: list[dict], today: date, config: dict):
    """
    将本次新闻条目保存到 data/last_digest.json。
    每条记录预留 user_score / user_notes 字段（默认 null / ""），
    用户可直接编辑此文件填写分数，下次运行时自动写入偏好数据库。
    """
    import json
    from pathlib import Path

    data_dir = Path(config.get("storage", {}).get("data_dir", "/app/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / "last_digest.json"

    records = []
    for item in news_items:
        records.append({
            "date":       str(today),
            "source":     item.get("source", ""),
            "title":      item.get("title", ""),
            "url":        item.get("url", ""),
            "ai_score":   item.get("ai_score"),
            "ai_summary": item.get("ai_summary", ""),
            # ── 在此填写你的评分后，下次运行时自动生效 ──────────
            "user_score": None,   # 填 1-5 整数，null 表示跳过
            "user_notes": "",     # 备注（可留空）
        })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    logger.info(f"📋 已保存 {len(records)} 条内容到 {out_path}（填写 user_score 后下次运行自动学习偏好）")


def _print_dry_run(payload: dict):
    print(f"\n{'='*60}")
    print(f"DRY RUN: {payload['subject_prefix']} | {payload['date']}")
    print(f"{'='*60}")

    entries = payload.get("schedule_entries") or []
    if entries:
        print(f"\n--- 今日日程 ({len(entries)} 条) ---")
        for e in entries:
            loc = f" @ {e['location']}" if e.get("location") else ""
            print(f"  {e['time']}  {e['title']}{loc}")

    todos = payload.get("todos") or []
    if todos:
        print(f"\n--- TODO ({len(todos)} 条) ---")
        for t in todos:
            print(f"  [{t['status']}] {t['title']} (due: {t['due']})")

    news = payload.get("news_items") or []
    if news:
        print(f"\n--- 新闻精选 ({len(news)} 条) ---")
        for item in news[:5]:
            print(f"  [{item['ai_score']}/10][{item['source']}] {item['title'][:60]}")
        if len(news) > 5:
            print(f"  ... 还有 {len(news)-5} 条")


def main():
    parser = argparse.ArgumentParser(description="DailyRadar - 个人 AI 日报服务")
    parser.add_argument(
        "--schedule-name",
        default="",
        help="要执行的调度名称（匹配 config.schedules[].name，空则用第一个）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="打印预览，不发送通知",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config()
    run(
        schedule_name=args.schedule_name,
        config=config,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
