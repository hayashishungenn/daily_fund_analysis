# -*- coding: utf-8 -*-
"""
基金数据获取模块
- 使用 akshare 拉取: 基本信息、历史净值、持仓数据
- 计算技术指标: MA5/MA10/MA20、最大回撤、近期收益率
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FundInfo:
    """基金基本信息 + 最新状态"""
    code: str
    name: str = "未知"
    fund_type: str = "未知"          # 股票型、混合型、债券型等
    manager: str = "未知"
    size_billion: float = 0.0        # 基金规模（亿元）
    latest_nav: float = 0.0          # 最新单位净值
    latest_date: str = ""            # 净值日期
    nav_change_pct: float = 0.0      # 今日涨跌幅(%)
    cumulative_nav: float = 0.0      # 累计净值


@dataclass
class FundHistory:
    """历史净值序列及技术指标"""
    code: str
    dates: List[str] = field(default_factory=list)
    navs: List[float] = field(default_factory=list)
    # 技术指标
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    max_drawdown_pct: float = 0.0    # 最大回撤(%)
    ret_7d: float = 0.0              # 近7日收益率
    ret_30d: float = 0.0             # 近30日收益率
    ret_90d: float = 0.0             # 近90日收益率
    trend_signal: str = "震荡"       # 多头排列/空头排列/震荡


@dataclass
class FundAnalysisData:
    """完整分析数据包，传给分析器"""
    info: FundInfo
    history: FundHistory
    top_holdings: List[dict] = field(default_factory=list)   # 前十大持仓
    error: Optional[str] = None                               # 获取失败时填入错误信息


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _calc_max_drawdown(navs: List[float]) -> float:
    """计算最大回撤"""
    if len(navs) < 2:
        return 0.0
    arr = np.array(navs, dtype=float)
    peak = np.maximum.accumulate(arr)
    drawdowns = (arr - peak) / np.where(peak == 0, 1, peak)
    return float(drawdowns.min() * 100)


def _calc_return(navs: List[float], days: int) -> float:
    """计算近 N 天收益率(%)"""
    if len(navs) >= days + 1:
        start = navs[-(days + 1)]
        end = navs[-1]
        return (end - start) / start * 100 if start else 0.0
    elif len(navs) >= 2:
        return (navs[-1] - navs[0]) / navs[0] * 100 if navs[0] else 0.0
    return 0.0


def _trend_signal(ma5: Optional[float], ma10: Optional[float], ma20: Optional[float]) -> str:
    """根据均线判断趋势"""
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            return "多头排列"
        if ma5 < ma10 < ma20:
            return "空头排列"
    return "震荡"


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

def fetch_fund_basic_info(code: str) -> FundInfo:
    """获取基金基本信息"""
    try:
        import akshare as ak
        df = ak.fund_individual_basic_info_xq(symbol=code)
        # df 结构：两列，第一列为字段名，第二列为值
        info_dict = {}
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                key = str(row.iloc[0]).strip()
                val = row.iloc[1]
                info_dict[key] = val
        return FundInfo(
            code=code,
            name=str(info_dict.get("基金名称", info_dict.get("名称", "未知"))),
            fund_type=str(info_dict.get("基金类型", info_dict.get("类型", "未知"))),
            manager=str(info_dict.get("基金经理", info_dict.get("经理", "未知"))),
            size_billion=float(info_dict.get("基金规模", 0) or 0),
        )
    except Exception as e:
        logger.warning(f"[{code}] 获取基金基本信息失败（xq）: {e}，尝试备用接口...")
        return _fetch_basic_info_fallback(code)


def _fetch_basic_info_fallback(code: str) -> FundInfo:
    """备用接口获取基金基本信息（东方财富）"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        # 已知列名: ['净值日期', '单位净值', '日增长率']
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return FundInfo(
                code=code,
                name=code,   # 无法从此接口获取名称
                latest_nav=float(latest["单位净值"]) if "单位净值" in df.columns else 0.0,
                latest_date=str(latest["净值日期"]) if "净值日期" in df.columns else "",
            )
    except Exception as e:
        logger.warning(f"[{code}] 备用基本信息接口也失败: {e}")
    return FundInfo(code=code)


def fetch_fund_nav_history(code: str, days: int = 30) -> FundHistory:
    """获取历史净值并计算技术指标"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is None or df.empty:
            raise ValueError("返回空数据")

        # 已知列名: ['净值日期', '单位净值', '日增长率']
        date_col = "净值日期" if "净值日期" in df.columns else df.columns[0]
        nav_col = "单位净值" if "单位净值" in df.columns else df.columns[1]

        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col]).sort_values(date_col)
        df[nav_col] = pd.to_numeric(df[nav_col], errors="coerce")
        df = df.dropna(subset=[nav_col])

        navs = df[nav_col].tolist()
        dates = df[date_col].dt.strftime("%Y-%m-%d").tolist()

        if len(navs) < 2:
            raise ValueError("净值数据不足")

        # 计算均线
        series = pd.Series(navs)
        ma5 = float(series.rolling(5).mean().iloc[-1]) if len(navs) >= 5 else None
        ma10 = float(series.rolling(10).mean().iloc[-1]) if len(navs) >= 10 else None
        ma20 = float(series.rolling(20).mean().iloc[-1]) if len(navs) >= 20 else None

        return FundHistory(
            code=code,
            dates=dates[-days:],
            navs=navs[-days:],
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            max_drawdown_pct=_calc_max_drawdown(navs[-90:] if len(navs) >= 90 else navs),
            ret_7d=_calc_return(navs, 7),
            ret_30d=_calc_return(navs, 30),
            ret_90d=_calc_return(navs, 90),
            trend_signal=_trend_signal(ma5, ma10, ma20),
        )

    except Exception as e:
        logger.error(f"[{code}] 获取历史净值失败: {e}")
        return FundHistory(code=code)


def fetch_fund_top_holdings(code: str) -> List[dict]:
    """获取基金前十大持仓（季报，可能有3个月延迟）"""
    try:
        import akshare as ak
        df = ak.fund_portfolio_hold_em(symbol=code, date="2024")
        if df is None or df.empty:
            return []
        holdings = []
        for _, row in df.head(10).iterrows():
            holdings.append({
                "name": str(row.get("股票名称", row.get("名称", ""))),
                "code": str(row.get("股票代码", row.get("代码", ""))),
                "ratio": float(row.get("占净值比例", row.get("比例", 0)) or 0),
            })
        return holdings
    except Exception as e:
        logger.warning(f"[{code}] 获取持仓数据失败: {e}")
        return []


def fetch_latest_nav(code: str, info: FundInfo) -> FundInfo:
    """补充最新净值和涨跌幅到 FundInfo"""
    try:
        import akshare as ak
        # 已知列名: ['净值日期', '单位净值', '日增长率']
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is None or df.empty:
            return info

        df = df.sort_values("净值日期", ascending=True)
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None

        info.latest_nav = float(latest["单位净值"]) if "单位净值" in df.columns else 0.0
        info.latest_date = str(latest["净值日期"]) if "净值日期" in df.columns else ""

        # 日增长率 已在数据中
        if "日增长率" in df.columns and latest["日增长率"] is not None:
            info.nav_change_pct = float(latest["日增长率"] or 0)
        elif prev is not None:
            prev_nav = float(prev["单位净值"])
            info.nav_change_pct = (
                (info.latest_nav - prev_nav) / prev_nav * 100 if prev_nav else 0.0
            )

        return info
    except Exception as e:
        logger.warning(f"[{code}] 补充最新净值失败: {e}")
        return info


def fetch_fund_data(code: str, report_days: int = 30) -> FundAnalysisData:
    """主入口：聚合获取单基金完整数据"""
    logger.info(f"[{code}] 开始获取基金数据...")
    try:
        info = fetch_fund_basic_info(code)
        info = fetch_latest_nav(code, info)
        history = fetch_fund_nav_history(code, days=report_days)
        holdings = fetch_fund_top_holdings(code)
        logger.info(
            f"[{code}] {info.name} | 净值: {info.latest_nav:.4f} "
            f"({info.nav_change_pct:+.2f}%) | 趋势: {history.trend_signal}"
        )
        return FundAnalysisData(info=info, history=history, top_holdings=holdings)
    except Exception as e:
        logger.error(f"[{code}] 数据获取异常: {e}")
        return FundAnalysisData(info=FundInfo(code=code), history=FundHistory(code=code), error=str(e))
