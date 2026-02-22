# -*- coding: utf-8 -*-
"""
日志配置
"""
import logging
import sys
from pathlib import Path
from datetime import datetime


def setup_logging(log_prefix: str = "fund_analysis", debug: bool = False, log_dir: str = "./logs") -> None:
    """配置控制台和文件双输出日志"""
    level = logging.DEBUG if debug else logging.INFO
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    log_file = log_dir_path / f"{log_prefix}_{datetime.now().strftime('%Y%m%d')}.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    # 文件
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # 抑制第三方库噪声
    for noisy in ("httpx", "httpcore", "urllib3", "requests", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
