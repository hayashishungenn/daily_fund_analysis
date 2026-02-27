# -*- coding: utf-8 -*-
"""
中国大陆法定工作日判断工具
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

_HAS_CHINESE_CALENDAR = False
try:
    from chinese_calendar import is_workday as _is_workday

    _HAS_CHINESE_CALENDAR = True
except Exception:
    _is_workday = None


def cn_today(now: Optional[datetime] = None) -> date:
    """获取北京时间下的今天日期。"""
    if now is None:
        now = datetime.now(TZ_CN)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=TZ_CN)
    else:
        now = now.astimezone(TZ_CN)
    return now.date()


def is_cn_legal_workday(check_date: Optional[date] = None) -> bool:
    """判断指定日期是否为中国大陆法定工作日。"""
    d = check_date or cn_today()
    if _HAS_CHINESE_CALENDAR and _is_workday is not None:
        try:
            return bool(_is_workday(d))
        except Exception as e:
            logger.warning(f"法定工作日判断失败，降级为周一到周五规则: {e}")
    return d.weekday() < 5


def should_run_today(
    workday_only: bool,
    force_run: bool,
    check_date: Optional[date] = None,
) -> Tuple[bool, str]:
    """
    返回今日是否应该执行分析，以及用于日志输出的说明文本。
    """
    d = check_date or cn_today()
    if force_run:
        return True, f"{d.isoformat()}（已启用强制执行）"
    if not workday_only:
        return True, f"{d.isoformat()}（未启用法定工作日限制）"
    allowed = is_cn_legal_workday(d)
    if allowed:
        return True, f"{d.isoformat()}（法定工作日）"
    return False, f"{d.isoformat()}（非法定工作日）"
