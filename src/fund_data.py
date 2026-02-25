# -*- coding: utf-8 -*-
"""
基金数据获取模块
- 使用 akshare 拉取: 基本信息、历史净值、持仓数据、评级、经理业绩、大盘背景
- 计算技术指标: MA5/MA10/MA20、最大回撤、近期收益率
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
_XQ_UNSUPPORTED_CODES: set = set()

# ─── 近期季报日期（按优先级，最新在前）─────────────────────────────────────────
def _recent_quarter_dates() -> List[str]:
    """返回最近4个季报日期字符串，供持仓接口使用"""
    now = datetime.now()
    quarters = []
    year = now.year
    for y in range(year, year - 2, -1):
        for q in [4, 3, 2, 1]:
            q_end = {1: f"{y}-03-31", 2: f"{y}-06-30", 3: f"{y}-09-30", 4: f"{y}-12-31"}[q]
            if datetime.strptime(q_end, "%Y-%m-%d") <= now:
                quarters.append(str(y))           # akshare 接受年份字符串
    # 去重后返回（同年多个季度都用同一年，akshare 按最新季度返回）
    seen = []
    for y in quarters:
        if y not in seen:
            seen.append(y)
    return seen[:3]


# ─── ETF 场内基金前缀（xq 接口不稳定，直接走东方财富） ──────────────────────────
_ETF_PREFIXES = {"15", "16", "18", "50", "51", "52", "56", "58", "59", "88"}


@dataclass
class FundInfo:
    """基金基本信息 + 最新状态"""
    code: str
    name: str = "未知"
    fund_type: str = "未知"          # 股票型、混合型、债券型等
    manager: str = "未知"
    manager_years: float = 0.0       # 基金经理从业年数
    manager_best_return: float = 0.0 # 经理历史最佳基金回报(%)
    size_billion: float = 0.0        # 基金规模（亿元）
    latest_nav: float = 0.0          # 最新单位净值
    latest_date: str = ""            # 净值日期
    nav_change_pct: float = 0.0      # 今日涨跌幅(%)
    cumulative_nav: float = 0.0      # 累计净值
    # 评级
    rating_morningstar: str = ""     # 晨星评级（如 ★★★★）
    rating_zhaos: str = ""           # 招商证券评级
    # 同类排名
    rank_in_category: int = 0        # 同类排名（序号）
    rank_total: int = 0              # 同类总数
    rank_percentile: float = 0.0     # 百分位（越小越好）


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
    ma60: Optional[float] = None
    max_drawdown_pct: float = 0.0    # 最大回撤(%)
    ret_7d: float = 0.0              # 近7日收益率
    ret_30d: float = 0.0             # 近30日收益率
    ret_90d: float = 0.0             # 近90日收益率
    momentum_10d: float = 0.0        # 近10个净值点动量(%)
    volatility_30d: float = 0.0      # 近30个净值点年化波动率(%)
    downside_volatility_30d: float = 0.0  # 近30个净值点下行年化波动率(%)
    sharpe_30d: float = 0.0          # 近30个净值点年化夏普（无风险利率近似为0）
    rsi14: Optional[float] = None    # RSI(14)
    macd_dif: Optional[float] = None
    macd_dea: Optional[float] = None
    macd_hist: Optional[float] = None
    trend_strength: int = 50         # 0-100，综合趋势强度
    trend_signal: str = "震荡"       # 多头排列/空头排列/震荡
    backtest_samples: int = 0
    backtest_direction_accuracy_pct: float = 0.0
    backtest_bullish_win_rate_pct: float = 0.0
    backtest_bearish_win_rate_pct: float = 0.0
    backtest_avg_forward_return_pct: float = 0.0
    backtest_recent_consistency: str = "未知"


@dataclass
class MarketContext:
    """大盘背景数据（每日报告头部使用）"""
    sh_change: float = 0.0        # 上证指数涨跌(%)
    sh_close: float = 0.0         # 上证收盘
    hs_change: float = 0.0        # 恒生指数涨跌(%)
    hs_close: float = 0.0         # 恒生收盘
    ndx_change: float = 0.0       # 纳斯达克涨跌(%)
    ndx_close: float = 0.0        # 纳斯达克收盘
    cny_usd: float = 0.0          # 人民币/美元汇率
    date: str = ""


@dataclass
class FundAnalysisData:
    """完整分析数据包，传给分析器"""
    info: FundInfo
    history: FundHistory
    top_holdings: List[dict] = field(default_factory=list)   # 兼容字段（等同股票持仓）
    top_stock_holdings: List[dict] = field(default_factory=list)  # 前十大股票持仓
    top_bond_holdings: List[dict] = field(default_factory=list)   # 前十大债券持仓
    top_fund_holdings: List[dict] = field(default_factory=list)   # 前十大基金持仓（FOF/母基金）
    stock_exposure_pct: float = 0.0
    bond_exposure_pct: float = 0.0
    fund_exposure_pct: float = 0.0
    other_exposure_pct: float = 0.0
    market: Optional[MarketContext] = None                    # 大盘背景
    error: Optional[str] = None                               # 获取失败时填入错误信息


# ─────────────────────────────────────────────────────────────────────────────
# 内部工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _calc_max_drawdown(navs: List[float]) -> float:
    if len(navs) < 2:
        return 0.0
    arr = np.array(navs, dtype=float)
    peak = np.maximum.accumulate(arr)
    drawdowns = (arr - peak) / np.where(peak == 0, 1, peak)
    return float(drawdowns.min() * 100)


def _calc_return(navs: List[float], n_points: int) -> float:
    """计算近 n_points 个数据点的收益率(%)，不依赖自然日"""
    if len(navs) >= n_points + 1:
        start = navs[-(n_points + 1)]
        end = navs[-1]
        return (end - start) / start * 100 if start else 0.0
    elif len(navs) >= 2:
        return (navs[-1] - navs[0]) / navs[0] * 100 if navs[0] else 0.0
    return 0.0


def _trend_signal(ma5: Optional[float], ma10: Optional[float], ma20: Optional[float]) -> str:
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            return "多头排列"
        if ma5 < ma10 < ma20:
            return "空头排列"
    return "震荡"


def _calc_rsi(navs: List[float], period: int = 14) -> Optional[float]:
    """RSI(14)，返回最后一个值。"""
    if len(navs) < period + 1:
        return None
    series = pd.Series(navs, dtype=float)
    delta = series.diff()
    gains = delta.where(delta > 0, 0.0)
    losses = -delta.where(delta < 0, 0.0)
    avg_gain = gains.rolling(period).mean().iloc[-1]
    avg_loss = losses.rolling(period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi)


def _calc_macd(
    navs: List[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """MACD(DIF/DEA/HIST)，返回最后一个值。"""
    if len(navs) < slow + signal:
        return None, None, None
    series = pd.Series(navs, dtype=float)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return float(dif.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])


def _calc_annualized_volatility_and_sharpe(
    navs: List[float], window_points: int = 30
) -> tuple[float, float]:
    """计算年化波动率(%)和夏普比率（无风险利率近似为0）。"""
    if len(navs) < 3:
        return 0.0, 0.0
    rets = pd.Series(navs, dtype=float).pct_change().dropna()
    if rets.empty:
        return 0.0, 0.0
    window = rets.iloc[-window_points:] if len(rets) > window_points else rets
    std = float(window.std())
    if std <= 0:
        return 0.0, 0.0
    ann_vol = std * np.sqrt(252)
    ann_ret = float(window.mean()) * 252
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    return ann_vol * 100, sharpe


def _calc_downside_volatility(navs: List[float], window_points: int = 30) -> float:
    """计算下行年化波动率(%)。"""
    if len(navs) < 3:
        return 0.0
    rets = pd.Series(navs, dtype=float).pct_change().dropna()
    if rets.empty:
        return 0.0
    window = rets.iloc[-window_points:] if len(rets) > window_points else rets
    downside = window[window < 0]
    if downside.empty:
        return 0.0
    return float(downside.std() * np.sqrt(252) * 100)


def _calc_trend_strength(
    ma5: Optional[float],
    ma10: Optional[float],
    ma20: Optional[float],
    ret_30d: float,
    ret_90d: float,
    max_drawdown_pct: float,
    rsi14: Optional[float],
    macd_hist: Optional[float],
) -> int:
    """综合趋势强度评分(0-100)。"""
    score = 50.0

    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            score += 18
        elif ma5 < ma10 < ma20:
            score -= 18
        elif ma5 > ma10:
            score += 6
        elif ma5 < ma10:
            score -= 6

    score += max(-15.0, min(15.0, ret_30d * 1.0))
    score += max(-10.0, min(10.0, ret_90d * 0.5))

    if max_drawdown_pct <= -20:
        score -= 12
    elif max_drawdown_pct <= -10:
        score -= 6

    if rsi14 is not None:
        if 45 <= rsi14 <= 65:
            score += 4
        elif rsi14 >= 75 or rsi14 <= 25:
            score -= 4

    if macd_hist is not None:
        if macd_hist > 0:
            score += 4
        elif macd_hist < 0:
            score -= 4

    return int(round(max(0.0, min(100.0, score))))


def _infer_direction_from_past_navs(past_navs: List[float]) -> str:
    """Infer expected direction from historical NAVs only: up/down/flat."""
    if len(past_navs) < 20:
        return "flat"

    series = pd.Series(past_navs, dtype=float)
    ma5 = float(series.rolling(5).mean().iloc[-1])
    ma10 = float(series.rolling(10).mean().iloc[-1])
    ma20 = float(series.rolling(20).mean().iloc[-1])
    ret_20 = _calc_return(past_navs, 20)
    rsi14 = _calc_rsi(past_navs, period=14)
    _, _, macd_hist = _calc_macd(past_navs)

    score = 50.0
    if ma5 > ma10 > ma20:
        score += 12
    elif ma5 < ma10 < ma20:
        score -= 12
    score += max(-8.0, min(8.0, ret_20 * 0.8))
    if rsi14 is not None:
        if 42 <= rsi14 <= 68:
            score += 2
        elif rsi14 >= 78:
            score -= 3
        elif rsi14 <= 25:
            score -= 2
    if macd_hist is not None:
        score += 3 if macd_hist > 0 else -3 if macd_hist < 0 else 0

    if score >= 60:
        return "up"
    if score <= 40:
        return "down"
    return "flat"


def _is_direction_correct(signal: str, forward_ret: float, neutral_band_pct: float) -> bool:
    band = abs(float(neutral_band_pct))
    if signal == "up":
        return forward_ret >= band
    if signal == "down":
        return forward_ret <= -band
    return abs(forward_ret) <= band


def _calc_signal_backtest_metrics(
    navs: List[float],
    forward_points: int = 10,
    min_train_points: int = 60,
    neutral_band_pct: float = 1.5,
) -> Dict[str, float]:
    """
    Rolling walk-forward signal backtest on NAV series.

    For each split point t:
    - build a signal using navs[:t+1]
    - evaluate direction using navs[t+forward_points]
    """
    if len(navs) < min_train_points + forward_points + 1:
        return {
            "samples": 0,
            "direction_accuracy_pct": 0.0,
            "bullish_win_rate_pct": 0.0,
            "bearish_win_rate_pct": 0.0,
            "avg_forward_return_pct": 0.0,
            "recent_consistency": "未知",
        }

    signals: List[str] = []
    correctness: List[int] = []
    forward_returns: List[float] = []

    bull_total = 0
    bull_win = 0
    bear_total = 0
    bear_win = 0

    end_idx = len(navs) - forward_points
    for idx in range(min_train_points - 1, end_idx):
        past = navs[: idx + 1]
        start = past[-1]
        future = navs[idx + forward_points]
        if not start:
            continue
        forward_ret = (future - start) / start * 100
        signal = _infer_direction_from_past_navs(past)
        correct = _is_direction_correct(signal, forward_ret, neutral_band_pct)

        signals.append(signal)
        correctness.append(1 if correct else 0)
        forward_returns.append(forward_ret)

        if signal == "up":
            bull_total += 1
            if correct:
                bull_win += 1
        elif signal == "down":
            bear_total += 1
            if correct:
                bear_win += 1

    samples = len(correctness)
    if samples == 0:
        return {
            "samples": 0,
            "direction_accuracy_pct": 0.0,
            "bullish_win_rate_pct": 0.0,
            "bearish_win_rate_pct": 0.0,
            "avg_forward_return_pct": 0.0,
            "recent_consistency": "未知",
        }

    overall_acc = sum(correctness) / samples * 100
    bullish_win = (bull_win / bull_total * 100) if bull_total else 0.0
    bearish_win = (bear_win / bear_total * 100) if bear_total else 0.0
    avg_forward = float(sum(forward_returns) / len(forward_returns)) if forward_returns else 0.0

    tail_n = min(20, samples)
    recent_acc = sum(correctness[-tail_n:]) / tail_n * 100
    if recent_acc >= overall_acc + 8:
        consistency = "改善"
    elif recent_acc <= overall_acc - 8:
        consistency = "走弱"
    else:
        consistency = "稳定"

    return {
        "samples": samples,
        "direction_accuracy_pct": round(overall_acc, 2),
        "bullish_win_rate_pct": round(bullish_win, 2),
        "bearish_win_rate_pct": round(bearish_win, 2),
        "avg_forward_return_pct": round(avg_forward, 2),
        "recent_consistency": consistency,
    }


def _to_float(v, default: float = 0.0) -> float:
    """安全转 float，支持 '9.6%' 格式"""
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except Exception:
        return default


def _should_skip_xq(code: str) -> bool:
    code = (code or "").strip()
    if code in _XQ_UNSUPPORTED_CODES:
        return True
    if len(code) == 6 and code[:2] in _ETF_PREFIXES:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 全局评级缓存（当天只拉一次）
# ─────────────────────────────────────────────────────────────────────────────
_rating_cache: Optional[Dict[str, dict]] = None

def _get_rating_cache() -> Dict[str, dict]:
    global _rating_cache
    if _rating_cache is not None:
        return _rating_cache
    result: Dict[str, dict] = {}
    try:
        import akshare as ak
        df = ak.fund_rating_all()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("基金代码", "")).strip().zfill(6)
                result[code] = {
                    "morningstar": str(row.get("晨星评级", "") or ""),
                    "zhaos": str(row.get("招商证券评级", "") or ""),
                }
        logger.info(f"基金评级缓存加载完成，共 {len(result)} 条")
    except Exception as e:
        logger.warning(f"基金评级数据获取失败: {e}")
    _rating_cache = result
    return result


# 全局经理业绩缓存
_manager_cache: Optional[Dict[str, dict]] = None

def _get_manager_cache() -> Dict[str, dict]:
    global _manager_cache
    if _manager_cache is not None:
        return _manager_cache
    result: Dict[str, dict] = {}
    try:
        import akshare as ak
        df = ak.fund_manager_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = str(row.get("姓名", "") or "").strip()
                if not name:
                    continue
                years_raw = str(row.get("从业时间", "0") or "0")
                try:
                    years = float(years_raw)
                except Exception:
                    years = 0.0
                best_return = _to_float(row.get("最佳基金回报", 0))
                result[name] = {"years": years, "best_return": best_return}
        logger.info(f"基金经理缓存加载完成，共 {len(result)} 条")
    except Exception as e:
        logger.warning(f"基金经理数据获取失败: {e}")
    _manager_cache = result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 大盘背景
# ─────────────────────────────────────────────────────────────────────────────

def fetch_market_context() -> MarketContext:
    """获取大盘一日背景数据（上证、恒生、纳指、汇率）"""
    ctx = MarketContext()
    try:
        import akshare as ak
        # 上证指数
        try:
            df_sh = ak.stock_zh_index_daily_em(symbol="sh000001")
            if df_sh is not None and not df_sh.empty:
                df_sh = df_sh.sort_values("date")
                last = df_sh.iloc[-1]
                prev = df_sh.iloc[-2] if len(df_sh) > 1 else None
                ctx.sh_close = _to_float(last.get("close", 0))
                ctx.date = str(last.get("date", ""))[:10]
                if prev is not None:
                    p = _to_float(prev.get("close", 1) or 1)
                    ctx.sh_change = (ctx.sh_close - p) / p * 100 if p else 0.0
        except Exception as e:
            logger.debug(f"上证指数获取失败: {e}")

        # 恒生指数
        try:
            df_hs = ak.stock_zh_index_daily_em(symbol="hk0HSI")
            if df_hs is not None and not df_hs.empty:
                df_hs = df_hs.sort_values("date")
                last = df_hs.iloc[-1]
                prev = df_hs.iloc[-2] if len(df_hs) > 1 else None
                ctx.hs_close = _to_float(last.get("close", 0))
                if prev is not None:
                    p = _to_float(prev.get("close", 1) or 1)
                    ctx.hs_change = (ctx.hs_close - p) / p * 100 if p else 0.0
        except Exception as e:
            logger.debug(f"恒生指数获取失败: {e}")

        # 纳斯达克（IXIC）
        try:
            df_ndx = ak.stock_zh_index_daily_em(symbol="us.IXIC")
            if df_ndx is not None and not df_ndx.empty:
                df_ndx = df_ndx.sort_values("date")
                last = df_ndx.iloc[-1]
                prev = df_ndx.iloc[-2] if len(df_ndx) > 1 else None
                ctx.ndx_close = _to_float(last.get("close", 0))
                if prev is not None:
                    p = _to_float(prev.get("close", 1) or 1)
                    ctx.ndx_change = (ctx.ndx_close - p) / p * 100 if p else 0.0
        except Exception as e:
            logger.debug(f"纳指获取失败: {e}")

        # 人民币汇率
        try:
            df_fx = ak.currency_boc_safe()
            if df_fx is not None and not df_fx.empty:
                usd_row = df_fx[df_fx["货币"] == "美元"]
                if not usd_row.empty:
                    ctx.cny_usd = _to_float(usd_row.iloc[0].get("现汇买入价", 0))
        except Exception as e:
            logger.debug(f"汇率获取失败: {e}")

    except Exception as e:
        logger.warning(f"大盘背景数据获取异常: {e}")
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 基金数据获取
# ─────────────────────────────────────────────────────────────────────────────

def fetch_fund_basic_info(code: str) -> FundInfo:
    """获取基金基本信息（含评级 + 经理业绩）"""
    if _should_skip_xq(code):
        logger.info(f"[{code}] 跳过 xq 接口，使用东方财富接口")
        return _fetch_basic_info_fallback(code)
    try:
        import akshare as ak
        df = ak.fund_individual_basic_info_xq(symbol=code)
        info_dict: Dict[str, object] = {}
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                key = str(row.iloc[0]).strip()
                val = row.iloc[1]
                info_dict[key] = val
        name = str(info_dict.get("基金名称", info_dict.get("名称", "未知")))
        manager_name = str(info_dict.get("基金经理", info_dict.get("经理", "未知")))
        info = FundInfo(
            code=code,
            name=name,
            fund_type=str(info_dict.get("基金类型", info_dict.get("类型", "未知"))),
            manager=manager_name,
            size_billion=_to_float(info_dict.get("基金规模", 0)),
        )
        _enrich_manager_info(info, manager_name)
        _enrich_rating(info, code)
        return info
    except KeyError as e:
        if str(e).strip("'\"") == "data":
            _XQ_UNSUPPORTED_CODES.add(code)
            logger.info(f"[{code}] xq 返回缺少 data 字段，已切换为东方财富接口")
        else:
            logger.warning(f"[{code}] xq 接口返回异常: {e}")
        return _fetch_basic_info_fallback(code)
    except Exception as e:
        logger.warning(f"[{code}] 获取基金基本信息失败（xq）: {e}，尝试备用接口...")
        return _fetch_basic_info_fallback(code)


def _enrich_manager_info(info: FundInfo, manager_name: str) -> None:
    """补充经理从业年限与历史最佳回报"""
    try:
        cache = _get_manager_cache()
        mgr = cache.get(manager_name)
        if mgr:
            info.manager_years = mgr.get("years", 0.0)
            info.manager_best_return = mgr.get("best_return", 0.0)
    except Exception:
        pass


def _enrich_rating(info: FundInfo, code: str) -> None:
    """补充晨星和招商证券评级"""
    try:
        cache = _get_rating_cache()
        padded = code.strip().zfill(6)
        rating = cache.get(padded) or cache.get(code.strip())
        if rating:
            info.rating_morningstar = rating.get("morningstar", "")
            info.rating_zhaos = rating.get("zhaos", "")
    except Exception:
        pass


def _fetch_basic_info_fallback(code: str) -> FundInfo:
    """备用接口获取基金基本信息（东方财富净值走势 + 名称接口）"""
    info = FundInfo(code=code)
    try:
        import akshare as ak
        # 尝试从基金排名接口获取名称
        try:
            df_rank = ak.fund_open_fund_daily_em()
            if df_rank is not None and not df_rank.empty:
                row = df_rank[df_rank["基金代码"] == code]
                if not row.empty:
                    info.name = str(row.iloc[0].get("基金简称", code))
                    info.fund_type = str(row.iloc[0].get("类型", "未知"))
        except Exception:
            pass
        # 净值数据
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                info.latest_nav = _to_float(latest.get("单位净值", 0))
                info.latest_date = str(latest.get("净值日期", ""))[:10]
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[{code}] 备用基本信息接口失败: {e}")
    # 无论如何都尝试补充评级
    _enrich_rating(info, code)
    return info


def fetch_fund_nav_history(
    code: str,
    days: int = 30,
    backtest_enabled: bool = True,
    backtest_forward_points: int = 10,
    backtest_min_train_points: int = 60,
    backtest_neutral_band_pct: float = 1.5,
) -> FundHistory:
    """获取历史净值并计算技术指标（同时补充 latest_nav 避免重复请求）"""
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

        series = pd.Series(navs)
        ma5 = float(series.rolling(5).mean().iloc[-1]) if len(navs) >= 5 else None
        ma10 = float(series.rolling(10).mean().iloc[-1]) if len(navs) >= 10 else None
        ma20 = float(series.rolling(20).mean().iloc[-1]) if len(navs) >= 20 else None
        ma60 = float(series.rolling(60).mean().iloc[-1]) if len(navs) >= 60 else None
        rsi14 = _calc_rsi(navs, period=14)
        macd_dif, macd_dea, macd_hist = _calc_macd(navs)
        volatility_30d, sharpe_30d = _calc_annualized_volatility_and_sharpe(navs, window_points=30)
        downside_volatility_30d = _calc_downside_volatility(navs, window_points=30)
        momentum_10d = _calc_return(navs, 10)
        max_drawdown_pct = _calc_max_drawdown(navs[-90:] if len(navs) >= 90 else navs)
        ret_7d = _calc_return(navs, 7)
        ret_30d = _calc_return(navs, 22)    # 约22个交易日 ≈ 1个月
        ret_90d = _calc_return(navs, 65)    # 约65个交易日 ≈ 3个月
        trend_signal = _trend_signal(ma5, ma10, ma20)
        trend_strength = _calc_trend_strength(
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ret_30d=ret_30d,
            ret_90d=ret_90d,
            max_drawdown_pct=max_drawdown_pct,
            rsi14=rsi14,
            macd_hist=macd_hist,
        )

        if backtest_enabled:
            bt = _calc_signal_backtest_metrics(
                navs=navs,
                forward_points=backtest_forward_points,
                min_train_points=backtest_min_train_points,
                neutral_band_pct=backtest_neutral_band_pct,
            )
        else:
            bt = {
                "samples": 0,
                "direction_accuracy_pct": 0.0,
                "bullish_win_rate_pct": 0.0,
                "bearish_win_rate_pct": 0.0,
                "avg_forward_return_pct": 0.0,
                "recent_consistency": "关闭",
            }

        # 用数据点数量而非自然日计算收益（更准确，不受节假日影响）
        return FundHistory(
            code=code,
            dates=dates[-days:],
            navs=navs[-days:],
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma60,
            max_drawdown_pct=max_drawdown_pct,
            ret_7d=ret_7d,
            ret_30d=ret_30d,
            ret_90d=ret_90d,
            momentum_10d=momentum_10d,
            volatility_30d=volatility_30d,
            downside_volatility_30d=downside_volatility_30d,
            sharpe_30d=sharpe_30d,
            rsi14=rsi14,
            macd_dif=macd_dif,
            macd_dea=macd_dea,
            macd_hist=macd_hist,
            trend_strength=trend_strength,
            trend_signal=trend_signal,
            backtest_samples=int(bt["samples"]),
            backtest_direction_accuracy_pct=float(bt["direction_accuracy_pct"]),
            backtest_bullish_win_rate_pct=float(bt["bullish_win_rate_pct"]),
            backtest_bearish_win_rate_pct=float(bt["bearish_win_rate_pct"]),
            backtest_avg_forward_return_pct=float(bt["avg_forward_return_pct"]),
            backtest_recent_consistency=str(bt["recent_consistency"]),
        )

    except Exception as e:
        logger.error(f"[{code}] 获取历史净值失败: {e}")
        return FundHistory(code=code)


def _normalize_holdings(
    df: pd.DataFrame,
    name_keys: List[str],
    code_keys: List[str],
    ratio_keys: List[str],
    limit: int = 10,
) -> List[dict]:
    """统一转换持仓列表结构。"""
    if df is None or df.empty:
        return []
    holdings: List[dict] = []
    for _, row in df.head(limit).iterrows():
        name = ""
        sec_code = ""
        ratio_raw: Any = 0
        for key in name_keys:
            if key in df.columns:
                name = str(row.get(key, "") or "").strip()
                if name:
                    break
        for key in code_keys:
            if key in df.columns:
                sec_code = str(row.get(key, "") or "").strip()
                if sec_code:
                    break
        for key in ratio_keys:
            if key in df.columns:
                ratio_raw = row.get(key, 0)
                break
        if not name and not sec_code:
            continue
        holdings.append({
            "name": name or sec_code,
            "code": sec_code,
            "ratio": _to_float(ratio_raw),
        })
    return holdings


def _call_portfolio_api(func, code: str, year: str) -> Optional[pd.DataFrame]:
    """兼容不同 AkShare 接口参数签名。"""
    candidate_kwargs = [
        {"symbol": code, "date": year},
        {"symbol": code},
        {"code": code, "date": year},
        {"code": code},
    ]
    for kwargs in candidate_kwargs:
        try:
            return func(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    return None


def fetch_fund_stock_holdings(code: str) -> List[dict]:
    """获取基金前十大股票持仓（自动检索最近季报）。"""
    import akshare as ak
    for year in _recent_quarter_dates():
        try:
            df = ak.fund_portfolio_hold_em(symbol=code, date=year)
            holdings = _normalize_holdings(
                df=df,
                name_keys=["股票名称", "名称", "证券名称"],
                code_keys=["股票代码", "代码", "证券代码"],
                ratio_keys=["占净值比例", "比例", "占基金净值比"],
                limit=10,
            )
            if holdings:
                return holdings
        except Exception as e:
            logger.debug(f"[{code}] 股票持仓 date={year} 失败: {e}")
    return []


def fetch_fund_bond_holdings(code: str) -> List[dict]:
    """获取基金前十大债券持仓（若接口不可用则返回空）。"""
    import akshare as ak
    api_candidates = [
        "fund_portfolio_bond_hold_em",
        "fund_portfolio_bond_hold_xq",
    ]
    for year in _recent_quarter_dates():
        for api_name in api_candidates:
            if not hasattr(ak, api_name):
                continue
            try:
                df = _call_portfolio_api(getattr(ak, api_name), code, year)
                holdings = _normalize_holdings(
                    df=df,
                    name_keys=["债券名称", "名称", "证券名称"],
                    code_keys=["债券代码", "代码", "证券代码"],
                    ratio_keys=["占净值比例", "比例", "占基金净值比"],
                    limit=10,
                )
                if holdings:
                    return holdings
            except Exception as e:
                logger.debug(f"[{code}] 债券持仓 {api_name} date={year} 失败: {e}")
    return []


def fetch_fund_fund_holdings(code: str) -> List[dict]:
    """获取基金前十大基金持仓（FOF/母基金，若接口不可用则返回空）。"""
    import akshare as ak
    api_candidates = [
        "fund_portfolio_fund_hold_em",
        "fund_portfolio_fof_hold_em",
    ]
    for year in _recent_quarter_dates():
        for api_name in api_candidates:
            if not hasattr(ak, api_name):
                continue
            try:
                df = _call_portfolio_api(getattr(ak, api_name), code, year)
                holdings = _normalize_holdings(
                    df=df,
                    name_keys=["基金名称", "名称", "证券名称"],
                    code_keys=["基金代码", "代码", "证券代码"],
                    ratio_keys=["占净值比例", "比例", "占基金净值比"],
                    limit=10,
                )
                if holdings:
                    return holdings
            except Exception as e:
                logger.debug(f"[{code}] 基金持仓 {api_name} date={year} 失败: {e}")
    return []


def fetch_fund_top_holdings(code: str) -> List[dict]:
    """兼容旧调用：返回股票持仓。"""
    return fetch_fund_stock_holdings(code)


def _sum_exposure(holdings: List[dict]) -> float:
    """累计持仓占比（%）。"""
    return round(sum(max(0.0, float(h.get("ratio", 0.0))) for h in holdings), 2)


def fetch_latest_nav(code: str, info: FundInfo, nav_df: Optional[pd.DataFrame] = None) -> FundInfo:
    """补充最新净值和涨跌幅，可复用已拉取的 DataFrame 避免重复请求"""
    try:
        import akshare as ak
        df = nav_df if nav_df is not None else ak.fund_open_fund_info_em(
            symbol=code, indicator="单位净值走势"
        )
        if df is None or df.empty:
            return info

        df = df.sort_values("净值日期", ascending=True)
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None

        info.latest_nav = _to_float(latest.get("单位净值", 0))
        info.latest_date = str(latest.get("净值日期", ""))[:10]

        if "日增长率" in df.columns and latest["日增长率"] is not None:
            info.nav_change_pct = _to_float(latest["日增长率"])
        elif prev is not None:
            prev_nav = _to_float(prev.get("单位净值", 0))
            info.nav_change_pct = (
                (info.latest_nav - prev_nav) / prev_nav * 100 if prev_nav else 0.0
            )
        return info
    except Exception as e:
        logger.warning(f"[{code}] 补充最新净值失败: {e}")
        return info


def fetch_fund_rank(code: str, info: FundInfo) -> FundInfo:
    """获取基金同类排名（近1年）"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_daily_em()
        if df is None or df.empty:
            return info
        # 找到该基金行
        row = df[df["基金代码"] == code]
        if row.empty:
            return info
        row = row.iloc[0]
        rank_raw = str(row.get("近1年排名", "")).strip()
        # 格式通常为 "23/456"
        if "/" in rank_raw:
            parts = rank_raw.split("/")
            try:
                info.rank_in_category = int(parts[0])
                info.rank_total = int(parts[1])
                if info.rank_total > 0:
                    info.rank_percentile = round(info.rank_in_category / info.rank_total * 100, 1)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[{code}] 获取同类排名失败: {e}")
    return info


def fetch_fund_data(
    code: str,
    report_days: int = 30,
    market: Optional[MarketContext] = None,
    backtest_enabled: bool = True,
    backtest_forward_points: int = 10,
    backtest_min_train_points: int = 60,
    backtest_neutral_band_pct: float = 1.5,
) -> FundAnalysisData:
    """主入口：聚合获取单基金完整数据（历史净值只拉一次，复用填充 latest_nav）"""
    logger.info(f"[{code}] 开始获取基金数据...")
    try:
        import akshare as ak
        info = fetch_fund_basic_info(code)

        # 拉一次历史净值，同时用来填充 latest_nav（避免重复请求）
        nav_df: Optional[pd.DataFrame] = None
        try:
            nav_df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        except Exception as e:
            logger.warning(f"[{code}] NAV 接口失败: {e}")

        info = fetch_latest_nav(code, info, nav_df=nav_df)
        history = fetch_fund_nav_history(
            code,
            days=report_days,
            backtest_enabled=backtest_enabled,
            backtest_forward_points=backtest_forward_points,
            backtest_min_train_points=backtest_min_train_points,
            backtest_neutral_band_pct=backtest_neutral_band_pct,
        )
        stock_holdings = fetch_fund_stock_holdings(code)
        bond_holdings = fetch_fund_bond_holdings(code)
        fund_holdings = fetch_fund_fund_holdings(code)
        stock_exposure = _sum_exposure(stock_holdings)
        bond_exposure = _sum_exposure(bond_holdings)
        fund_exposure = _sum_exposure(fund_holdings)
        known_exposure = min(100.0, stock_exposure + bond_exposure + fund_exposure)
        other_exposure = round(max(0.0, 100.0 - known_exposure), 2)
        info = fetch_fund_rank(code, info)

        logger.info(
            f"[{code}] {info.name} | 净值: {info.latest_nav:.4f} "
            f"({info.nav_change_pct:+.2f}%) | 趋势: {history.trend_signal} | "
            f"持仓暴露(股/债/基): {stock_exposure:.1f}%/{bond_exposure:.1f}%/{fund_exposure:.1f}% | "
            f"回测准确率: {history.backtest_direction_accuracy_pct:.1f}%({history.backtest_samples}样本)"
        )
        return FundAnalysisData(
            info=info,
            history=history,
            top_holdings=stock_holdings,
            top_stock_holdings=stock_holdings,
            top_bond_holdings=bond_holdings,
            top_fund_holdings=fund_holdings,
            stock_exposure_pct=stock_exposure,
            bond_exposure_pct=bond_exposure,
            fund_exposure_pct=fund_exposure,
            other_exposure_pct=other_exposure,
            market=market,
        )
    except Exception as e:
        logger.error(f"[{code}] 数据获取异常: {e}")
        return FundAnalysisData(info=FundInfo(code=code), history=FundHistory(code=code), error=str(e))
