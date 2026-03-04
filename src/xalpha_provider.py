# -*- coding: utf-8 -*-
"""
xalpha 兼容基金数据适配层
- 复用天天基金 / 东方财富公开接口
- 本地保留解析逻辑，避免受 xalpha 当前 pandas 版本约束
"""

from __future__ import annotations

import ast
from io import StringIO
import logging
import re
from typing import Any, Dict, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _normalize_code(code: str) -> str:
    value = (code or "").strip()
    if value.upper().startswith("F") and value[1:].isdigit():
        value = value[1:]
    return value.zfill(6)


def _request_text(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    response = requests.get(url, headers=headers or {})
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = "utf-8"
    return response.text


def _to_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    if value is None:
        return default
    text = str(value).replace("%", "").replace(",", "").replace(" ", "").strip()
    if not text:
        return default
    try:
        return float(text)
    except Exception:
        return default


def _to_event_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    direct = _to_float(text, None)
    if direct is not None:
        return direct

    match = re.search(r"(分红|现金|拆分|折算|分拆)[^0-9-]*(-?\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    amount = _to_float(match.group(2), 0.0) or 0.0
    if match.group(1) in {"拆分", "折算", "分拆"}:
        return -abs(amount)
    return abs(amount)


def _extract_assignment(page_text: str, variable: str) -> Any:
    match = re.search(rf"{re.escape(variable)}\s*=\s*(.*?);", page_text, re.S)
    if not match:
        return None
    payload = match.group(1).strip()
    if not payload:
        return None
    try:
        return ast.literal_eval(payload.replace("null", "None"))
    except Exception:
        logger.debug("xalpha 页面变量解析失败: %s", variable, exc_info=True)
        return None


def _extract_jsonp_payload(text: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"\(\s*(\{[\s\S]*\})\s*\)\s*;?\s*$", text.strip(), re.S)
    if not match:
        return None
    try:
        payload = ast.literal_eval(match.group(1).replace("null", "None"))
    except Exception:
        logger.debug("JSONP 载荷解析失败", exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _extract_date(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [str(col[-1]) for col in df.columns]
        return df
    df = df.copy()
    df.columns = [str(col) for col in df.columns]
    return df


def _find_column(columns: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    for column in columns:
        if any(candidate in column for candidate in candidates):
            return column
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_latest_lsjz_content(content: str) -> Optional[Dict[str, Any]]:
    if not content or "暂无数据" in content:
        return None
    rows = re.findall(r"<tr[\s\S]*?</tr>", content, re.I)
    for row in rows:
        cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, re.I)
        if len(cells) < 2:
            continue
        date_str = _strip_html(cells[0])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            continue
        nav = _to_float(_strip_html(cells[1]), None)
        if nav is None:
            continue
        growth = None
        for cell in cells:
            match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", _strip_html(cell))
            if match:
                growth = _to_float(match.group(1), None)
                break
        cumulative_nav = _to_float(_strip_html(cells[2]) if len(cells) > 2 else None, None)
        return {
            "date": date_str,
            "nav": float(nav),
            "growth": growth,
            "cumulative_nav": cumulative_nav,
        }
    return None


def fetch_fund_valuation(code: str) -> Optional[Dict[str, Any]]:
    code = _normalize_code(code)
    text = _request_text(
        f"http://fundgz.1234567.com.cn/js/{code}.js",
        headers={
            "Host": "fundgz.1234567.com.cn",
            "Referer": "http://fund.eastmoney.com/",
        },
    )
    payload = _extract_jsonp_payload(text)
    if not payload:
        return None
    return {
        "code": str(payload.get("fundcode", "") or code),
        "name": str(payload.get("name", "") or ""),
        "dwjz": _to_float(payload.get("dwjz"), None),
        "gsz": _to_float(payload.get("gsz"), None),
        "gztime": str(payload.get("gztime", "") or ""),
        "jzrq": str(payload.get("jzrq", "") or ""),
        "gszzl": _to_float(payload.get("gszzl"), None),
    }


def fetch_fund_latest_nav_snapshot(code: str) -> Optional[Dict[str, Any]]:
    code = _normalize_code(code)
    text = _request_text(
        f"http://fundf10.eastmoney.com/F10DataApi.aspx?type=lsjz&code={code}&page=1&per=1&sdate=&edate=",
        headers={
            "Host": "fundf10.eastmoney.com",
            "Referer": f"http://fundf10.eastmoney.com/jjjz_{code}.html",
        },
    )
    match = re.search(r'content\s*:\s*(["\'])([\s\S]*?)\1\s*,\s*records', text, re.S)
    if not match:
        return None
    content = match.group(2).replace("\\'", "'").replace('\\"', '"').replace("\\/", "/")
    return _parse_latest_lsjz_content(content)


def fetch_fund_realtime_info(code: str) -> Dict[str, Any]:
    code = _normalize_code(code)
    valuation = None
    latest_snapshot = None
    try:
        valuation = fetch_fund_valuation(code)
    except Exception:
        logger.debug("[%s] 估值接口获取失败", code, exc_info=True)
    try:
        latest_snapshot = fetch_fund_latest_nav_snapshot(code)
    except Exception:
        logger.debug("[%s] 最新净值快照获取失败", code, exc_info=True)

    page_text = ""
    try:
        page_text = _request_text(f"http://fund.eastmoney.com/{code}.html")
    except Exception:
        logger.debug("[%s] 基金详情页获取失败", code, exc_info=True)
    soup = BeautifulSoup(page_text or "", "lxml")

    name = ""
    name_nodes = soup.select("div[style='float: left']")
    if name_nodes:
        name = name_nodes[0].get_text(" ", strip=True).split("(")[0].strip()
    if not name and soup.title:
        name = soup.title.get_text(strip=True).split("基金净值")[0].split("(")[0].strip()
    if not name and valuation:
        name = str(valuation.get("name", "") or "")

    info_cells: Dict[str, str] = {}
    for cell in soup.select("div.infoOfFund > table > tr > td"):
        text = cell.get_text(" ", strip=True).replace("\xa0", " ")
        if "：" not in text:
            continue
        key, value = text.split("：", 1)
        info_cells[key.replace(" ", "").strip()] = value.strip()

    status_nodes = soup.select("span.staticCell")
    status = status_nodes[0].get_text(strip=True) if status_nodes else ""

    current = (
        _to_float((latest_snapshot or {}).get("nav"), None)
        if latest_snapshot
        else _to_float((valuation or {}).get("dwjz"), None)
    )
    current_date = (
        str((latest_snapshot or {}).get("date", "") or "")
        if latest_snapshot
        else str((valuation or {}).get("jzrq", "") or "")
    )
    estimate = _to_float((valuation or {}).get("gsz"), None) if valuation else None
    estimate_time = str((valuation or {}).get("gztime", "") or "") if valuation else ""

    if current is None or not current_date:
        number_rows = soup.find_all("dd", class_="dataNums")
        date_rows = soup.find_all("dt")
        if len(number_rows) > 1 and number_rows[1].find("span", class_="ui-font-large"):
            value_index = 1
            estimate_nodes = soup.select("span[id=gz_gsz]")
            if estimate is None and estimate_nodes:
                estimate_text = estimate_nodes[0].get_text(strip=True)
                if estimate_text and estimate_text != "--":
                    estimate = _to_float(estimate_text, None)
            else:
                value_index = 0

            value_node = number_rows[value_index].find("span", class_="ui-font-large")
            if value_node and current is None:
                current = _to_float(value_node.get_text(strip=True), None)
            if len(date_rows) > value_index and not current_date:
                current_date = _extract_date(date_rows[value_index].get_text(" ", strip=True))
        elif len(number_rows) > 1:
            if current is None:
                current = _to_float(number_rows[1].get_text(" ", strip=True), None)
            if len(date_rows) > 1 and not current_date:
                current_date = _extract_date(date_rows[1].get_text(" ", strip=True))

    return {
        "name": name or code,
        "time": current_date,
        "current": current,
        "status": status,
        "type": info_cells.get("基金类型", info_cells.get("类型", "")),
        "scale": info_cells.get("基金规模", info_cells.get("规模", "")),
        "manager": info_cells.get("基金经理", info_cells.get("经理", "")),
        "company": info_cells.get("管理人", info_cells.get("管理公司", "")),
        "estimate": estimate,
        "estimate_time": estimate_time,
        "daily_change_pct": _to_float((latest_snapshot or {}).get("growth"), None),
        "estimate_change_pct": _to_float((valuation or {}).get("gszzl"), None) if valuation else None,
        "cumulative_nav": _to_float((latest_snapshot or {}).get("cumulative_nav"), None),
    }


def fetch_fund_nav_history(code: str) -> pd.DataFrame:
    code = _normalize_code(code)
    page_text = _request_text(f"http://fund.eastmoney.com/pingzhongdata/{code}.js")
    net_values = _extract_assignment(page_text, "Data_netWorthTrend")
    total_values = _extract_assignment(page_text, "Data_ACWorthTrend")
    if not net_values:
        raise ValueError("xalpha 净值接口返回空数据")

    rows: list[dict[str, Any]] = []
    for item in net_values:
        if not isinstance(item, dict):
            continue
        timestamp_ms = item.get("x")
        net_value = _to_float(item.get("y"), None)
        if timestamp_ms is None or net_value is None:
            continue
        date_value = (
            pd.to_datetime(int(timestamp_ms), unit="ms", utc=True)
            .tz_convert("Asia/Shanghai")
            .tz_localize(None)
        )
        rows.append(
            {
                "date": date_value,
                "netvalue": float(net_value),
                "comment": _to_event_value(item.get("unitMoney")),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("xalpha 净值接口没有可用记录")
    df = df.dropna(subset=["date", "netvalue"]).sort_values("date").reset_index(drop=True)

    if isinstance(total_values, list) and len(total_values) == len(df):
        df["totvalue"] = [
            _to_float(item[1] if isinstance(item, list) and len(item) > 1 else None, None)
            for item in total_values
        ]

    return df


def _fetch_holdings_table(
    code: str,
    year: str = "",
    season: str = "",
    month: str = "",
    category: str = "stock",
) -> Optional[pd.DataFrame]:
    code = _normalize_code(code)
    if not month and season:
        month = str(int(season) * 3)

    if category == "stock":
        endpoint = "jjcc"
    elif category == "bond":
        endpoint = "zqcc"
    else:
        raise ValueError(f"不支持的持仓类型: {category}")

    page_text = _request_text(
        (
            "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
            f"?type={endpoint}&code={code}&topline=10&year={year}&month={month}"
        ),
        headers={
            "Host": "fundf10.eastmoney.com",
            "Referer": f"http://fundf10.eastmoney.com/ccmx_{code}.html",
        },
    )
    match = re.search(r"apidata=\{\s*content:(.*),arryear:", page_text, re.S)
    if not match:
        return None

    soup = BeautifulSoup(match.group(1), "lxml")
    if len(soup.get_text(" ", strip=True)) < 20:
        return None

    tables = soup.find_all("table")
    if not tables:
        return None

    table_index = 0
    if month:
        timeline = [
            node.get_text(strip=True)
            for node in soup.find_all("font", class_="px12")
            if node.get_text(strip=True).startswith("2")
        ]
        for index, label in enumerate(timeline):
            parts = label.split("-")
            if len(parts) >= 2 and parts[1].endswith(str(month)[-1]):
                table_index = index
                break
        else:
            return None

    if table_index >= len(tables):
        return None

    frames = pd.read_html(StringIO(str(tables[table_index])))
    if not frames:
        return None
    return _flatten_columns(frames[0])


def _normalize_holdings_frame(df: Optional[pd.DataFrame], category: str) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None

    columns = [str(col) for col in df.columns]
    if category == "stock":
        code_col = _find_column(columns, ("股票代码", "证券代码", "代码"))
        name_col = _find_column(columns, ("股票名称", "证券名称", "名称"))
        ratio_col = _find_column(columns, ("占净值比例", "占基金净值比", "比例"))
        share_col = _find_column(columns, ("持股数", "持仓数量"))
        value_col = _find_column(columns, ("持仓市值", "市值"))
    else:
        code_col = _find_column(columns, ("债券代码", "证券代码", "代码"))
        name_col = _find_column(columns, ("债券名称", "证券名称", "名称"))
        ratio_col = _find_column(columns, ("占净值比例", "占基金净值比", "比例"))
        share_col = None
        value_col = _find_column(columns, ("持仓市值", "市值"))

    if not code_col or not name_col or not ratio_col:
        return None

    result = pd.DataFrame(
        {
            "code": df[code_col].astype(str).str.strip(),
            "name": df[name_col].astype(str).str.strip(),
            "ratio": df[ratio_col].map(lambda value: _to_float(value, 0.0) or 0.0),
        }
    )
    if share_col:
        result["share"] = df[share_col].map(lambda value: _to_float(value, 0.0) or 0.0)
    if value_col:
        result["value"] = df[value_col].map(lambda value: _to_float(value, 0.0) or 0.0)

    result = result[
        ~(
            result["name"].astype(str).str.contains("合计", na=False)
            | result["code"].astype(str).str.contains("合计", na=False)
        )
    ]
    result = result[(result["name"] != "") | (result["code"] != "")]
    result = result.reset_index(drop=True)
    return result if not result.empty else None


def fetch_fund_stock_holdings(code: str) -> Optional[pd.DataFrame]:
    return _normalize_holdings_frame(_fetch_holdings_table(code, category="stock"), category="stock")


def fetch_fund_bond_holdings(code: str) -> Optional[pd.DataFrame]:
    return _normalize_holdings_frame(_fetch_holdings_table(code, category="bond"), category="bond")


def fetch_fund_portfolio_snapshot(code: str, date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    code = _normalize_code(code)
    page_text = _request_text(f"http://fundf10.eastmoney.com/zcpz_{code}.html")
    soup = BeautifulSoup(page_text, "lxml")
    table = soup.find("table", class_="tzxq")
    if table is None:
        return None

    frames = pd.read_html(StringIO(str(table)))
    if not frames:
        return None

    df = _flatten_columns(frames[0])
    report_col = _find_column(list(df.columns), ("报告期",))
    stock_col = _find_column(list(df.columns), ("股票占净比",))
    bond_col = _find_column(list(df.columns), ("债券占净比",))
    cash_col = _find_column(list(df.columns), ("现金占净比",))
    fund_col = _find_column(list(df.columns), ("基金占净比",))
    assets_col = _find_column(list(df.columns), ("净资产（亿元）", "净资产(亿元)"))
    if not report_col:
        return None

    df["date"] = pd.to_datetime(df[report_col], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if date:
        cutoff = pd.to_datetime(date, errors="coerce")
        if pd.notna(cutoff):
            df = df[df["date"] <= cutoff]
    if df.empty:
        return None

    latest = df.iloc[-1]
    result = {
        "date": latest["date"].strftime("%Y-%m-%d"),
        "stock_ratio": _to_float(latest.get(stock_col, 0.0) if stock_col else 0.0, 0.0) or 0.0,
        "bond_ratio": _to_float(latest.get(bond_col, 0.0) if bond_col else 0.0, 0.0) or 0.0,
        "cash_ratio": _to_float(latest.get(cash_col, 0.0) if cash_col else 0.0, 0.0) or 0.0,
        "fund_ratio": _to_float(latest.get(fund_col, 0.0) if fund_col else 0.0, 0.0) or 0.0,
        "assets": _to_float(latest.get(assets_col, 0.0) if assets_col else 0.0, 0.0) or 0.0,
    }
    result["other_ratio"] = round(
        max(
            0.0,
            100.0
            - result["stock_ratio"]
            - result["bond_ratio"]
            - result["cash_ratio"]
            - result["fund_ratio"],
        ),
        2,
    )
    return result
