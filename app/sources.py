"""Opportunity Radar - 数据源适配层
原则：只用免费、无需 API Key 的官方开放接口。
- 世界银行开放数据 API（宏观指标）
- OECD SDMX API（消费者信心指数，失败则优雅降级）
- Wikimedia Pageviews API（品类需求热度代理）
每个数据源都做了超时与降级处理，单一来源挂掉不影响整体服务。
"""
from __future__ import annotations

import requests
import time
from datetime import datetime
from typing import Any

WB = "https://api.worldbank.org/v2"
WIKI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
UA = {"User-Agent": "OpportunityRadar/1.0 (open-source demo; contact: local)"}
TIMEOUT = 20

# 关注的市场（可自行增删，均为 ISO3 代码）
COUNTRIES = {
    "CHN": "中国", "USA": "美国", "FRA": "法国", "DEU": "德国", "GBR": "英国",
    "JPN": "日本", "KOR": "韩国", "IND": "印度", "IDN": "印尼",
    "BRA": "巴西", "MEX": "墨西哥", "SAU": "沙特", "ARE": "阿联酋",
}
ISO3 = ";".join(COUNTRIES.keys())  # 注意：世界银行多国用分号分隔

# 世界银行指标：宏观(GDP/消费/通胀) + 民生(失业率/青年失业率)
# 口径均为官方统计，但存在时滞（失业率通常滞后1-2年），页面上会标注数据年份
WB_INDICATORS = {
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",           # GDP实际增速 %
    "consumption_growth": "NE.CON.PRVT.KD.ZG",    # 居民最终消费增速 %
    "inflation": "FP.CPI.TOTL.ZG",               # CPI通胀 %
    "unemployment": "SL.UEM.TOTL.ZS",            # 失业率 %（占总劳动力，ILO口径）
    "youth_unemployment": "SL.UEM.1524.ZS",      # 15-24岁青年失业率 %
}


def fetch_worldbank() -> dict[str, dict[str, Any]]:
    """拉取各国最近5年宏观指标，返回 {ISO3: {gdp_growth: v, ...}}"""
    result: dict[str, dict[str, Any]] = {c: {} for c in COUNTRIES}
    for name, code in WB_INDICATORS.items():
        url = f"{WB}/country/{ISO3}/indicator/{code}"
        params = {"format": "json", "per_page": 500, "date": "2019:2026"}
        try:
            data = requests.get(url, params=params, headers=UA, timeout=TIMEOUT).json()
            rows = data[1] or []
            for row in rows:
                if row["value"] is None:
                    continue
                iso = row["countryiso3code"]
                year = int(row["date"])
                bucket = result.setdefault(iso, {})
                # 每个指标只留最近3个有效年份
                if year > bucket.get(f"{name}_year", 0):
                    bucket[name] = round(float(row["value"]), 2)
                    bucket[f"{name}_year"] = year
        except Exception as e:  # 降级：该指标缺失不致命
            print(f"[worldbank] {name} 获取失败: {e}")
    for iso in list(result):
        for k in ("gdp_growth", "consumption_growth", "inflation",
                  "unemployment", "youth_unemployment"):
            y = result[iso].pop(f"{k}_year", None)
            if y:
                result[iso][f"{k}_data_year"] = y  # 保留数据年份作为时效证据
    return result


def fetch_oecd_cci() -> dict[str, list[dict]]:
    """OECD 消费者信心指数（CCI>100 偏乐观，<100 偏悲观）。
    返回 {国家名: [{t: 'YYYY-MM', v: float}, ...]}；接口失败返回空并降级。"""
    url = ("https://sdmx.oecd.org/public/rest/data/"
           "OECD.ECO.MAD,DSD_CCI@DF_CCI,1.1/"
           "A..M?startPeriod=2023-01&format=csvfile")
    out: dict[str, list[dict]] = {}
    try:
        import csv, io
        text = requests.get(url, headers=UA, timeout=30).text
        for row in csv.DictReader(io.StringIO(text)):
            country = row.get("Reference area") or row.get("LOCATION") or ""
            period = row.get("TIME_PERIOD") or ""
            value = row.get("OBS_VALUE") or ""
            if country and period and value:
                out.setdefault(country, []).append(
                    {"t": period[:7], "v": float(value)})
        for c in out:
            out[c] = sorted(out[c], key=lambda x: x["t"])[-12:]
    except Exception as e:
        print(f"[oecd] CCI 获取失败（服务降级为不使用信心指标）: {e}")
    return out


def _get(url: str, params: dict | None = None, timeout: int = 25, retries: int = 2):
    """带重试的 GET。"""
    for i in range(retries + 1):
        try:
            return requests.get(url, params=params, headers=UA, timeout=timeout)
        except Exception as e:
            if i == retries:
                raise
            print(f"[http] 重试 {i+1}: {e}")
            time.sleep(2)


_wiki_dead = {"down": False, "fails": 0}  # 熔断器：连续3次失败后跳过，避免拖慢刷新


def fetch_wiki_topic(article: str) -> list[dict]:
    """单条维基百科词条近24个月月度浏览量，作为需求热度代理。"""
    if _wiki_dead["down"]:
        return []
    end = datetime.utcnow()
    start = f"{end.year - 2}010100"          # 近24个月
    end_str = f"{end.year}{end.month:02d}2823"  # 到当月
    url = f"{WIKI}/{article}/monthly/{start}/{end_str}"
    try:
        data = _get(url, timeout=15, retries=0).json()
        _wiki_dead["fails"] = 0
        return [{"t": item["timestamp"][:6], "v": item["views"]}
                for item in data.get("items", [])][-24:]
    except Exception as e:
        _wiki_dead["fails"] += 1
        if _wiki_dead["fails"] >= 3:
            _wiki_dead["down"] = True
            print(f"[wiki] 连续{_wiki_dead['fails']}次失败，本轮剩余词条跳过（熔断）")
        else:
            print(f"[wiki] {article} 获取失败: {e}")
        return []
