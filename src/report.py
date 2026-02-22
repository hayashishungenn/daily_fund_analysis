# -*- coding: utf-8 -*-
"""
报告生成模块
- 生成 Markdown 格式的基金每日分析报告（simple / full）
- 报告结构参考: https://github.com/hayashishungenn/daily_stock_analysis
"""
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple

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


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _calc_signal_score(data: FundAnalysisData, analysis: dict) -> int:
    """计算 0-100 信号分，用于排序和快速总览"""
    hist = data.history
    info = data.info
    advice = analysis.get("advice", "观望")

    score = 50.0
    score += {"多头排列": 12, "震荡": 0, "空头排列": -12}.get(hist.trend_signal, 0)
    score += _clamp(hist.ret_30d * 1.2, -20, 20)
    score += _clamp(hist.ret_7d * 0.8, -10, 10)

    if hist.max_drawdown_pct <= -30:
        score -= 25
    elif hist.max_drawdown_pct <= -20:
        score -= 15
    elif hist.max_drawdown_pct <= -10:
        score -= 8

    score += {"加仓": 8, "持有": 2, "观望": -3, "减仓": -8}.get(advice, 0)

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

    if hist.trend_signal == "空头排列":
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
    return issues


def _single_fund_block(data: FundAnalysisData, analysis: dict, signal_score: int, risk_level: str) -> str:
    """生成单只基金详情块（参考 daily_stock_analysis 风格）"""
    info = data.info
    hist = data.history
    advice = analysis.get("advice", "观望")
    reason = analysis.get("reason", "")
    risk = analysis.get("risk", "")

    trend_icon = _TREND_EMOJI.get(hist.trend_signal, "➡️")
    advice_icon = _ADVICE_EMOJI.get(advice, "⚪")

    ma_text = f"MA5 {_fmt_ma(hist.ma5)} | MA10 {_fmt_ma(hist.ma10)} | MA20 {_fmt_ma(hist.ma20)}"

    lines = [
        f"### {advice_icon} {info.name} ({info.code})",
        "",
        (
            f"**操作建议：{advice}** | **信号分：{signal_score}/100** | "
            f"**风险：{_RISK_EMOJI.get(risk_level, risk_level)}** | "
            f"**趋势：{trend_icon} {hist.trend_signal}**"
        ),
        f"",
        f"- 净值：**{info.latest_nav:.4f}** ({_fmt_pct(info.nav_change_pct)}) | 更新：{info.latest_date or 'N/A'}",
        f"- 类型：{info.fund_type} | 经理：{info.manager}",
        f"- 收益：近7日 {_fmt_pct(hist.ret_7d)} | 近30日 {_fmt_pct(hist.ret_30d)} | 近90日 {_fmt_pct(hist.ret_90d)}",
        f"- 最大回撤：{hist.max_drawdown_pct:.2f}% | {ma_text}",
    ]

    if data.top_holdings:
        lines.append("")
        lines.append("**前五大持仓**")
        for i, h in enumerate(data.top_holdings[:5]):
            lines.append(f"- {i + 1}. {h['name']}({h['code']}) {h['ratio']:.1f}%")

    lines += [
        "",
        f"- 操作理由：{reason}",
        f"- 风险提示：{risk}",
        "",
        "---",
    ]

    return "\n".join(lines)


def _prepare_report_context(results: List[Tuple[FundAnalysisData, dict]]) -> dict:
    """预计算报告所需统计与排序信息"""
    advice_count = {k: 0 for k in _ADVICE_EMOJI.keys()}
    ret30_values = []
    enriched = []
    for data, analysis in results:
        adv = analysis.get("advice", "观望")
        advice_count[adv] = advice_count.get(adv, 0) + 1
        ret30_values.append(data.history.ret_30d)
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

    ranked = sorted(enriched, key=lambda x: x["signal_score"], reverse=True)
    high_risk = [x for x in ranked if x["risk_level"] == "高"]
    medium_risk = [x for x in ranked if x["risk_level"] == "中"]
    incomplete = [x for x in ranked if x["issues"]]
    complete_count = len(ranked) - len(incomplete)
    return {
        "advice_count": advice_count,
        "avg_ret30": avg_ret30,
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
        "---",
        "",
        "## 📊 操作建议汇总",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 🟢 建议加仓 | **{advice_count.get('加仓', 0)}** 只 |",
        f"| 🔵 建议持有 | **{advice_count.get('持有', 0)}** 只 |",
        f"| 🔴 建议减仓 | **{advice_count.get('减仓', 0)}** 只 |",
        f"| 🟡 建议观望 | **{advice_count.get('观望', 0)}** 只 |",
        f"| 📈 平均近30日收益 | **{avg_ret30:+.2f}%** |",
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
                f"信号分 {x['signal_score']} | 近30日 {_fmt_pct(d.history.ret_30d)}"
            )
        header.append("")
    if focus_risk:
        has_focus = True
        header.append("风险预警：")
        for x in focus_risk:
            d = x["data"]
            header.append(
                f"- {_RISK_EMOJI.get(x['risk_level'], x['risk_level'])} {d.info.name} ({d.info.code}) | "
                f"近30日 {_fmt_pct(d.history.ret_30d)} | 最大回撤 {d.history.max_drawdown_pct:.2f}%"
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
        "| 基金 | 建议 | 信号分 | 风险 | 近30日 | 最大回撤 | 趋势 |",
        "|------|------|--------|------|--------|----------|------|",
    ]

    for x in ranked:
        d = x["data"]
        advice = x["advice"]
        advice_text = f"{_ADVICE_EMOJI.get(advice, '⚪')}{advice}"
        risk_text = _RISK_EMOJI.get(x["risk_level"], x["risk_level"])
        header.append(
            f"| {d.info.name}({d.info.code}) | {advice_text} | {x['signal_score']} | {risk_text} | "
            f"{_fmt_pct(d.history.ret_30d)} | {d.history.max_drawdown_pct:.2f}% | {d.history.trend_signal} |"
        )

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
                f"近30日 {_fmt_pct(d.history.ret_30d)}"
            )
        lines.append("")

    if focus_risk:
        lines.append("风险预警（偏弱）：")
        for x in focus_risk:
            d = x["data"]
            lines.append(
                f"- 🔴 {d.info.name}({d.info.code}) | 最大回撤 {d.history.max_drawdown_pct:.2f}% | "
                f"近30日 {_fmt_pct(d.history.ret_30d)}"
            )
        lines.append("")

    if not focus_add and not focus_risk:
        lines.append("- 今日无显著强弱分化，建议按既定策略管理仓位。")
        lines.append("")

    lines += [
        "## 🧾 基金清单（按信号分）",
        "",
        "| 基金 | 建议 | 信号分 | 风险 | 近30日 | 趋势 |",
        "|------|------|--------|------|--------|------|",
    ]

    for x in ranked:
        d = x["data"]
        advice_text = f"{_ADVICE_EMOJI.get(x['advice'], '⚪')}{x['advice']}"
        risk_text = _RISK_EMOJI.get(x["risk_level"], x["risk_level"])
        lines.append(
            f"| {d.info.name}({d.info.code}) | {advice_text} | {x['signal_score']} | "
            f"{risk_text} | {_fmt_pct(d.history.ret_30d)} | {d.history.trend_signal} |"
        )

    lines += [
        "",
        "> ⚠️ 本简报仅供快速浏览，不构成投资建议。",
        "> 报告结构参考：https://github.com/hayashishungenn/daily_stock_analysis",
    ]
    return "\n".join(lines)


def generate_report(
    results: List[Tuple[FundAnalysisData, dict]],
    report_days: int = 30,
    report_type: str = "full",
) -> str:
    """
    生成报告（支持 simple / full 两种格式）

    Args:
        results: [(FundAnalysisData, analysis_dict), ...]
        report_days: 分析天数
        report_type: 报告类型，simple / full

    Returns:
        Markdown 字符串
    """
    now = datetime.now(TZ_CN)
    rt = (report_type or "full").strip().lower()
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
