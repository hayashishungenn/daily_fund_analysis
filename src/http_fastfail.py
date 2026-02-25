# -*- coding: utf-8 -*-
"""HTTP 快失败补丁：为 requests 默认注入短超时和更低重试。"""

import logging
import threading

logger = logging.getLogger(__name__)

_PATCH_LOCK = threading.Lock()
_PATCHED = False


def install_requests_fast_fail(
    connect_timeout: float = 3.0,
    read_timeout: float = 8.0,
    max_retries: int = 0,
    retry_backoff: float = 0.0,
) -> None:
    """给 requests.Session 打补丁，减少数据源请求长时间卡住。"""
    global _PATCHED

    connect_timeout = max(0.1, float(connect_timeout))
    read_timeout = max(0.1, float(read_timeout))
    max_retries = max(0, int(max_retries))
    retry_backoff = max(0.0, float(retry_backoff))

    with _PATCH_LOCK:
        if _PATCHED:
            return

        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
        except Exception as e:
            logger.warning(f"requests fast-fail 补丁初始化失败: {e}")
            return

        original_init = requests.sessions.Session.__init__
        original_request = requests.sessions.Session.request

        retry_template = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            redirect=max_retries,
            backoff_factor=retry_backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            raise_on_status=False,
        )

        def _patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            adapter = HTTPAdapter(max_retries=retry_template)
            self.mount("http://", adapter)
            self.mount("https://", adapter)

        def _patched_request(self, method, url, **kwargs):
            kwargs.setdefault("timeout", (connect_timeout, read_timeout))
            return original_request(self, method, url, **kwargs)

        requests.sessions.Session.__init__ = _patched_init
        requests.sessions.Session.request = _patched_request
        _PATCHED = True

        logger.info(
            "已启用数据源 fast-fail: timeout=(%.1fs, %.1fs), retries=%d, backoff=%.2f",
            connect_timeout,
            read_timeout,
            max_retries,
            retry_backoff,
        )
