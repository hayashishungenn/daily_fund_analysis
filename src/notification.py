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
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.65; color: #24292e; font-size: 16px; padding: 20px; max-width: 1040px; margin: 0 auto; }
        h1 { font-size: 24px; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; color: #0366d6; margin: 0 0 12px 0; }
        h2 { font-size: 20px; border-bottom: 1px solid #eaecef; padding-bottom: 0.25em; margin: 18px 0 10px 0; }
        h3 { font-size: 18px; margin: 14px 0 8px 0; }
        h4 { font-size: 16px; margin: 12px 0 6px 0; }
        p { margin: 0 0 8px 0; }
        table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 14px; }
        th, td { border: 1px solid #dfe2e5; padding: 6px 10px; text-align: left; }
        th { background-color: #f6f8fa; font-weight: 600; }
        ul, ol { padding-left: 22px; margin: 6px 0 10px 0; }
        li { margin: 3px 0; }
        hr { height: 0.2em; margin: 14px 0; background-color: #e1e4e8; border: 0; }
        code { padding: 0.2em 0.4em; background-color: rgba(27,31,35,0.05); border-radius: 3px; }
        pre { margin: 10px 0; padding: 10px 12px; background: #f6f8fa; border: 1px solid #e5e7eb; border-radius: 4px; line-height: 1.45; font-size: 14px; overflow-x: auto; white-space: pre-wrap; }
        blockquote { color: #4b5563; border-left: 0.25em solid #dfe2e5; padding: 0 1em; margin: 8px 0; }
        table.text-grid { width: auto; table-layout: auto; }
        table.text-grid th, table.text-grid td { font-family: Consolas, "SFMono-Regular", "Liberation Mono", monospace; white-space: nowrap; font-size: 13px; }
        table.text-grid th.align-right, table.text-grid td.align-right { text-align: right; }
        table.text-grid th.align-left, table.text-grid td.align-left { text-align: left; }
    """

    def _format_inline_md(text: str) -> str:
        s = _html.escape(text)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
        return s

    def _looks_like_text_table(code_lines: List[str]) -> bool:
        if len(code_lines) < 2:
            return False
        header = code_lines[0]
        separator = code_lines[1].strip()
        return (
            "|" in header
            and "+" in separator
            and separator
            and set(separator) <= {"-", "+", " "}
        )

    def _is_numeric_like(cell: str) -> bool:
        return bool(re.match(r"^[+\-]?\d+(?:\.\d+)?%?$", cell))

    def _render_text_table(code_lines: List[str]) -> Optional[str]:
        if not _looks_like_text_table(code_lines):
            return None

        rows = []
        for raw in code_lines:
            if not raw.strip():
                continue
            rows.append([cell.strip() for cell in raw.split("|")])

        if len(rows) < 3:
            return None

        header_cells = rows[0]
        body_rows = rows[2:]
        if not header_cells:
            return None

        normalized_body = []
        for row in body_rows:
            if len(row) < len(header_cells):
                row = row + [""] * (len(header_cells) - len(row))
            normalized_body.append(row[: len(header_cells)])

        aligns = []
        for col_idx in range(len(header_cells)):
            col_values = [row[col_idx] for row in normalized_body if row[col_idx]]
            if col_values and all(_is_numeric_like(value) for value in col_values):
                aligns.append("align-right")
            else:
                aligns.append("align-left")

        table_html = ['<table class="text-grid"><thead><tr>']
        for idx, cell in enumerate(header_cells):
            table_html.append(f'<th class="{aligns[idx]}">{_format_inline_md(cell)}</th>')
        table_html.append("</tr></thead><tbody>")
        for row in normalized_body:
            table_html.append("<tr>")
            for idx, cell in enumerate(row):
                table_html.append(f'<td class="{aligns[idx]}">{_format_inline_md(cell)}</td>')
            table_html.append("</tr>")
        table_html.append("</tbody></table>")
        return "".join(table_html)

    def _render_markdown_fallback(markdown_text: str) -> str:
        lines = markdown_text.splitlines()
        html_parts: List[str] = []
        paragraph: List[str] = []
        list_items: List[str] = []
        i = 0

        def _flush_paragraph() -> None:
            nonlocal paragraph
            if paragraph:
                html_parts.append(f"<p>{'<br>'.join(_format_inline_md(x) for x in paragraph)}</p>")
                paragraph = []

        def _flush_list() -> None:
            nonlocal list_items
            if list_items:
                html_parts.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
                list_items = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("```"):
                _flush_paragraph()
                _flush_list()
                code_lines: List[str] = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                text_table_html = _render_text_table(code_lines)
                if text_table_html:
                    html_parts.append(text_table_html)
                else:
                    html_parts.append(f"<pre>{_html.escape(chr(10).join(code_lines))}</pre>")
                i += 1
                continue

            if stripped.startswith("|") and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if re.match(r"^\|?[\s:\-|\u2014]+\|?$", next_line):
                    _flush_paragraph()
                    _flush_list()
                    table_lines = [line]
                    i += 2
                    while i < len(lines) and lines[i].strip().startswith("|"):
                        table_lines.append(lines[i])
                        i += 1

                    rows = []
                    for raw in table_lines:
                        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
                        rows.append(cells)

                    if rows:
                        head = rows[0]
                        body = rows[1:]
                        table_html = ["<table><thead><tr>"]
                        table_html.extend(f"<th>{_format_inline_md(c)}</th>" for c in head)
                        table_html.append("</tr></thead><tbody>")
                        for row in body:
                            table_html.append("<tr>")
                            table_html.extend(f"<td>{_format_inline_md(c)}</td>" for c in row)
                            table_html.append("</tr>")
                        table_html.append("</tbody></table>")
                        html_parts.append("".join(table_html))
                    continue

            if not stripped:
                _flush_paragraph()
                _flush_list()
                i += 1
                continue

            if stripped == "---":
                _flush_paragraph()
                _flush_list()
                html_parts.append("<hr>")
                i += 1
                continue

            m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if m:
                _flush_paragraph()
                _flush_list()
                level = min(len(m.group(1)), 4)
                html_parts.append(f"<h{level}>{_format_inline_md(m.group(2))}</h{level}>")
                i += 1
                continue

            if stripped.startswith("> "):
                _flush_paragraph()
                _flush_list()
                html_parts.append(f"<blockquote>{_format_inline_md(stripped[2:])}</blockquote>")
                i += 1
                continue

            if stripped.startswith("- "):
                _flush_paragraph()
                list_items.append(_format_inline_md(stripped[2:]))
                i += 1
                continue

            paragraph.append(line)
            i += 1

        _flush_paragraph()
        _flush_list()
        return "".join(html_parts)

    # 邮件正文采用固定渲染器，避免不同环境下 fenced code / table 的输出不一致。
    html_body = _render_markdown_fallback(md)

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
    code_blocks: List[str] = []

    def _capture_code_block(match: re.Match) -> str:
        code_blocks.append(match.group(1).strip("\n"))
        return f"@@CODEBLOCK_{len(code_blocks) - 1}@@"

    text = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", _capture_code_block, text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,4} (.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    for i, block in enumerate(code_blocks):
        text = text.replace(f"@@CODEBLOCK_{i}@@", f"<pre>{block}</pre>")
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
