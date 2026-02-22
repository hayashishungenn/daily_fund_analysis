# -*- coding: utf-8 -*-
"""
定时任务调度 - 支持本地/Docker 长驻运行
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable

import schedule

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))


def run_with_schedule(
    task: Callable,
    schedule_time: str = "15:35",
    run_immediately: bool = True,
) -> None:
    """
    启动定时任务循环

    Args:
        task: 每次触发时调用的函数
        schedule_time: 24小时制 HH:MM，北京时间
        run_immediately: 首次启动时是否立即执行一次
    """
    logger.info(f"定时任务模式启动，每日 {schedule_time}（北京时间）执行")

    # 将北京时间转换为本地 schedule 时间
    # schedule 库使用本地时间；在 GitHub Actions(UTC) 上需换算
    schedule.every().day.at(schedule_time).do(task)

    if run_immediately:
        logger.info("首次立即执行...")
        task()

    logger.info("等待下一次定时触发，按 Ctrl+C 退出...")
    while True:
        schedule.run_pending()
        time.sleep(30)
