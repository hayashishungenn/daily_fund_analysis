# -*- coding: utf-8 -*-
"""
AI 分析模块
- 优先使用 Gemini，备选 OpenAI 兼容 API
- 无 Key 时降级为多因子规则引擎
- Prompt 按基金类型差异化，注入大盘背景、技术因子与三类持仓审查（股票/基金/债券）
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from src.config import Config
from src.fund_data import FundAnalysisData, MarketContext

logger = logging.getLogger(__name__)

ADVICE_OPTIONS = ("加仓", "持有", "减仓", "观望")

_gemini_model_instance = None  # 全局复用，避免每只基金重新 configure


def _fmt_ma(v) -> str:
    try:
        return f"{float(v):.4f}" if v is not None else "N/A"
    except Exception:
        return "N/A"


def _market_context_text(market: Optional[MarketContext]) -> str:
    """将大盘背景数据格式化为 Prompt 文本段。"""
    if market is None:
        return ""
    parts = []
    if market.sh_close:
        parts.append(f"上证指数: {market.sh_close:.2f}（{market.sh_change:+.2f}%）")
    if market.hs_close:
        parts.append(f"恒生指数: {market.hs_close:.2f}（{market.hs_change:+.2f}%）")
    if market.ndx_close:
        parts.append(f"纳斯达克: {market.ndx_close:.2f}（{market.ndx_change:+.2f}%）")
    if market.cny_usd:
        parts.append(f"人民币/美元: {market.cny_usd:.4f}")
    if not parts:
        return ""
    return "【当日大盘背景】\n" + "  | ".join(parts) + "\n\n"


def _fund_type_tags(fund_type: str) -> Dict[str, bool]:
    ft_raw = fund_type or ""
    ft = ft_raw.lower()
    is_bond = any(k in ft_raw for k in ("债", "固收", "货币"))
    is_qdii = "qdii" in ft or any(k in ft_raw for k in ("海外", "美国", "港股"))
    is_index = "指数" in ft_raw or "etf" in ft
    is_fof = "fof" in ft or any(k in ft_raw for k in ("基金中基金", "母基金"))
    is_equity_like = not is_bond
    return {
        "bond": is_bond,
        "qdii": is_qdii,
        "index": is_index,
        "fof": is_fof,
        "equity_like": is_equity_like,
    }


def _fund_type_guidance(fund_type: str) -> str:
    """根据基金类型返回差异化的分析要点提示。"""
    tags = _fund_type_tags(fund_type)
    if tags["bond"]:
        return (
            "【分析重点（债券/货币基金）】请重点关注：\n"
            "- 利率敏感度、信用风险、波动率与回撤控制\n"
            "- 持仓中的债券集中度与期限风险\n"
            "- 股票暴露是否异常偏高\n"
        )
    if tags["fof"]:
        return (
            "【分析重点（FOF）】请重点关注：\n"
            "- 底层基金持仓占比与集中度\n"
            "- 权益/固收风格漂移\n"
            "- 底层基金风格同质化风险\n"
        )
    if tags["qdii"]:
        return (
            "【分析重点（QDII / 海外基金）】请重点关注：\n"
            "- 海外市场与汇率波动对净值的影响\n"
            "- 股票/基金/债券三类资产暴露是否匹配策略\n"
            "- T+2~3 净值滞后对短线判断的影响\n"
        )
    if tags["index"]:
        return (
            "【分析重点（指数/ETF）】请重点关注：\n"
            "- 跟踪标的趋势（均线、动量、MACD）\n"
            "- 持仓集中度与行业偏离\n"
            "- 股票/基金/债券持仓结构是否与指数属性一致\n"
        )
    return (
        "【分析重点（股票/混合基金）】请重点关注：\n"
        "- 均线、动量、RSI、MACD、趋势强度\n"
        "- 波动率、回撤、夏普等风险调整指标\n"
        "- 股票/基金/债券持仓是否支持当前策略\n"
    )


def _fmt_holdings(title: str, holdings: List[dict], limit: int = 5) -> str:
    if not holdings:
        return f"{title}：无披露/暂无数据"
    lines = [f"{title}（Top{min(limit, len(holdings))}）:"]
    for i, h in enumerate(holdings[:limit], 1):
        lines.append(f"  {i}. {h.get('name', '未知')}({h.get('code', '')}) {float(h.get('ratio', 0.0)):.1f}%")
    return "\n".join(lines)


def _fmt_lookback_returns(data: FundAnalysisData) -> str:
    hist = data.history
    return (
        f"30天 {hist.ret_30d:+.2f}% | "
        f"90天 {hist.ret_90d:+.2f}% | "
        f"180天 {hist.ret_180d:+.2f}% | "
        f"1年 {hist.ret_1y:+.2f}% | "
        f"3年 {hist.ret_3y:+.2f}%"
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _normalize_score(v: Any, default: int = 50) -> int:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return int(round(_clamp(float(v), 0, 100)))
    m = re.search(r"-?\d+", str(v))
    if not m:
        return default
    return int(round(_clamp(float(m.group()), 0, 100)))


def _top_ratio(holdings: List[dict], n: int = 3) -> float:
    if not holdings:
        return 0.0
    return sum(max(0.0, float(h.get("ratio", 0.0))) for h in holdings[:n])


def _choose_confidence(score: int, risk_count: int) -> str:
    if score >= 72 and risk_count <= 2:
        return "高"
    if score >= 52 and risk_count <= 4:
        return "中"
    return "低"


def _compose_reason(score: int, factors: List[str], risks: List[str]) -> str:
    parts = [f"多因子评分 {score}/100"]
    if factors:
        parts.extend(factors[:2])
    if risks:
        parts.append(f"需关注：{risks[0]}")
    text = "；".join(parts)
    return text if len(text) <= 120 else text[:117] + "..."


def _compose_risk(risks: List[str]) -> str:
    if not risks:
        return "关注净值波动与申赎节奏，分批交易并设置仓位上限。"
    text = "；".join(risks[:2])
    return text if len(text) <= 60 else text[:57] + "..."


def _multi_factor_score(data: FundAnalysisData) -> dict:
    """多因子规则引擎：将技术面、风险面、三类持仓结构统一打分。"""
    info = data.info
    hist = data.history
    tags = _fund_type_tags(info.fund_type)

    stock_exp = float(data.stock_exposure_pct or 0.0)
    bond_exp = float(data.bond_exposure_pct or 0.0)
    fund_exp = float(data.fund_exposure_pct or 0.0)
    other_exp = float(data.other_exposure_pct or 0.0)

    score = 50.0
    factors: List[str] = []
    risks: List[str] = []
    bt_samples = int(hist.backtest_samples or 0)
    bt_acc = float(hist.backtest_direction_accuracy_pct or 0.0)
    bt_bull = float(hist.backtest_bullish_win_rate_pct or 0.0)
    bt_bear = float(hist.backtest_bearish_win_rate_pct or 0.0)
    bt_avg_ret = float(hist.backtest_avg_forward_return_pct or 0.0)
    bt_consistency = str(hist.backtest_recent_consistency or "未知")

    score += _clamp(hist.ret_30d * 0.9, -12, 12)
    score += _clamp(hist.ret_90d * 0.5, -10, 10)
    score += _clamp(hist.ret_180d * 0.18, -8, 8)
    score += _clamp(hist.ret_1y * 0.10, -8, 8)
    score += _clamp(hist.ret_3y * 0.04, -6, 6)
    score += _clamp(hist.momentum_10d * 0.5, -6, 6)

    trend_score_map = {"多头排列": 12, "震荡": 0, "空头排列": -12}
    score += trend_score_map.get(hist.trend_signal, 0)
    score += _clamp((float(hist.trend_strength) - 50.0) * 0.35, -12, 12)

    if hist.sharpe_30d >= 1.0:
        score += 8
        factors.append(f"夏普 {hist.sharpe_30d:.2f} 较高")
    elif hist.sharpe_30d >= 0.3:
        score += 4
        factors.append(f"夏普 {hist.sharpe_30d:.2f} 正向")
    elif hist.sharpe_30d <= -0.2:
        score -= 6
        risks.append(f"夏普 {hist.sharpe_30d:.2f} 偏弱")

    if tags["bond"]:
        if hist.volatility_30d <= 4:
            score += 10
            factors.append(f"年化波动 {hist.volatility_30d:.1f}% 稳定")
        elif hist.volatility_30d >= 8:
            score -= 10
            risks.append(f"债基波动 {hist.volatility_30d:.1f}% 偏高")
    else:
        if hist.volatility_30d <= 18:
            score += 6
            factors.append(f"年化波动 {hist.volatility_30d:.1f}% 可控")
        elif hist.volatility_30d >= 35:
            score -= 10
            risks.append(f"波动 {hist.volatility_30d:.1f}% 偏高")

    if hist.downside_volatility_30d > 0:
        if hist.downside_volatility_30d >= 22:
            score -= 6
            risks.append(f"下行波动 {hist.downside_volatility_30d:.1f}% 偏高")
        elif hist.downside_volatility_30d <= 10:
            score += 3

    if hist.max_drawdown_pct <= -25:
        score -= 14
        risks.append(f"最大回撤 {hist.max_drawdown_pct:.2f}% 较深")
    elif hist.max_drawdown_pct <= -15:
        score -= 8
        risks.append(f"最大回撤 {hist.max_drawdown_pct:.2f}% 偏大")
    elif hist.max_drawdown_pct >= -6:
        score += 4

    if hist.rsi14 is not None:
        if 40 <= hist.rsi14 <= 65:
            score += 4
            factors.append(f"RSI14={hist.rsi14:.1f} 中性偏强")
        elif hist.rsi14 >= 78:
            score -= 5
            risks.append(f"RSI14={hist.rsi14:.1f} 短期过热")
        elif hist.rsi14 <= 25:
            score -= 3
            risks.append(f"RSI14={hist.rsi14:.1f} 弱势未修复")

    if hist.macd_hist is not None:
        if hist.macd_hist > 0:
            score += 4
            factors.append("MACD 柱线为正")
        elif hist.macd_hist < 0:
            score -= 4
            risks.append("MACD 柱线为负")

    if bt_samples >= 20:
        if bt_acc >= 62:
            score += 10
            factors.append(f"滚动回测方向准确率 {bt_acc:.1f}%（{bt_samples}样本）")
        elif bt_acc >= 55:
            score += 5
            factors.append(f"滚动回测准确率 {bt_acc:.1f}%（{bt_samples}样本）")
        elif bt_acc <= 45:
            score -= 10
            risks.append(f"滚动回测准确率 {bt_acc:.1f}% 偏低（{bt_samples}样本）")
        elif bt_acc <= 50:
            score -= 5
            risks.append(f"滚动回测准确率 {bt_acc:.1f}% 边际偏弱")
        else:
            factors.append(f"滚动回测准确率 {bt_acc:.1f}%（{bt_samples}样本）")

        if bt_consistency == "改善":
            score += 3
            factors.append("回测近期一致性改善")
        elif bt_consistency == "走弱":
            score -= 4
            risks.append("回测近期一致性走弱")

        if bt_bull >= 60:
            score += 2
            factors.append(f"看多信号胜率 {bt_bull:.1f}%")
        elif bt_bull > 0 and bt_bull <= 45 and not tags["bond"]:
            score -= 2
            risks.append(f"看多信号胜率 {bt_bull:.1f}% 偏低")

        if bt_bear >= 60:
            score += 2
            factors.append(f"看空信号胜率 {bt_bear:.1f}%")
        elif bt_bear > 0 and bt_bear <= 45:
            score -= 1

        if bt_avg_ret >= 1.0:
            score += 2
        elif bt_avg_ret <= -1.0:
            score -= 2
            risks.append(f"回测前瞻平均收益 {bt_avg_ret:+.2f}% 偏弱")
    elif bt_samples > 0:
        score -= 2
        risks.append(f"回测样本仅 {bt_samples}，稳定性参考有限")
    else:
        risks.append("回测样本不足，稳定性验证缺失")

    known_exposure = stock_exp + bond_exp + fund_exp
    if known_exposure <= 0:
        risks.append("未获取到有效持仓披露")
        score -= 8
    else:
        factors.append(f"持仓暴露 股{stock_exp:.1f}%/债{bond_exp:.1f}%/基{fund_exp:.1f}%")

    if tags["bond"]:
        if bond_exp >= 40:
            score += 12
            factors.append(f"债券暴露 {bond_exp:.1f}% 匹配债基属性")
        elif bond_exp < 25 and known_exposure > 0:
            score -= 10
            risks.append(f"债券暴露仅 {bond_exp:.1f}% 偏低")
        if stock_exp >= 25:
            score -= 6
            risks.append(f"股票暴露 {stock_exp:.1f}% 偏高")
    elif tags["fof"]:
        if fund_exp >= 35:
            score += 10
            factors.append(f"基金持仓 {fund_exp:.1f}% 匹配FOF属性")
        elif fund_exp < 20 and known_exposure > 0:
            score -= 8
            risks.append(f"基金持仓仅 {fund_exp:.1f}% 偏低")
    else:
        equity_exp = stock_exp + fund_exp
        if equity_exp >= 45:
            score += 8
            factors.append(f"权益暴露 {equity_exp:.1f}% 与进攻策略匹配")
        elif known_exposure > 0 and equity_exp < 20:
            score -= 8
            risks.append(f"权益暴露仅 {equity_exp:.1f}% 偏低")

    stock_top3 = _top_ratio(data.top_stock_holdings, 3)
    bond_top3 = _top_ratio(data.top_bond_holdings, 3)
    fund_top3 = _top_ratio(data.top_fund_holdings, 3)
    if stock_top3 >= 35:
        score -= 5
        risks.append(f"股票前3持仓集中 {stock_top3:.1f}%")
    elif stock_top3 >= 15:
        score += 2

    if bond_top3 >= 55:
        score -= 4
        risks.append(f"债券前3持仓集中 {bond_top3:.1f}%")
    if fund_top3 >= 45:
        score -= 4
        risks.append(f"基金前3持仓集中 {fund_top3:.1f}%")

    if info.rank_total > 0:
        if info.rank_percentile <= 20:
            score += 6
            factors.append(f"同类排名 Top {info.rank_percentile:.1f}%")
        elif info.rank_percentile >= 80:
            score -= 6
            risks.append(f"同类排名 Top {info.rank_percentile:.1f}% 偏后")

    if "5" in (info.rating_morningstar or "") or "五" in (info.rating_morningstar or ""):
        score += 5
    elif "4" in (info.rating_morningstar or "") or "四" in (info.rating_morningstar or ""):
        score += 2

    if info.manager_years >= 6:
        score += 3
    elif 0 < info.manager_years < 1:
        score -= 2
        risks.append(f"经理任职 {info.manager_years:.1f} 年偏短")

    if info.name == "未知" or info.latest_nav <= 0:
        score -= 12
        risks.append("基金基础数据不完整")
    if len(hist.navs) < 20:
        score -= 8
        risks.append("净值样本不足 20 个点")

    score_int = _normalize_score(score, default=50)

    if score_int >= 78:
        advice = "加仓"
    elif score_int >= 60:
        advice = "持有"
    elif score_int >= 45:
        advice = "观望"
    else:
        advice = "减仓"

    hard_risk = hist.max_drawdown_pct <= -20 or (
        hist.trend_signal == "空头排列" and (hist.ret_30d < -5 or hist.ret_90d < -10)
    )
    if hard_risk and advice == "加仓":
        advice = "持有"
    if hard_risk and score_int < 55:
        advice = "减仓"

    confidence = _choose_confidence(score_int, len(risks))
    reason = _compose_reason(score_int, factors, risks)
    risk = _compose_risk(risks)
    portfolio_review = (
        f"持仓审查：股票 {stock_exp:.1f}%，债券 {bond_exp:.1f}%，基金 {fund_exp:.1f}%，"
        f"其他 {other_exp:.1f}%（基于前十大披露）"
    )
    if bt_samples > 0:
        backtest_review = (
            f"回测审查：{bt_samples}样本，方向准确率 {bt_acc:.1f}%，"
            f"看多胜率 {bt_bull:.1f}%，看空胜率 {bt_bear:.1f}%，"
            f"平均前瞻收益 {bt_avg_ret:+.2f}%，近期一致性 {bt_consistency}"
        )
    else:
        backtest_review = "回测审查：样本不足或已关闭，暂无法验证信号稳定性"

    return {
        "advice": advice,
        "score": score_int,
        "confidence": confidence,
        "reason": reason,
        "risk": risk,
        "factors": factors[:5],
        "risk_items": risks[:5],
        "portfolio_review": portfolio_review,
        "backtest_review": backtest_review,
    }


def _build_prompt(data: FundAnalysisData) -> str:
    info = data.info
    hist = data.history
    market = data.market
    baseline = _multi_factor_score(data)

    nav_series = ""
    if hist.navs and hist.dates:
        pairs = list(zip(hist.dates, hist.navs))[-10:]
        nav_series = " | ".join(f"{d}: {n:.4f}" for d, n in pairs)

    rating_str = ""
    if info.rating_morningstar:
        rating_str += f"晨星评级: {info.rating_morningstar}  "
    if info.rating_zhaos:
        rating_str += f"招商评级: {info.rating_zhaos}"

    mgr_str = info.manager
    if info.manager_years > 0:
        mgr_str += f"（从业 {info.manager_years:.1f} 年"
        if info.manager_best_return != 0:
            mgr_str += f"，历史最佳回报 {info.manager_best_return:+.1f}%"
        mgr_str += "）"

    rank_str = ""
    if info.rank_total > 0:
        rank_str = f"同类排名: {info.rank_in_category}/{info.rank_total}（Top {info.rank_percentile:.0f}%）"

    holdings_text = "\n".join(
        [
            _fmt_holdings("股票持仓", data.top_stock_holdings, limit=5),
            _fmt_holdings("债券持仓", data.top_bond_holdings, limit=5),
            _fmt_holdings("基金持仓", data.top_fund_holdings, limit=5),
        ]
    )
    if hist.backtest_samples > 0:
        backtest_text = (
            "【滚动回测（信号稳定性）】\n"
            f"样本数：{hist.backtest_samples}\n"
            f"方向准确率：{hist.backtest_direction_accuracy_pct:.2f}%\n"
            f"看多信号胜率：{hist.backtest_bullish_win_rate_pct:.2f}%\n"
            f"看空信号胜率：{hist.backtest_bearish_win_rate_pct:.2f}%\n"
            f"平均前瞻收益：{hist.backtest_avg_forward_return_pct:+.2f}%\n"
            f"近期一致性：{hist.backtest_recent_consistency}"
        )
    else:
        backtest_text = "【滚动回测（信号稳定性）】\n样本不足或回测关闭，暂无有效统计。"

    return f"""{_market_context_text(market)}你是一位专注于国内公募基金的资深分析师，请根据以下数据对该基金进行深度分析并给出操作建议。

{_fund_type_guidance(info.fund_type)}
【基金信息】
代码：{info.code}
名称：{info.name}
类型：{info.fund_type}
基金经理：{mgr_str}
规模（亿元）：{info.size_billion:.2f}
最新净值：{info.latest_nav:.4f}（{info.latest_date}）
今日涨跌：{info.nav_change_pct:+.2f}%
{f"评级：{rating_str}" if rating_str else ""}
{f"{rank_str}" if rank_str else ""}

【技术指标】
近7日收益：{hist.ret_7d:+.2f}%
近30日收益：{hist.ret_30d:+.2f}%
近90日收益：{hist.ret_90d:+.2f}%
近180日收益：{hist.ret_180d:+.2f}%
近1年收益：{hist.ret_1y:+.2f}%
近3年收益：{hist.ret_3y:+.2f}%
近10点动量：{hist.momentum_10d:+.2f}%
最大回撤：{hist.max_drawdown_pct:.2f}%
年化波动（30点）：{hist.volatility_30d:.2f}%
下行年化波动（30点）：{hist.downside_volatility_30d:.2f}%
夏普（30点）：{hist.sharpe_30d:.2f}
RSI14：{hist.rsi14 if hist.rsi14 is not None else "N/A"}
MACD(DIF/DEA/HIST)：{hist.macd_dif if hist.macd_dif is not None else "N/A"} / {hist.macd_dea if hist.macd_dea is not None else "N/A"} / {hist.macd_hist if hist.macd_hist is not None else "N/A"}
MA5：{_fmt_ma(hist.ma5)}  MA10：{_fmt_ma(hist.ma10)}  MA20：{_fmt_ma(hist.ma20)}  MA60：{_fmt_ma(hist.ma60)}
趋势信号：{hist.trend_signal}  趋势强度：{hist.trend_strength}/100

{backtest_text}

【持仓结构审查（必须覆盖股票/基金/债券三类）】
资产暴露（基于前十大披露）：
- 股票：{data.stock_exposure_pct:.1f}%
- 债券：{data.bond_exposure_pct:.1f}%
- 基金：{data.fund_exposure_pct:.1f}%
- 其他：{data.other_exposure_pct:.1f}%

{holdings_text}

【近10日净值】
{nav_series}

【规则引擎基线】
建议：{baseline["advice"]}
信号分：{baseline["score"]}/100
置信度：{baseline["confidence"]}
重点因子：{"；".join(baseline.get("factors", [])[:3]) if baseline.get("factors") else "无"}
风险因子：{"；".join(baseline.get("risk_items", [])[:3]) if baseline.get("risk_items") else "无"}
回测结论：{baseline.get("backtest_review", "无")}
多周期回看：{_fmt_lookback_returns(data)}

请严格输出以下格式（不要添加其他内容）：
操作建议：<加仓|持有|减仓|观望>
信号分：<0-100整数>
置信度：<高|中|低>
理由：<120字以内，要有具体数据支撑，并明确提及股票/基金/债券持仓审查与回测稳定性结论>
风险提示：<60字以内>"""


def _rule_engine_advice(data: FundAnalysisData) -> dict:
    """无 AI Key 时的多因子规则引擎。"""
    return _multi_factor_score(data)


def _parse_ai_response(text: str) -> dict:
    result: Dict[str, Any] = {
        "advice": "观望",
        "score": None,
        "confidence": None,
        "reason": text[:200],
        "risk": "投资有风险，请谨慎决策。",
    }
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("操作建议：") or line.startswith("操作建议:"):
            val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            for opt in ADVICE_OPTIONS:
                if opt in val:
                    result["advice"] = opt
                    break
        elif line.startswith("信号分：") or line.startswith("信号分:"):
            raw = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            result["score"] = _normalize_score(raw, default=50)
        elif line.startswith("置信度：") or line.startswith("置信度:"):
            val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if "高" in val:
                result["confidence"] = "高"
            elif "低" in val:
                result["confidence"] = "低"
            else:
                result["confidence"] = "中"
        elif line.startswith("理由：") or line.startswith("理由:"):
            result["reason"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("风险提示：") or line.startswith("风险提示:"):
            result["risk"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
    return result


def _finalize_ai_result(ai_result: dict, baseline: dict) -> dict:
    out = dict(ai_result or {})
    advice = out.get("advice")
    if advice not in ADVICE_OPTIONS:
        advice = baseline["advice"]

    score = _normalize_score(out.get("score"), default=baseline["score"])
    confidence = out.get("confidence")
    if confidence not in ("高", "中", "低"):
        confidence = baseline["confidence"]

    reason = (out.get("reason") or "").strip() or baseline["reason"]
    risk = (out.get("risk") or "").strip() or baseline["risk"]

    out.update(
        {
            "advice": advice,
            "score": score,
            "confidence": confidence,
            "reason": reason,
            "risk": risk,
            "factors": baseline.get("factors", []),
            "risk_items": baseline.get("risk_items", []),
            "portfolio_review": baseline.get("portfolio_review", ""),
            "backtest_review": baseline.get("backtest_review", ""),
        }
    )
    return out


def _get_gemini_model(config: Config):
    """懒加载 Gemini model，全局复用避免每只基金 configure。"""
    global _gemini_model_instance
    if _gemini_model_instance is None:
        import google.generativeai as genai

        genai.configure(api_key=config.gemini_api_key)
        _gemini_model_instance = genai.GenerativeModel(config.gemini_model)
    return _gemini_model_instance


def analyze_with_gemini(data: FundAnalysisData, config: Config) -> Optional[dict]:
    try:
        import google.generativeai as genai

        model = _get_gemini_model(config)
        prompt = _build_prompt(data)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(temperature=config.gemini_temperature),
        )
        return _parse_ai_response(response.text)
    except Exception as e:
        logger.warning(f"[{data.info.code}] Gemini 分析失败: {e}")
        return None


def analyze_with_openai(data: FundAnalysisData, config: Config) -> Optional[dict]:
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url or None,
        )
        prompt = _build_prompt(data)
        response = client.chat.completions.create(
            model=config.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.openai_temperature,
            max_tokens=900,
        )
        return _parse_ai_response(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[{data.info.code}] OpenAI 分析失败: {e}")
        return None


def analyze_fund(data: FundAnalysisData, config: Config, request_delay: float = 2.0) -> dict:
    """主分析入口：依次尝试 Gemini -> OpenAI -> 多因子规则引擎。"""
    if data.error:
        return {
            "advice": "观望",
            "score": 35,
            "confidence": "低",
            "reason": f"数据获取失败: {data.error}",
            "risk": "暂无完整数据，请手动核查后再决策。",
            "factors": [],
            "risk_items": ["数据获取失败"],
            "portfolio_review": "持仓审查不可用（数据异常）",
            "backtest_review": "回测审查不可用（数据异常）",
        }

    baseline = _multi_factor_score(data)

    if config.gemini_api_key:
        result = analyze_with_gemini(data, config)
        if result:
            final_result = _finalize_ai_result(result, baseline)
            logger.info(
                f"[{data.info.code}] Gemini 分析完成: {final_result['advice']} "
                f"(score={final_result['score']})"
            )
            time.sleep(request_delay)
            return final_result

    if config.openai_api_key:
        result = analyze_with_openai(data, config)
        if result:
            final_result = _finalize_ai_result(result, baseline)
            logger.info(
                f"[{data.info.code}] OpenAI 分析完成: {final_result['advice']} "
                f"(score={final_result['score']})"
            )
            time.sleep(request_delay)
            return final_result

    logger.info(f"[{data.info.code}] 使用多因子规则引擎生成建议")
    return baseline
