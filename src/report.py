# -*- coding: utf-8 -*-
"""
报告生成模块
- 生成 Markdown 格式的基金每日分析报告（summary / simple / full）
- 报告结构参考: https://github.com/hayashishungenn/daily_stock_analysis
"""
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, List, Tuple

from src.fund_data import FundAnalysisData

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

_ADVICE_EMOJI = {
    "加仓": "🟢",
    "持有": "🔵",
    "减仓": "🔴",
    "观望": "🟡",
}

_TREND_EMOJI = {
    "多头排列": "📈",
    "空头排列": "📉",
    "震荡": "➡️",
}

_ADVICE_ORDER = ("加仓", "持有", "观望", "减仓")
_RISK_EMOJI = {"低": "🟢低", "中": "🟠中", "高": "🔴高"}


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_ma(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.4f}"


def _fmt_value(v: Any, digits: int = 2, default: str = "N/A") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return default


def _fmt_lookback_returns(hist) -> str:
    return (
        f"30天 {_fmt_pct(hist.ret_30d)} | "
        f"90天 {_fmt_pct(hist.ret_90d)} | "
        f"180天 {_fmt_pct(hist.ret_180d)} | "
        f"1年 {_fmt_pct(hist.ret_1y)} | "
        f"3年 {_fmt_pct(hist.ret_3y)}"
    )


def _char_width(ch: str) -> int:
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def _display_width(text: str) -> int:
    return sum(_char_width(ch) for ch in str(text))


def _truncate_display(text: str, max_width: int) -> str:
    s = str(text)
    if _display_width(s) <= max_width:
        return s
    ellipsis = "..."
    target = max(1, max_width - len(ellipsis))
    acc = []
    width = 0
    for ch in s:
        ch_w = _char_width(ch)
        if width + ch_w > target:
            break
        acc.append(ch)
        width += ch_w
    return "".join(acc) + ellipsis


def _pad_display(text: str, width: int, align: str = "left") -> str:
    s = _truncate_display(text, width)
    pad = max(0, width - _display_width(s))
    if align == "right":
        return " " * pad + s
    return s + " " * pad


def _build_text_table(headers: List[str], rows: List[List[str]], aligns: List[str], widths: List[int]) -> str:
    header_line = " | ".join(_pad_display(h, w, "left") for h, w in zip(headers, widths))
    separator_line = "-+-".join("-" * w for w in widths)
    body_lines = [
        " | ".join(_pad_display(cell, w, a) for cell, w, a in zip(row, widths, aligns))
        for row in rows
    ]
    return "```text\n" + "\n".join([header_line, separator_line] + body_lines) + "\n```"


def _build_overview_table(ranked: List[dict], include_drawdown: bool) -> str:
    headers = ["基金", "建议", "分数", "风险", "30天", "90天", "180天", "1年", "3年"]
    aligns = ["left", "left", "right", "left", "right", "right", "right", "right", "right"]
    widths = [26, 6, 4, 5, 8, 8, 8, 8, 8]

    if include_drawdown:
        headers += ["回撤", "趋势"]
        aligns += ["right", "left"]
        widths += [8, 8]
    else:
        headers += ["趋势"]
        aligns += ["left"]
        widths += [8]

    rows: List[List[str]] = []
    for x in ranked:
        d = x["data"]
        row = [
            f"{d.info.name}({d.info.code})",
            x["advice"],
            str(x["signal_score"]),
            x["risk_level"],
            _fmt_pct(d.history.ret_30d),
            _fmt_pct(d.history.ret_90d),
            _fmt_pct(d.history.ret_180d),
            _fmt_pct(d.history.ret_1y),
            _fmt_pct(d.history.ret_3y),
        ]
        if include_drawdown:
            row += [f"{d.history.max_drawdown_pct:.2f}%", d.history.trend_signal]
        else:
            row += [d.history.trend_signal]
        rows.append(row)
    return _build_text_table(headers, rows, aligns, widths)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _calc_signal_score(data: FundAnalysisData, analysis: dict) -> int:
    """计算 0-100 信号分，用于排序和快速总览"""
    ai_score = analysis.get("score")
    if ai_score is not None:
        if isinstance(ai_score, (int, float)):
            return int(round(_clamp(float(ai_score), 0, 100)))
        m = re.search(r"-?\d+", str(ai_score))
        if m:
            return int(round(_clamp(float(m.group()), 0, 100)))

    hist = data.history
    info = data.info
    advice = analysis.get("advice", "观望")

    score = 50.0
    score += {"多头排列": 12, "震荡": 0, "空头排列": -12}.get(hist.trend_signal, 0)
    score += _clamp(hist.ret_30d * 1.2, -20, 20)
    score += _clamp(hist.ret_90d * 0.5, -10, 10)
    score += _clamp(hist.ret_180d * 0.18, -8, 8)
    score += _clamp(hist.ret_1y * 0.10, -8, 8)
    score += _clamp(hist.ret_3y * 0.04, -6, 6)
    score += _clamp(hist.ret_7d * 0.8, -10, 10)

    if hist.max_drawdown_pct <= -30:
        score -= 25
    elif hist.max_drawdown_pct <= -20:
        score -= 15
    elif hist.max_drawdown_pct <= -10:
        score -= 8

    # AI 建议权重提升至 ±15（原为 ±8）
    score += {"加仓": 15, "持有": 3, "观望": -5, "减仓": -15}.get(advice, 0)

    # 晨星高评级加分
    ms = info.rating_morningstar
    if "5" in ms or "五" in ms:
        score += 5
    elif "4" in ms or "四" in ms:
        score += 2

    # 同类排名前20%加分
    if 0 < info.rank_percentile <= 20:
        score += 5
    elif info.rank_percentile > 80:
        score -= 5

    if info.name == "未知" or info.latest_nav <= 0:
        score -= 10

    return int(round(_clamp(score, 0, 100)))


def _risk_level(data: FundAnalysisData) -> str:
    """根据回撤、趋势和收益给出低/中/高风险标签"""
    hist = data.history
    info = data.info

    risk_points = 0
    if hist.max_drawdown_pct <= -20:
        risk_points += 2
    elif hist.max_drawdown_pct <= -10:
        risk_points += 1

    if hist.ret_30d < -8:
        risk_points += 2
    elif hist.ret_30d < -3:
        risk_points += 1
    if hist.ret_90d < -12:
        risk_points += 1
    if hist.ret_1y < -15:
        risk_points += 1

    if hist.trend_signal == "空头排列":
        risk_points += 1

    if hist.volatility_30d >= 35:
        risk_points += 1
    if hist.downside_volatility_30d >= 20:
        risk_points += 1
    if hist.backtest_samples >= 20 and hist.backtest_direction_accuracy_pct <= 45:
        risk_points += 1
    if hist.backtest_recent_consistency == "走弱":
        risk_points += 1

    if info.name == "未知" or info.latest_nav <= 0:
        risk_points += 1

    if risk_points >= 4:
        return "高"
    if risk_points >= 2:
        return "中"
    return "低"


def _data_quality_issues(data: FundAnalysisData) -> List[str]:
    issues: List[str] = []
    if data.info.name == "未知":
        issues.append("名称")
    if data.info.latest_nav <= 0:
        issues.append("净值")
    if not data.info.latest_date:
        issues.append("净值日期")
    if len(data.history.navs) < 5:
        issues.append("历史净值")
    if not data.top_stock_holdings and not data.top_bond_holdings and not data.top_fund_holdings:
        issues.append("持仓披露")
    return issues


def _append_holdings(lines: List[str], title: str, holdings: List[dict], limit: int = 3) -> None:
    if not holdings:
        lines.append(f"- {title}：暂无披露")
        return
    lines.append(f"- {title}（Top{min(limit, len(holdings))}）：")
    for i, h in enumerate(holdings[:limit], 1):
        lines.append(f"  - {i}. {h.get('name', '未知')}({h.get('code', '')}) {float(h.get('ratio', 0.0)):.1f}%")


def _single_fund_block(data: FundAnalysisData, analysis: dict, signal_score: int, risk_level: str) -> str:
    """生成单只基金详情块（参考 daily_stock_analysis 风格）"""
    info = data.info
    hist = data.history
    advice = analysis.get("advice", "观望")
    confidence = analysis.get("confidence", "中")
    portfolio_review = analysis.get("portfolio_review", "")
    backtest_review = analysis.get("backtest_review", "")
    factors = analysis.get("factors", []) or []
    risk_items = analysis.get("risk_items", []) or []
    reason = analysis.get("reason", "")
    risk = analysis.get("risk", "")

    trend_icon = _TREND_EMOJI.get(hist.trend_signal, "➡️")
    advice_icon = _ADVICE_EMOJI.get(advice, "⚪")
    ma_text = f"MA5 {_fmt_ma(hist.ma5)} | MA10 {_fmt_ma(hist.ma10)} | MA20 {_fmt_ma(hist.ma20)} | MA60 {_fmt_ma(hist.ma60)}"

    # 经理信息行
    mgr_text = info.manager
    if info.manager_years > 0:
        mgr_text += f"（从业 {info.manager_years:.1f}年"
        if info.manager_best_return != 0:
            mgr_text += f"，最佳回报 {info.manager_best_return:+.1f}%"
        mgr_text += "）"

    # 评级行
    rating_parts = []
    if info.rating_morningstar:
        rating_parts.append(f"晨星 {info.rating_morningstar}")
    if info.rating_zhaos:
        rating_parts.append(f"招商 {info.rating_zhaos}")
    rating_text = "  |  ".join(rating_parts) if rating_parts else ""

    # 同类排名
    rank_text = ""
    if info.rank_total > 0:
        rank_text = f"同类排名 {info.rank_in_category}/{info.rank_total}（Top {info.rank_percentile:.0f}%）"

    lines = [
        f"### {advice_icon} {info.name} ({info.code})",
        (
            f"**操作建议：{advice}** | **信号分：{signal_score}/100** | "
            f"**置信度：{confidence}** | "
            f"**风险：{_RISK_EMOJI.get(risk_level, risk_level)}** | "
            f"**趋势：{trend_icon} {hist.trend_signal}**"
        ),
        "",
        f"- 净值：**{info.latest_nav:.4f}** ({_fmt_pct(info.nav_change_pct)}) | 更新：{info.latest_date or 'N/A'}",
        f"- 类型：{info.fund_type} | 经理：{mgr_text}",
    ]
    if rating_text:
        lines.append(f"- 评级：{rating_text}")
    if rank_text:
        lines.append(f"- {rank_text}")
    lines += [
        f"- 收益：近7日 {_fmt_pct(hist.ret_7d)} | {_fmt_lookback_returns(hist)}",
        f"- 最大回撤：{hist.max_drawdown_pct:.2f}% | {ma_text}",
        (
            f"- 深度指标：趋势强度 {hist.trend_strength}/100 | 波动 {hist.volatility_30d:.2f}% | "
            f"下行波动 {hist.downside_volatility_30d:.2f}% | 夏普 {hist.sharpe_30d:.2f}"
        ),
        (
            f"- 动量与择时：10点动量 {_fmt_pct(hist.momentum_10d)} | RSI14 {_fmt_value(hist.rsi14, 1)} | "
            f"MACD(DIF/DEA/HIST) {_fmt_value(hist.macd_dif, 4)}/{_fmt_value(hist.macd_dea, 4)}/{_fmt_value(hist.macd_hist, 4)}"
        ),
        (
            f"- 回测验证：样本 {hist.backtest_samples} | 方向准确率 {_fmt_value(hist.backtest_direction_accuracy_pct, 1)}% | "
            f"看多胜率 {_fmt_value(hist.backtest_bullish_win_rate_pct, 1)}% | "
            f"看空胜率 {_fmt_value(hist.backtest_bearish_win_rate_pct, 1)}% | "
            f"平均前瞻收益 {_fmt_value(hist.backtest_avg_forward_return_pct, 2)}% | "
            f"近期一致性 {hist.backtest_recent_consistency}"
        ),
        (
            f"- 资产暴露（前十大披露）：股票 {data.stock_exposure_pct:.1f}% | 债券 {data.bond_exposure_pct:.1f}% | "
            f"基金 {data.fund_exposure_pct:.1f}% | 其他 {data.other_exposure_pct:.1f}%"
        ),
    ]

    lines.append("")
    lines.append("**三类持仓审查（股票 / 债券 / 基金）**")
    _append_holdings(lines, "股票持仓", data.top_stock_holdings, limit=3)
    _append_holdings(lines, "债券持仓", data.top_bond_holdings, limit=3)
    _append_holdings(lines, "基金持仓", data.top_fund_holdings, limit=3)

    lines += [
        f"- 持仓结论：{portfolio_review or '持仓结构无异常结论'}",
        f"- 回测结论：{backtest_review or '回测结论暂不可用'}",
    ]
    if factors:
        lines.append(f"- 正向因子：{'；'.join(factors[:3])}")
    if risk_items:
        lines.append(f"- 关键风险：{'；'.join(risk_items[:3])}")

    lines += [
        f"- 操作理由：{reason}",
        f"- 风险提示：{risk}",
        "---",
    ]

    return "\n".join(lines)


def _prepare_report_context(results: List[Tuple[FundAnalysisData, dict]]) -> dict:
    """预计算报告所需统计与排序信息"""
    advice_count = {k: 0 for k in _ADVICE_EMOJI.keys()}
    ret30_values = []
    ret90_values = []
    ret180_values = []
    ret1y_values = []
    ret3y_values = []
    enriched = []
    for data, analysis in results:
        adv = analysis.get("advice", "观望")
        advice_count[adv] = advice_count.get(adv, 0) + 1
        ret30_values.append(data.history.ret_30d)
        ret90_values.append(data.history.ret_90d)
        ret180_values.append(data.history.ret_180d)
        ret1y_values.append(data.history.ret_1y)
        ret3y_values.append(data.history.ret_3y)
        enriched.append(
            {
                "data": data,
                "analysis": analysis,
                "advice": adv,
                "signal_score": _calc_signal_score(data, analysis),
                "risk_level": _risk_level(data),
                "issues": _data_quality_issues(data),
            }
        )
    avg_ret30 = sum(ret30_values) / len(ret30_values) if ret30_values else 0.0
    avg_ret90 = sum(ret90_values) / len(ret90_values) if ret90_values else 0.0
    avg_ret180 = sum(ret180_values) / len(ret180_values) if ret180_values else 0.0
    avg_ret1y = sum(ret1y_values) / len(ret1y_values) if ret1y_values else 0.0
    avg_ret3y = sum(ret3y_values) / len(ret3y_values) if ret3y_values else 0.0

    ranked = sorted(enriched, key=lambda x: x["signal_score"], reverse=True)
    high_risk = [x for x in ranked if x["risk_level"] == "高"]
    medium_risk = [x for x in ranked if x["risk_level"] == "中"]
    incomplete = [x for x in ranked if x["issues"]]
    complete_count = len(ranked) - len(incomplete)
    return {
        "advice_count": advice_count,
        "avg_ret30": avg_ret30,
        "avg_ret90": avg_ret90,
        "avg_ret180": avg_ret180,
        "avg_ret1y": avg_ret1y,
        "avg_ret3y": avg_ret3y,
        "ranked": ranked,
        "high_risk": high_risk,
        "medium_risk": medium_risk,
        "incomplete": incomplete,
        "complete_count": complete_count,
    }


def _generate_full_report(
    results: List[Tuple[FundAnalysisData, dict]],
    report_days: int,
    now: datetime,
) -> str:
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    ctx = _prepare_report_context(results)
    advice_count = ctx["advice_count"]
    avg_ret30 = ctx["avg_ret30"]
    avg_ret90 = ctx["avg_ret90"]
    avg_ret180 = ctx["avg_ret180"]
    avg_ret1y = ctx["avg_ret1y"]
    avg_ret3y = ctx["avg_ret3y"]
    ranked = ctx["ranked"]
    high_risk = ctx["high_risk"]
    medium_risk = ctx["medium_risk"]
    incomplete = ctx["incomplete"]
    complete_count = ctx["complete_count"]

    header = [
        f"# 📅 {date_str} 基金智能分析报告",
        "",
        f"> 共分析 **{len(results)}** 只基金 | 回看周期：{report_days} 天 | 报告生成时间：{time_str}",
        "",
    ]

    # 大盘背景区块（有数据才显示）
    if results and results[0][0].market and results[0][0].market.sh_close:
        m = results[0][0].market
        def _idx(v, c): return f"{c:.2f}（{v:+.2f}%）"
        header += [
            "## 🌐 大盘背景",
            "",
            f"| 指数 | 最新 | 涨跌 |",
            f"|------|------|------|",
        ]
        if m.sh_close:
            header.append(f"| 🇨🇳 上证指数 | {m.sh_close:.2f} | {m.sh_change:+.2f}% |")
        if m.hs_close:
            header.append(f"| 🇭🇰 恒生指数 | {m.hs_close:.2f} | {m.hs_change:+.2f}% |")
        if m.ndx_close:
            header.append(f"| 🇺🇸 纳斯达克 | {m.ndx_close:.2f} | {m.ndx_change:+.2f}% |")
        if m.cny_usd:
            header.append(f"| 💱 人民币/美元 | {m.cny_usd:.4f} | — |")
        header += ["", "---", ""]
    else:
        header += ["---", ""]

    header += [
        "## 📊 操作建议汇总",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 🟢 建议加仓 | **{advice_count.get('加仓', 0)}** 只 |",
        f"| 🔵 建议持有 | **{advice_count.get('持有', 0)}** 只 |",
        f"| 🔴 建议减仓 | **{advice_count.get('减仓', 0)}** 只 |",
        f"| 🟡 建议观望 | **{advice_count.get('观望', 0)}** 只 |",
        f"| 📈 平均近30日收益 | **{avg_ret30:+.2f}%** |",
        f"| 📈 平均近90日收益 | **{avg_ret90:+.2f}%** |",
        f"| 📈 平均近180日收益 | **{avg_ret180:+.2f}%** |",
        f"| 📈 平均近1年收益 | **{avg_ret1y:+.2f}%** |",
        f"| 📈 平均近3年收益 | **{avg_ret3y:+.2f}%** |",
        "",
        "---",
        "",
        "## 🧪 数据质量",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| ✅ 数据完整基金 | **{complete_count}** 只 |",
        f"| ⚠️ 数据缺失基金 | **{len(incomplete)}** 只 |",
        "",
    ]

    if incomplete:
        header.append("缺失明细：")
        for x in incomplete:
            code = x["data"].info.code
            name = x["data"].info.name
            issue_text = "、".join(x["issues"])
            header.append(f"- {name} ({code})：{issue_text}")
        header += ["", "---", ""]
    else:
        header += ["---", ""]

    # 重点关注：加仓优先 + 高风险预警
    focus_add = [x for x in ranked if x["advice"] == "加仓"][:5]
    focus_risk = sorted(high_risk + medium_risk, key=lambda x: x["signal_score"])[:5]

    header += [
        "## 🎯 今日关注",
        "",
    ]
    has_focus = False
    if focus_add:
        has_focus = True
        header.append("可优先跟踪：")
        for x in focus_add:
            d = x["data"]
            header.append(
                f"- {_ADVICE_EMOJI.get(x['advice'], '⚪')} {d.info.name} ({d.info.code}) | "
                f"信号分 {x['signal_score']} | 30天 {_fmt_pct(d.history.ret_30d)} | 1年 {_fmt_pct(d.history.ret_1y)}"
            )
        header.append("")
    if focus_risk:
        has_focus = True
        header.append("风险预警：")
        for x in focus_risk:
            d = x["data"]
            header.append(
                f"- {_RISK_EMOJI.get(x['risk_level'], x['risk_level'])} {d.info.name} ({d.info.code}) | "
                f"30天 {_fmt_pct(d.history.ret_30d)} | 1年 {_fmt_pct(d.history.ret_1y)} | 最大回撤 {d.history.max_drawdown_pct:.2f}%"
            )
        header.append("")
    if not has_focus:
        header.append("- 今日无高优先级关注项，可按“快速总览”跟踪信号分变化。")
        header.append("")

    header += [
        "---",
        "",
        "## 🧭 快速总览",
        "",
    ]
    header.append(_build_overview_table(ranked, include_drawdown=True))

    header += [
        "",
        "---",
        "",
        "## 📈 基金详细分析",
        "",
    ]

    blocks = []
    for advice in _ADVICE_ORDER:
        group = [x for x in ranked if x["advice"] == advice]
        if not group:
            continue
        blocks.append(f"### {_ADVICE_EMOJI.get(advice, '⚪')} {advice}（{len(group)}只）")
        blocks.append("")
        for x in group:
            blocks.append(
                _single_fund_block(
                    x["data"],
                    x["analysis"],
                    signal_score=x["signal_score"],
                    risk_level=x["risk_level"],
                )
            )

    footer = [
        "",
        "> ⚠️ 本报告由程序自动生成，仅供参考，不构成投资建议。",
        "> 数据来源：东方财富（akshare），AI 分析：Gemini / OpenAI",
        "> 报告结构参考：https://github.com/hayashishungenn/daily_stock_analysis",
    ]

    return "\n".join(header + blocks + footer)


def _generate_simple_report(
    results: List[Tuple[FundAnalysisData, dict]],
    report_days: int,
    now: datetime,
) -> str:
    """生成精简报告（参考 daily_stock_analysis 的 simple 模式）"""
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    ctx = _prepare_report_context(results)
    advice_count = ctx["advice_count"]
    avg_ret30 = ctx["avg_ret30"]
    avg_ret90 = ctx["avg_ret90"]
    avg_ret180 = ctx["avg_ret180"]
    avg_ret1y = ctx["avg_ret1y"]
    avg_ret3y = ctx["avg_ret3y"]
    ranked = ctx["ranked"]
    high_risk = ctx["high_risk"]

    focus_add = [x for x in ranked if x["advice"] == "加仓"][:3]
    focus_risk = sorted(high_risk, key=lambda x: x["signal_score"])[:3]

    lines = [
        f"# 📅 {date_str} 基金分析简报",
        "",
        f"> 模式：**simple** | 共分析 **{len(results)}** 只基金 | 回看周期：{report_days} 天 | 生成时间：{time_str}",
        "",
        "## 📊 建议汇总",
        "",
        f"- 🟢 加仓：**{advice_count.get('加仓', 0)}** 只",
        f"- 🔵 持有：**{advice_count.get('持有', 0)}** 只",
        f"- 🔴 减仓：**{advice_count.get('减仓', 0)}** 只",
        f"- 🟡 观望：**{advice_count.get('观望', 0)}** 只",
        f"- 📈 平均近30日收益：**{avg_ret30:+.2f}%**",
        f"- 📈 平均近90日收益：**{avg_ret90:+.2f}%**",
        f"- 📈 平均近180日收益：**{avg_ret180:+.2f}%**",
        f"- 📈 平均近1年收益：**{avg_ret1y:+.2f}%**",
        f"- 📈 平均近3年收益：**{avg_ret3y:+.2f}%**",
        "",
        "## 🎯 今日重点",
        "",
    ]

    if focus_add:
        lines.append("优先跟踪（偏强）：")
        for x in focus_add:
            d = x["data"]
            lines.append(
                f"- 🟢 {d.info.name}({d.info.code}) | 信号分 {x['signal_score']} | "
                f"30天 {_fmt_pct(d.history.ret_30d)} | 1年 {_fmt_pct(d.history.ret_1y)}"
            )
        lines.append("")

    if focus_risk:
        lines.append("风险预警（偏弱）：")
        for x in focus_risk:
            d = x["data"]
            lines.append(
                f"- 🔴 {d.info.name}({d.info.code}) | 最大回撤 {d.history.max_drawdown_pct:.2f}% | "
                f"30天 {_fmt_pct(d.history.ret_30d)} | 1年 {_fmt_pct(d.history.ret_1y)}"
            )
        lines.append("")

    if not focus_add and not focus_risk:
        lines.append("- 今日无显著强弱分化，建议按既定策略管理仓位。")
        lines.append("")

    lines += [
        "## 🧾 基金清单（按信号分）",
        "",
    ]
    lines.append(_build_overview_table(ranked, include_drawdown=False))

    lines += [
        "",
        "> ⚠️ 本简报仅供快速浏览，不构成投资建议。",
        "> 报告结构参考：https://github.com/hayashishungenn/daily_stock_analysis",
    ]
    return "\n".join(lines)


def _generate_summary_report(
    results: List[Tuple[FundAnalysisData, dict]],
    report_days: int,
    now: datetime,
) -> str:
    """生成仅汇总报告（summary 模式，不含逐只基金详情）"""
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    ctx = _prepare_report_context(results)
    advice_count = ctx["advice_count"]
    avg_ret30 = ctx["avg_ret30"]
    avg_ret90 = ctx["avg_ret90"]
    avg_ret180 = ctx["avg_ret180"]
    avg_ret1y = ctx["avg_ret1y"]
    avg_ret3y = ctx["avg_ret3y"]
    ranked = ctx["ranked"]
    high_risk = ctx["high_risk"]

    top_focus = ranked[:5]
    risk_focus = sorted(high_risk, key=lambda x: x["signal_score"])[:5]

    lines = [
        f"# 📅 {date_str} 基金分析汇总",
        "",
        f"> 模式：**summary** | 共分析 **{len(results)}** 只基金 | 回看周期：{report_days} 天 | 生成时间：{time_str}",
        "",
        "## 📊 建议总览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 🟢 加仓 | **{advice_count.get('加仓', 0)}** 只 |",
        f"| 🔵 持有 | **{advice_count.get('持有', 0)}** 只 |",
        f"| 🔴 减仓 | **{advice_count.get('减仓', 0)}** 只 |",
        f"| 🟡 观望 | **{advice_count.get('观望', 0)}** 只 |",
        f"| 📈 平均近30日收益 | **{avg_ret30:+.2f}%** |",
        f"| 📈 平均近90日收益 | **{avg_ret90:+.2f}%** |",
        f"| 📈 平均近180日收益 | **{avg_ret180:+.2f}%** |",
        f"| 📈 平均近1年收益 | **{avg_ret1y:+.2f}%** |",
        f"| 📈 平均近3年收益 | **{avg_ret3y:+.2f}%** |",
        "",
        "## 🎯 优先关注（Top 5）",
        "",
    ]

    if top_focus:
        for x in top_focus:
            d = x["data"]
            lines.append(
                f"- {_ADVICE_EMOJI.get(x['advice'], '⚪')} {d.info.name} ({d.info.code}) | "
                f"信号分 {x['signal_score']} | 30天 {_fmt_pct(d.history.ret_30d)} | 1年 {_fmt_pct(d.history.ret_1y)}"
            )
    else:
        lines.append("- 今日暂无可用数据。")

    lines += [
        "",
        "## ⚠️ 风险预警（Top 5）",
        "",
    ]

    if risk_focus:
        for x in risk_focus:
            d = x["data"]
            lines.append(
                f"- {_RISK_EMOJI.get(x['risk_level'], x['risk_level'])} {d.info.name} ({d.info.code}) | "
                f"最大回撤 {d.history.max_drawdown_pct:.2f}% | 30天 {_fmt_pct(d.history.ret_30d)} | 1年 {_fmt_pct(d.history.ret_1y)}"
            )
    else:
        lines.append("- 今日无高风险基金。")

    lines += [
        "",
        "## 🧾 汇总清单（按信号分）",
        "",
    ]
    lines.append(_build_overview_table(ranked, include_drawdown=False))

    lines += [
        "",
        "> ⚠️ 本汇总仅供参考，不构成投资建议。",
        "> 报告结构参考：https://github.com/hayashishungenn/daily_stock_analysis",
    ]
    return "\n".join(lines)


def generate_report(
    results: List[Tuple[FundAnalysisData, dict]],
    report_days: int = 30,
    report_type: str = "full",
) -> str:
    """
    生成报告（支持 summary / simple / full 三种格式）

    Args:
        results: [(FundAnalysisData, analysis_dict), ...]
        report_days: 分析天数
        report_type: 报告类型，summary / simple / full

    Returns:
        Markdown 字符串
    """
    now = datetime.now(TZ_CN)
    rt = (report_type or "full").strip().lower()
    if rt == "summary":
        return _generate_summary_report(results, report_days, now)
    if rt == "simple":
        return _generate_simple_report(results, report_days, now)
    return _generate_full_report(results, report_days, now)


def save_report(content: str, report_dir: str = "./reports") -> str:
    """保存报告到文件，返回文件路径"""
    path = Path(report_dir)
    path.mkdir(parents=True, exist_ok=True)
    filename = path / f"fund_report_{datetime.now(TZ_CN).strftime('%Y%m%d_%H%M')}.md"
    filename.write_text(content, encoding="utf-8")
    logger.info(f"报告已保存: {filename}")
    return str(filename)


def split_message(text: str, max_len: int = 4000) -> List[str]:
    """将长 Markdown 文本按块分割，用于 Telegram 等有长度限制的渠道"""
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len:
            parts.append(current.strip())
            current = line
        else:
            current += line
    if current.strip():
        parts.append(current.strip())
    return parts
