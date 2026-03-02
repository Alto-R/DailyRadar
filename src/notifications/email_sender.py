"""
email_sender.py - SMTP 邮件发送（Jinja2 HTML 模板）
改编自 obsidian-daily-digest/mailer.py
"""

import logging
import os
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _parse_recipients(raw: str) -> dict[str, str]:
    """
    解析 EMAIL_TO 字符串，返回 {email: name} 字典。
    支持两种格式：
      - 姓名:邮箱  （新格式，如 "张三:foo@example.com"）
      - 纯邮箱     （旧格式，如 "foo@example.com"，name 留空）
    """
    result: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            # 只以第一个冒号分割，避免邮箱中无冒号但名字含冒号的情况
            name, _, email = entry.partition(":")
            result[email.strip()] = name.strip()
        else:
            result[entry] = ""
    return result


def _get_recipients(config: dict) -> list[str]:
    """收件人：优先 EMAIL_TO 环境变量，其次 config 中的 recipients。"""
    env_to = os.environ.get("EMAIL_TO", "")
    if env_to:
        return list(_parse_recipients(env_to).keys())
    return config.get("notifications", {}).get("email", {}).get("recipients", [])


def _get_recipient_map(config: dict) -> dict[str, str]:
    """返回 {email: name} 映射，供日志或个性化使用。"""
    env_to = os.environ.get("EMAIL_TO", "")
    if env_to:
        return _parse_recipients(env_to)
    recipients = config.get("notifications", {}).get("email", {}).get("recipients", [])
    return {r: "" for r in recipients}


def _render_html(payload: dict, config: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("email.html")
    today: date = payload["date"]
    return template.render(
        subject_prefix=payload.get("subject_prefix", "DailyRadar"),
        schedule_name=payload.get("schedule_name", ""),
        focus=payload.get("focus", ""),
        date_str=today.strftime("%Y-%m-%d"),
        weekday_str=WEEKDAY_ZH[today.weekday()],
        schedule_entries=payload.get("schedule_entries") or [],
        todos=payload.get("todos") or [],
        news_items=payload.get("news_items") or [],
        ai_model=config.get("ai", {}).get("model", ""),
    )


def _smtp_send(smtp_server: str, smtp_port: int, smtp_user: str, smtp_pass: str,
               recipients: list[str], msg: MIMEMultipart) -> None:
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())


def send_email(payload: dict, config: dict) -> bool:
    """
    渲染 email.html 模板并通过 SMTP 发送。

    schedule / todos 属于个人隐私内容，只发给 EMAIL_FROM（发件人自己）；
    其他收件人只收到新闻精选部分。

    Args:
        payload: 来自 main.py 的数据字典
        config:  AppConfig dict

    Returns:
        True 表示至少一封邮件发送成功
    """
    smtp_server = os.environ.get("EMAIL_SMTP_SERVER", "smtp.qq.com")
    smtp_port   = int(os.environ.get("EMAIL_SMTP_PORT", "465"))
    smtp_user   = os.environ.get("EMAIL_FROM", "")
    smtp_pass   = os.environ.get("EMAIL_PASSWORD", "")
    recipients  = _get_recipients(config)
    recipient_map = _get_recipient_map(config)

    if not smtp_user or not smtp_pass:
        logger.error("邮件配置缺失: EMAIL_FROM / EMAIL_PASSWORD 未设置")
        return False

    if not recipients:
        logger.error("邮件配置缺失: EMAIL_TO 未设置")
        return False

    today: date = payload["date"]
    subject = f"{payload.get('subject_prefix', 'DailyRadar')} · {today.strftime('%Y-%m-%d')}"
    has_personal = bool(payload.get("schedule_entries") or payload.get("todos"))

    # 有个人内容时：个人邮箱收完整版，其他收件人收纯新闻版
    personal_recipients = [r for r in recipients if r == smtp_user] if has_personal else []
    other_recipients    = [r for r in recipients if r != smtp_user] if has_personal else recipients

    success = False

    def _make_msg(html: str, to: list[str]) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg

    def _fmt(addrs: list[str]) -> str:
        return ", ".join(
            f"{recipient_map.get(a, '')}<{a}>" if recipient_map.get(a) else a
            for a in addrs
        )

    try:
        if personal_recipients:
            html = _render_html(payload, config)
            _smtp_send(smtp_server, smtp_port, smtp_user, smtp_pass,
                       personal_recipients, _make_msg(html, personal_recipients))
            logger.info(f"邮件已发送（完整版）: {subject} → {_fmt(personal_recipients)}")
            success = True

        if other_recipients:
            news_payload = {**payload, "schedule_entries": [], "todos": []}
            html = _render_html(news_payload, config)
            _smtp_send(smtp_server, smtp_port, smtp_user, smtp_pass,
                       other_recipients, _make_msg(html, other_recipients))
            logger.info(f"邮件已发送（新闻版）: {subject} → {_fmt(other_recipients)}")
            success = True

    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP 认证失败：请检查 EMAIL_FROM / EMAIL_PASSWORD")
        return False
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False

    return success
