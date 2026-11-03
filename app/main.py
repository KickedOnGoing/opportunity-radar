"""Opportunity Radar - FastAPI 服务 + 后台定时刷新
- 启动时立即抓一轮数据并缓存到 data/cache.json
- 每 12 小时后台线程自动刷新（服务形态常驻运行）
- /api/* 提供数据接口；/ 提供仪表盘页面
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import sources, scoring

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache.json"
REFRESH_HOURS = 12
_state = {"data": None, "updated": None, "errors": []}


def _sample_series(key: str) -> list[dict]:
    """当真实需求源不可达时，生成明确标注的演示数据（仅用于展示管线，不冒充真实）。"""
    import math
    seeds = {"resale": 12, "repair": 9, "value_food": 15, "insurance": 8,
             "security": 10, "pet": 11, "health": 7, "side_hustle": 18,
             "dining": -20, "luxury": -12}
    drift = seeds.get(key, 5) / 100
    out = []
    now = time.gmtime()
    for i in range(23, -1, -1):
        t = time.gmtime(time.time() - i * 30 * 86400)
        month = f"{t.tm_year}{t.tm_mon:02d}"
        base = 100000 * (1 + drift) ** (23 - i)
        out.append({"t": month, "v": int(base * (1 + 0.1 * math.sin(i)))})
    return out


def _refresh():
    t0 = time.time()
    errors = []
    demo_mode = False
    macro = sources.fetch_worldbank() or {}
    if not macro:
        errors.append("世界银行数据不可用")
    cci = sources.fetch_oecd_cci()
    if not cci:
        errors.append("OECD 消费者信心数据不可用（已降级）")
    series_map = {}
    for cat in scoring.CATEGORIES:
        merged = []
        for art in cat["articles"]:
            for point in sources.fetch_wiki_topic(art):
                merged.append(point)
        by_month: dict[str, int] = {}
        for p in merged:
            by_month[p["t"]] = by_month.get(p["t"], 0) + p["v"]
        series = [{"t": k, "v": by_month[k]} for k in sorted(by_month)]
        if not series:  # 需求源不可达（如内网/沙箱防火墙）→ 演示数据兜底
            demo_mode = True
            series = _sample_series(cat["key"])
        series_map[cat["key"]] = series
    if demo_mode:
        errors.append("需求热度源（Wikimedia）不可达，品类趋势当前为【演示数据】——"
                      "在自有服务器部署时将自动使用真实数据")
    countries = scoring.score_countries(macro, cci)
    categories = scoring.score_categories(series_map)
    opportunities = scoring.build_opportunities(countries, categories)
    _state.update(
        data={"macro": macro, "cci": cci, "countries": countries,
              "categories": [{k: v for k, v in c.items() if k != "monthly"} for c in categories],
              "series": series_map, "opportunities": opportunities,
              "country_names": sources.COUNTRIES, "demo_mode": demo_mode},
        updated=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        errors=errors,
    )
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps({"data": _state["data"], "updated": _state["updated"],
                                "errors": _state["errors"]}, ensure_ascii=False))
    print(f"[radar] 数据刷新完成，耗时 {time.time()-t0:.1f}s")


def _loop():
    while True:
        try:
            _refresh()
        except Exception as e:
            print(f"[radar] 刷新异常: {e}")
            _state["errors"].append(str(e))
        time.sleep(REFRESH_HOURS * 3600)


app = FastAPI(title="Opportunity Radar 机会雷达")


@app.on_event("startup")
def startup():
    if CACHE.exists():  # 先用旧缓存秒开，再后台刷新
        try:
            cached = json.loads(CACHE.read_text())
            _state.update(data=cached["data"], updated=cached["updated"],
                          errors=cached.get("errors", []))
        except Exception:
            pass
    threading.Thread(target=_loop, daemon=True).start()


@app.get("/api/dashboard")
def dashboard():
    return {"updated": _state["updated"], "errors": _state["errors"],
            **(_state["data"] or {"countries": [], "categories": [],
                                  "opportunities": [], "series": {},
                                  "macro": {}, "country_names": {}})}


@app.get("/api/opportunities")
def opportunities():
    return {"updated": _state["updated"], "opportunities": _state["data"]["opportunities"] if _state["data"] else []}


@app.get("/api/refresh")
def manual_refresh():
    threading.Thread(target=_refresh, daemon=True).start()
    return {"ok": True, "msg": "后台刷新已触发，约1-2分钟后生效"}


@app.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parent.parent / "static" / "index.html")
