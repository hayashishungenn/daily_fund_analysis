# -*- coding: utf-8 -*-
"""
AI 分析模块
- 优先使用 Gemini，备选 OpenAI 兼容 API
- 无 Key 时降级为规则引擎生成简单建议
- Prompt 按基金类型差异化，注入大盘背景和评级
"""
import logging
import time
from typing import Optional

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
    """将大盘背景数据格式化为 Prompt 文本段"""
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


def _fund_type_guidance(fund_type: str) -> str:
    """根据基金类型返回差异化的分析要点提示"""
    ft = (fund_type or "").lower()
    if "债" in fund_type or "固收" in fund_type or "货币" in fund_type:
        return (
            "【分析重点（债券/货币基金）】请重点关注：\n"
            "- 近期利率走向对净值的影响\n"
            "- 久期风险和信用风险\n"
            "- 7日年化与同类比较\n"
            "- 不适合用均线做多空判断\n"
        )
    if "qdii" in ft or "海外" in fund_type or "美国" in fund_type or "港股" in fund_type:
        return (
            "【分析重点（QDII / 海外基金）】请重点关注：\n"
            "- 美联储/港股政策对持仓的影响\n"
            "- 汇率波动风险（人民币贬值/升值）\n"
            "- 海外市场情绪（纳指/恒指表现）\n"
            "- T+2~3 净值更新滞后问题\n"
        )
    if "指数" in fund_type or "etf" in ft:
        return (
            "【分析重点（指数/ETF）】请重点关注：\n"
            "- 跟踪误差与溢价率\n"
            "- 成份股行业景气度\n"
            "- 结合均线判断指数阶段性强弱\n"
        )
    # 默认：股票型 / 混合型
    return (
        "【分析重点（股票/混合基金）】请重点关注：\n"
        "- 均线排列与趋势\n"
        "- 近期大盘 Beta 匹配度\n"
        "- 前五大持仓行业集中度\n"
        "- 经理管理年限与历史最佳回报\n"
    )


def _build_prompt(data: FundAnalysisData) -> str:
    info = data.info
    hist = data.history
    market = data.market

    holdings_str = ""
    if data.top_holdings:
        lines = [
            f"  {i+1}. {h['name']}({h['code']}) {h['ratio']:.1f}%"
            for i, h in enumerate(data.top_holdings[:5])
        ]
        holdings_str = "\n前五大持仓:\n" + "\n".join(lines)

    nav_series = ""
    if hist.navs and hist.dates:
        pairs = list(zip(hist.dates, hist.navs))[-10:]
        nav_series = " | ".join(f"{d}: {n:.4f}" for d, n in pairs)

    # 评级文本
    rating_str = ""
    if info.rating_morningstar:
        rating_str += f"晨星评级: {info.rating_morningstar}  "
    if info.rating_zhaos:
        rating_str += f"招商评级: {info.rating_zhaos}"

    # 经理业绩文本
    mgr_str = info.manager
    if info.manager_years > 0:
        mgr_str += f"（从业 {info.manager_years:.1f} 年"
        if info.manager_best_return != 0:
            mgr_str += f"，历史最佳回报 {info.manager_best_return:+.1f}%"
        mgr_str += "）"

    # 同类排名文本
    rank_str = ""
    if info.rank_total > 0:
        rank_str = f"同类排名: {info.rank_in_category}/{info.rank_total}（Top {info.rank_percentile:.0f}%）"

    return f"""{_market_context_text(market)}你是一位专注于国内公募基金的资深分析师，请根据以下数据对该基金进行简短分析并给出操作建议。

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
最大回撤：{hist.max_drawdown_pct:.2f}%
MA5：{_fmt_ma(hist.ma5)}  MA10：{_fmt_ma(hist.ma10)}  MA20：{_fmt_ma(hist.ma20)}
趋势信号：{hist.trend_signal}

【近10日净值】
{nav_series}
{holdings_str}

请严格输出以下格式（不要添加其他内容）：
操作建议：<加仓|持有|减仓|观望>
理由：<120字以内，要有具体数据支撑>
风险提示：<40字以内>"""


def _rule_engine_advice(data: FundAnalysisData) -> dict:
    """无 AI Key 时的规则引擎（按基金类型分支）"""
    hist = data.history
    ft = data.info.fund_type

    # 债券/货币基金：主要看收益方向
    if "债" in ft or "货币" in ft or "固收" in ft:
        if hist.ret_30d > 1.0:
            advice, reason = "持有", f"债基近30日收益 {hist.ret_30d:+.2f}%，表现稳健，维持持仓。"
        elif hist.ret_30d < -1.0:
            advice, reason = "观望", f"债基近30日收益 {hist.ret_30d:+.2f}%，利率上行压力，建议谨慎。"
        else:
            advice, reason = "持有", f"债基近30日收益 {hist.ret_30d:+.2f}%，走势平稳。"
    # 均线策略（股票/混合/指数）
    elif hist.trend_signal == "多头排列" and hist.ret_30d > 0:
        advice = "持有"
        reason = f"均线多头排列（MA5>MA10>MA20），近30日收益 {hist.ret_30d:+.2f}%，趋势向好。"
    elif hist.trend_signal == "空头排列" and hist.max_drawdown_pct < -10:
        advice = "减仓"
        reason = f"均线空头排列，最大回撤 {hist.max_drawdown_pct:.2f}%，下行压力较大，建议减仓观望。"
    elif hist.ret_30d < -5:
        advice = "观望"
        reason = f"近30日收益 {hist.ret_30d:+.2f}%，短期走弱，建议等待企稳信号再介入。"
    elif hist.ret_30d > 5 and hist.trend_signal != "空头排列":
        advice = "加仓"
        reason = f"近30日收益 {hist.ret_30d:+.2f}%，趋势较好，可适量加仓。"
    else:
        advice = "持有"
        reason = f"净值走势平稳，近30日收益 {hist.ret_30d:+.2f}%，维持现有仓位即可。"

    return {"advice": advice, "reason": reason, "risk": "基金投资有风险，过往业绩不代表未来表现。"}


def _parse_ai_response(text: str) -> dict:
    result = {"advice": "观望", "reason": text[:200], "risk": "投资有风险，请谨慎决策。"}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("操作建议：") or line.startswith("操作建议:"):
            val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            for opt in ADVICE_OPTIONS:
                if opt in val:
                    result["advice"] = opt
                    break
        elif line.startswith("理由：") or line.startswith("理由:"):
            result["reason"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("风险提示：") or line.startswith("风险提示:"):
            result["risk"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
    return result


def _get_gemini_model(config: Config):
    """懒加载 Gemini model，全局复用避免每只基金 configure"""
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
            max_tokens=800,   # 提高上限，避免理由被截断
        )
        return _parse_ai_response(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[{data.info.code}] OpenAI 分析失败: {e}")
        return None


def analyze_fund(data: FundAnalysisData, config: Config, request_delay: float = 2.0) -> dict:
    """主分析入口：依次尝试 Gemini -> OpenAI -> 规则引擎"""
    if data.error:
        return {"advice": "观望", "reason": f"数据获取失败: {data.error}", "risk": "暂无数据，请手动核查。"}

    if config.gemini_api_key:
        result = analyze_with_gemini(data, config)
        if result:
            logger.info(f"[{data.info.code}] Gemini 分析完成: {result['advice']}")
            time.sleep(request_delay)
            return result

    if config.openai_api_key:
        result = analyze_with_openai(data, config)
        if result:
            logger.info(f"[{data.info.code}] OpenAI 分析完成: {result['advice']}")
            time.sleep(request_delay)
            return result

    logger.info(f"[{data.info.code}] 使用规则引擎生成建议")
    return _rule_engine_advice(data)
