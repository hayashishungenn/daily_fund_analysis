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
    schedule_time: str = "14:00",
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

    # 任务内部可自行判断“是否法定工作日”，调度层只负责按时触发。
    schedule.every().day.at(schedule_time).do(task)

    if run_immediately:
        logger.info("首次立即执行...")
        task()

    logger.info("等待下一次定时触发，按 Ctrl+C 退出...")
    while True:
        schedule.run_pending()
        time.sleep(30)
