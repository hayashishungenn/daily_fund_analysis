# -*- coding: utf-8 -*-
"""
通知服务 - Telegram Bot + 邮件（SMTP）+ PushPlus + 企业微信 Webhook
"""
import hashlib
import html as _html
import json
import logging
import re
import smtplib
import ssl
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import List, Optional

from src.config import Config
from src.report import split_message

logger = logging.getLogger(__name__)

# SMTP 服务器自动识别表
_SMTP_SERVERS = {
    "qq.com":      ("smtp.qq.com",      465, True),
    "163.com":     ("smtp.163.com",     465, True),
    "126.com":     ("smtp.126.com",     465, True),
    "yeah.net":    ("smtp.yeah.net",    465, True),
    "gmail.com":   ("smtp.gmail.com",   587, False),
    "outlook.com": ("smtp.outlook.com", 587, False),
    "hotmail.com": ("smtp.hotmail.com", 587, False),
    "sina.com":    ("smtp.sina.com",    465, True),
    "sohu.com":    ("smtp.sohu.com",    465, True),
}

_DEFAULT_SMTP = ("smtp.qq.com", 465, True)


def _get_smtp_config(sender: str):
    domain = sender.split("@")[-1].lower()
    return _SMTP_SERVERS.get(domain, _DEFAULT_SMTP)


def _md_to_html(md: str) -> str:
    """Markdown -> HTML 转换（邮件专用）"""
    css_style = """
        body { font-family: -apple-system, "Segoe UI", Arial, sans-serif; line-height: 1.6; color: #24292e; font-size: 14px; padding: 16px; max-width: 900px; margin: 0 auto; }
        h1 { font-size: 20px; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; color: #0366d6; }
        h2 { font-size: 18px; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }
        h3 { font-size: 16px; }
        p { margin: 0 0 8px 0; }
        table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
        th, td { border: 1px solid #dfe2e5; padding: 6px 10px; text-align: left; }
        th { background-color: #f6f8fa; font-weight: 600; }
        ul, ol { padding-left: 20px; margin-bottom: 10px; }
        li { margin: 2px 0; }
        hr { height: 0.25em; margin: 16px 0; background-color: #e1e4e8; border: 0; }
        code { padding: 0.2em 0.4em; background-color: rgba(27,31,35,0.05); border-radius: 3px; }
        blockquote { color: #6a737d; border-left: 0.25em solid #dfe2e5; padding: 0 1em; margin: 0 0 10px 0; }
    """
    try:
        import markdown2
        html_body = markdown2.markdown(
            md,
            extras=["tables", "fenced-code-blocks", "break-on-newline", "cuddled-lists"],
        )
    except Exception:
        html_body = md
        html_body = re.sub(r"^#{1,3} (.+)$", r"<h3>\1</h3>", html_body, flags=re.MULTILINE)
        html_body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_body)
        html_body = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html_body)
        html_body = re.sub(r"- (.+)", r"<li>\1</li>", html_body)
        html_body = re.sub(r"\n", "<br>", html_body)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css_style}</style></head>
<body>{html_body}</body>
</html>"""


def _email_state_file(config: Config) -> Path:
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "email_last_send.json"


def _build_email_fingerprint(content: str, subject: str, sender: str, receivers: List[str]) -> str:
    normalized_content = content.strip().replace("\r\n", "\n")
    normalized_content = re.sub(
        r"报告生成时间：\d{2}:\d{2}(?::\d{2})?",
        "报告生成时间：<TIME>",
        normalized_content,
    )
    normalized_content = re.sub(
        r"\*\*更新\*\*：\d{2}:\d{2}(?::\d{2})?",
        "**更新**：<TIME>",
        normalized_content,
    )
    payload = {
        "subject": subject.strip(),
        "sender": sender.strip().lower(),
        "receivers": sorted([r.strip().lower() for r in receivers]),
        "content": normalized_content,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_recent_duplicate(state_file: Path, fingerprint: str, window_minutes: int) -> bool:
    if window_minutes <= 0 or not state_file.exists():
        return False
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        last_fp = state.get("fingerprint", "")
        last_ts = float(state.get("timestamp", 0))
        return last_fp == fingerprint and (time.time() - last_ts) < window_minutes * 60
    except Exception:
        return False


def _save_email_state(state_file: Path, fingerprint: str) -> None:
    state = {"fingerprint": fingerprint, "timestamp": time.time()}
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _md_to_telegram_html(md: str) -> str:
    """将 Markdown 转换为 Telegram HTML（避免特殊字符报错）"""
    text = _html.escape(md)             # 先转义 < > &
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,3} (.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


def send_telegram(content: str, config: Config) -> bool:
    """发送 Telegram 消息（HTML 模式，自动分割长消息，避免 Markdown 特殊字符崩溃）"""
    if not config.has_telegram():
        return False

    try:
        import asyncio
        from telegram import Bot
        from telegram.constants import ParseMode

        html_content = _md_to_telegram_html(content)

        async def _send():
            bot = Bot(token=config.telegram_bot_token)
            parts = split_message(html_content, max_len=4000)
            for i, part in enumerate(parts):
                try:
                    await bot.send_message(
                        chat_id=config.telegram_chat_id,
                        text=part,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    await bot.send_message(
                        chat_id=config.telegram_chat_id,
                        text=part,
                        disable_web_page_preview=True,
                    )
                if i < len(parts) - 1:
                    import asyncio as _a
                    await _a.sleep(1)

        asyncio.run(_send())
        logger.info("✅ Telegram 发送成功")
        return True

    except Exception as e:
        logger.error(f"❌ Telegram 发送失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 邮件
# ---------------------------------------------------------------------------

def send_email(content: str, config: Config, subject: Optional[str] = None) -> bool:
    """发送 HTML 邮件"""
    if not config.has_email():
        return False

    from datetime import datetime, timezone, timedelta
    tz_cn = timezone(timedelta(hours=8))
    date_str = datetime.now(tz_cn).strftime("%Y-%m-%d")

    if not subject:
        subject = f"📊 基金每日分析报告 {date_str}"

    receivers = config.email_receivers if config.email_receivers else [config.email_sender]
    smtp_host, smtp_port, use_ssl = _get_smtp_config(config.email_sender)
    state_file = _email_state_file(config)
    fingerprint = _build_email_fingerprint(content, subject, config.email_sender, receivers)

    if config.email_dedup_enabled and _is_recent_duplicate(
        state_file, fingerprint, config.email_dedup_window_minutes
    ):
        logger.info(
            f"⚠️ 邮件内容与最近一次一致（{config.email_dedup_window_minutes} 分钟内），跳过重复发送"
        )
        return True

    msg = MIMEMultipart("alternative")
    sender_name = str(Header(config.email_sender_name, "utf-8"))
    msg["From"] = formataddr((sender_name, config.email_sender))
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = str(Header(subject, "utf-8"))

    msg.attach(MIMEText("基金分析报告已生成，请查看 HTML 正文。", "plain", "utf-8"))
    msg.attach(MIMEText(_md_to_html(content), "html", "utf-8"))

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                server.login(config.email_sender, config.email_password)
                server.sendmail(config.email_sender, receivers, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(config.email_sender, config.email_password)
                server.sendmail(config.email_sender, receivers, msg.as_string())

        if config.email_dedup_enabled:
            _save_email_state(state_file, fingerprint)
        logger.info(f"✅ 邮件发送成功 -> {receivers}")
        return True

    except Exception as e:
        logger.error(f"❌ 邮件发送失败: {e}")
        return False


# ---------------------------------------------------------------------------
# PushPlus（微信推送，无需申请 Bot）
# ---------------------------------------------------------------------------

def send_pushplus(content: str, config: Config) -> bool:
    """PushPlus 微信公众号推送（免 Bot 申请，个人微信直推）"""
    if not config.pushplus_token:
        return False
    import requests
    from datetime import datetime, timezone, timedelta
    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    try:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": config.pushplus_token,
                "title": f"📊 基金每日分析报告 {date_str}",
                "content": content,
                "template": "markdown",
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 200:
            logger.info("✅ PushPlus 发送成功")
            return True
        logger.error(f"❌ PushPlus 失败: {data}")
        return False
    except Exception as e:
        logger.error(f"❌ PushPlus 异常: {e}")
        return False


# ---------------------------------------------------------------------------
# 企业微信 WeCom Webhook
# ---------------------------------------------------------------------------

def send_wecom(content: str, config: Config) -> bool:
    """企业微信机器人 Webhook 推送（支持 Markdown）"""
    if not config.wecom_webhook:
        return False
    import requests
    try:
        resp = requests.post(
            config.wecom_webhook,
            json={"msgtype": "markdown", "markdown": {"content": content[:4096]}},
            timeout=15,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            logger.info("✅ 企业微信发送成功")
            return True
        logger.error(f"❌ 企业微信失败: {data}")
        return False
    except Exception as e:
        logger.error(f"❌ 企业微信异常: {e}")
        return False


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def send_report(content: str, config: Config) -> dict:
    """
    同时尝试所有已配置的通知渠道

    Returns:
        {"telegram": bool, "email": bool, "pushplus": bool, "wecom": bool}
    """
    results = {}

    if config.has_telegram():
        results["telegram"] = send_telegram(content, config)
    else:
        logger.info("Telegram 未配置，跳过")

    if config.has_email():
        results["email"] = send_email(content, config)
    else:
        logger.info("邮件未配置，跳过")

    if config.pushplus_token:
        results["pushplus"] = send_pushplus(content, config)
    else:
        logger.info("PushPlus 未配置，跳过")

    if config.wecom_webhook:
        results["wecom"] = send_wecom(content, config)
    else:
        logger.info("企业微信未配置，跳过")

    if not results:
        logger.warning("⚠️  未配置任何通知渠道，报告仅输出到控制台")

    return results
