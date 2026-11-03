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


def _fetch_hard_data() -> dict:
    """生产型硬数据（FRED行政数据）：真实个体事件/税务记录，非调查问卷。"""
    hard = {}
    for key, meta in sources.FRED_SERIES.items():
        series = sources.fetch_fred(meta["id"], years=2)
        if not series:
            continue
        vs = [p["v"] for p in series]
        latest_t, latest_v = series[-1]["t"], vs[-1]
        # 近4周均值 vs 12周前均值 → 短期趋势
        recent = sum(vs[-4:]) / 4
        prior = sum(vs[-16:-12]) / 4 if len(vs) >= 16 else recent
        trend = (recent - prior) / prior * 100 if prior else 0
        hard[key] = {**meta, "latest": latest_v, "date": latest_t,
                     "trend_pct": round(trend, 1), "series": series[-52:]}
    return hard


def _build_insights(data: dict) -> list[str]:
    """规则引擎生成白话解读：把数字翻译成人话，每条都回答'所以呢'。"""
    ins = []
    names = data.get("country_names", {})
    countries = sorted(data.get("countries", []), key=lambda c: -c["score"])
    hard = data.get("hard_data") or {}

    # 1. 宏观层
    if countries:
        top3 = [f"{names.get(c['iso'], c['iso'])}({c['score']}分)" for c in countries[:3]]
        bottom = countries[-1]
        ins.append(f"💰 钱在哪变多：动能最高的是 {'、'.join(top3)}。"
                   f"最低的是 {names.get(bottom['iso'], bottom['iso'])}（{bottom['score']}分，"
                   f"GDP仅{bottom['gdp']}%）。"
                   f"→ 想找增量市场，优先看前3；法国这类市场只适合卖'省钱替代'类产品。")
        youth = [c for c in countries if c.get("youth", 0) >= 15]
        if youth:
            yl = "、".join(f"{names.get(c['iso'], c['iso'])}({c['youth']}%)" for c in youth)
            ins.append(f"🧑 青年失业率≥15%的市场：{yl}。"
                       f"→ 年轻人找工作难的地方，副业工具、性价比消费、技能培训的需求最真实。"
                       f"这条比GDP更接近真实生活。")

    # 2. 生产型硬数据层
    if "icsa" in hard:
        icsa = hard["icsa"]
        direction = "下降（就业走强）" if icsa["trend_pct"] < 0 else "上升（就业走弱）"
        ins.append(f"🩺 美国就业体检：每周初请失业金 {icsa['latest']:.0f} 人，近4周比12周前 "
                   f"{('+' if icsa['trend_pct']>0 else '')}{icsa['trend_pct']}%，{direction}。"
                   f"→ 这是行政理赔单据、不是调查问卷，是美国消费力的最快温度计。"
                   f"初请持续上升=美国老百姓钱包在收紧，卖非刚需品要谨慎。")

    # 3. 需求层
    cats = sorted(data.get("categories", []), key=lambda c: -c["score"])
    if data.get("demo_mode"):
        ins.append("⚠️ 品类需求曲线当前是演示数据（本环境访问不到Wikimedia）→ "
                   "机会点矩阵里'需求分'那一半目前不可用于决策，只看宏观分和β。"
                   "部署到你自己的电脑/服务器后会自动变成真实数据。")
    elif cats:
        rising = "、".join(c["name"] for c in cats[:3] if c["growth_pct"] > 0)
        if rising:
            ins.append(f"📈 关注度在真实上涨的品类：{rising}。"
                       "→ 浏览量只是'关注度'不是'销量'，但关注度先于购买出现，"
                       "值得作为选品的候选名单去1688做供给端查重。")

    # 4. 行动建议
    if countries and cats:
        best = data.get("opportunities", [{}])[0]
        ins.append(f"🎯 当前综合排名第1的机会：{names.get(best.get('country',''), best.get('country',''))}"
                   f"×{best.get('category','—')}。但记住分数只是候选名单——"
                   "动手前的三步验证：① 1688/Amazon搜同款（有没有白牌和品牌卡死价位）"
                   "② 挑一个做¥5000预售页测试 ③ 算单台利润（售价−成本−渠道费−退货）。"
                   "任何一步不过，就淘汰，总成本控制在1万以内。")
    ins.append("📖 阅读顺序：先看'民生温度计'（最真实的数）→ 再看宏观分（钱的增量）"
               "→ 最后看机会矩阵（候选名单），每一层都比短视频可靠，但都不替代实地验证。")
    return ins


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
    hard_data = _fetch_hard_data()
    if not hard_data:
        errors.append("FRED 生产型硬数据不可用")
    categories = scoring.score_categories(series_map)
    opportunities = scoring.build_opportunities(countries, categories)
    insights = _build_insights({"countries": countries, "categories": categories,
                                 "opportunities": opportunities, "hard_data": hard_data,
                                 "country_names": sources.COUNTRIES, "demo_mode": demo_mode})
    _state.update(
        data={"macro": macro, "cci": cci, "countries": countries,
              "categories": [{k: v for k, v in c.items() if k != "monthly"} for c in categories],
              "series": series_map, "opportunities": opportunities,
              "country_names": sources.COUNTRIES, "demo_mode": demo_mode,
              "hard_data": hard_data, "insights": insights},
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
