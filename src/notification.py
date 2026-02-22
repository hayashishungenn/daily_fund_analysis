# -*- coding: utf-8 -*-
"""
通知服务 - Telegram Bot + 邮件（SMTP）
"""
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    """极简 Markdown -> HTML 转换（用于邮件）"""
    import re
    html = md
    html = re.sub(r"^#{1,3} (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
    html = re.sub(r"━+", "<hr>", html)
    html = re.sub(r"\n", "<br>", html)
    return f"<html><body style='font-family:sans-serif;line-height:1.6'>{html}</body></html>"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(content: str, config: Config) -> bool:
    """发送 Telegram 消息（自动分割长消息）"""
    if not config.has_telegram():
        return False

    try:
        import asyncio
        from telegram import Bot
        from telegram.constants import ParseMode

        async def _send():
            bot = Bot(token=config.telegram_bot_token)
            parts = split_message(content, max_len=4000)
            for i, part in enumerate(parts):
                try:
                    await bot.send_message(
                        chat_id=config.telegram_chat_id,
                        text=part,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    # Markdown 解析失败，降级为纯文本
                    await bot.send_message(
                        chat_id=config.telegram_chat_id,
                        text=part,
                        disable_web_page_preview=True,
                    )
                if i < len(parts) - 1:
                    import asyncio as _a
                    await _a.sleep(1)

        asyncio.run(_send())
        logger.info(f"✅ Telegram 发送成功")
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

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{config.email_sender_name} <{config.email_sender}>"
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = subject

    msg.attach(MIMEText(content, "plain", "utf-8"))
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

        logger.info(f"✅ 邮件发送成功 -> {receivers}")
        return True

    except Exception as e:
        logger.error(f"❌ 邮件发送失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def send_report(content: str, config: Config) -> dict:
    """
    同时尝试所有已配置的通知渠道

    Returns:
        {"telegram": bool, "email": bool}
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

    if not results:
        logger.warning("⚠️  未配置任何通知渠道，报告仅输出到控制台")

    return results
