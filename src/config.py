# -*- coding: utf-8 -*-
"""
配置管理 - 从环境变量和 .env 文件读取所有配置
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _split_list(value: str) -> List[str]:
    """将逗号分隔字符串拆分为列表，过滤空项"""
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Config:
    # 自选基金列表
    fund_list: List[str] = field(default_factory=list)
    report_days: int = 30
    report_type: str = "full"  # simple / full

    # AI 配置
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"
    gemini_temperature: float = 0.7
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.7

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # 邮件
    email_sender: Optional[str] = None
    email_password: Optional[str] = None
    email_receivers: List[str] = field(default_factory=list)
    email_sender_name: str = "基金每日分析助手"
    email_dedup_enabled: bool = True
    email_dedup_window_minutes: int = 180

    # PushPlus（微信推送）
    pushplus_token: Optional[str] = None

    # 企业微信 Webhook
    wecom_webhook: Optional[str] = None

    # 系统
    log_dir: str = "./logs"
    log_level: str = "INFO"
    max_workers: int = 1
    use_proxy: bool = False
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 10809

    def has_ai(self) -> bool:
        return bool(self.gemini_api_key or self.openai_api_key)

    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def has_email(self) -> bool:
        return bool(self.email_sender and self.email_password)

    def has_notification(self) -> bool:
        return self.has_telegram() or self.has_email()

    def validate(self) -> List[str]:
        warnings = []
        if not self.fund_list:
            warnings.append("⚠️  FUND_LIST 未配置，将使用默认示例基金")
        if self.report_type not in ("simple", "full"):
            warnings.append(
                f"⚠️  REPORT_TYPE={self.report_type} 非法，将自动回退为 full（可选: simple/full）"
            )
        if not self.has_ai():
            warnings.append("⚠️  未配置 AI API Key（GEMINI_API_KEY / OPENAI_API_KEY），将使用规则引擎生成建议")
        if not self.has_notification():
            warnings.append("⚠️  未配置任何通知渠道，报告仅输出到控制台")
        return warnings


def get_config() -> Config:
    fund_list_raw = os.getenv("FUND_LIST", "110022,003095,100032")
    email_receivers_raw = os.getenv("EMAIL_RECEIVERS", "")

    return Config(
        fund_list=_split_list(fund_list_raw),
        report_days=int(os.getenv("REPORT_DAYS", "30")),
        report_type=(os.getenv("REPORT_TYPE", "full") or "full").strip().lower(),

        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        gemini_temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.7")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.7")),

        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,

        email_sender=os.getenv("EMAIL_SENDER") or None,
        email_password=os.getenv("EMAIL_PASSWORD") or None,
        email_receivers=_split_list(email_receivers_raw) if email_receivers_raw else [],
        email_sender_name=os.getenv("EMAIL_SENDER_NAME", "基金每日分析助手"),
        email_dedup_enabled=os.getenv("EMAIL_DEDUP_ENABLED", "true").lower() == "true",
        email_dedup_window_minutes=int(os.getenv("EMAIL_DEDUP_WINDOW_MINUTES", "180")),

        pushplus_token=os.getenv("PUSHPLUS_TOKEN") or None,
        wecom_webhook=os.getenv("WECOM_WEBHOOK") or None,

        log_dir=os.getenv("LOG_DIR", "./logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_workers=int(os.getenv("MAX_WORKERS", "1")),
        use_proxy=os.getenv("USE_PROXY", "false").lower() == "true",
        proxy_host=os.getenv("PROXY_HOST", "127.0.0.1"),
        proxy_port=int(os.getenv("PROXY_PORT", "10809")),
    )
