"""Opportunity Radar - 评分模型与品类定义
三层评分：
1. 国家宏观动能分（0-100）：GDP增速 + 居民消费增速 - 通胀惩罚，对齐到百分位
2. 品类需求趋势分（0-100）：维基百科词条浏览量的近12月 vs 前12月环比 + 线性斜率
3. 机会点得分 = 宏观动能 × 抗萧条系数 × 需求趋势，输出 top 机会矩阵
"""
from __future__ import annotations


def _linear_slope(series: list[dict]) -> float:
    """对 [{t,v},...] 做简单最小二乘斜率，返回每月变化率（相对均值归一）。"""
    vs = [d["v"] for d in series]
    n = len(vs)
    if n < 4:
        return 0.0
    mean = sum(vs) / n or 1
    xs = list(range(n))
    mx, my = sum(xs) / n, mean
    num = sum((x - mx) * (v / mean - 1) for x, v in zip(xs, vs))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den * n if den else 0.0


def _recent_vs_prior(series: list[dict]) -> float:
    """近12个月均值相对前12个月的增幅（%）。"""
    vs = [d["v"] for d in series]
    prior, recent = vs[:-12] or [0], vs[-12:]
    p = sum(prior) / max(len(prior), 1)
    r = sum(recent) / max(len(recent), 1)
    return (r - p) / p * 100 if p else 0.0


# 品类定义：抗萧条系数 recess_beta（0-1，越高越抗跌），词条为需求热度代理
CATEGORIES = [
    {"key": "resale", "name": "二手/循环经济", "recess_beta": 0.9,
     "articles": ["Resale", "Second-hand_good"],
     "note": "经济下行期换新需求迁移为二手与维修"},
    {"key": "repair", "name": "维修/延寿", "recess_beta": 0.9,
     "articles": ["Repair"], "note": "不买新的就修旧的"},
    {"key": "value_food", "name": "在家吃饭/预制食品", "recess_beta": 0.85,
     "articles": ["Convenience_food", "Meal_kit"],
     "note": "外出就餐收缩的最大受益替代品类"},
    {"key": "insurance", "name": "保险/防御型金融", "recess_beta": 0.8,
     "articles": ["Insurance"], "note": "不确定性越大防御配置越强"},
    {"key": "security", "name": "安防/防损", "recess_beta": 0.75,
     "articles": ["Security", "Closed-circuit_television"],
     "note": "萧条期纠纷与失窃上升的防御型消费"},
    {"key": "pet", "name": "宠物经济", "recess_beta": 0.7,
     "articles": ["Pet_food", "Pet"], "note": "情感陪伴的口红效应"},
    {"key": "health", "name": "健康/自我护理", "recess_beta": 0.7,
     "articles": ["Dietary_supplement", "Physical_fitness"], "note": "小确幸型健康消费"},
    {"key": "side_hustle", "name": "副业/零工经济", "recess_beta": 0.85,
     "articles": ["Side_hustle", "Gig_economy"], "note": "收入焦虑的直接产物"},
    {"key": "dining", "name": "外出就餐(反例对照)", "recess_beta": 0.2,
     "articles": ["Restaurant"], "note": "萧条期最先被砍的可选消费"},
    {"key": "luxury", "name": "奢侈品(反例对照)", "recess_beta": 0.3,
     "articles": ["Luxury_goods"], "note": "反例对照：下行期承压"},
]


def score_countries(macro: dict, cci: dict | None) -> list[dict]:
    """国家动能分 = GDP增速 + 居民消费增速 − 通胀惩罚 − 就业惩罚。
    原始值全部随结果输出（前端展示为证据链），minmax 归一到 0-100。
    cci 参数保留（如接入OECD信心指数可加权），当前默认不影响评分。"""
    rows = []
    for iso, m in macro.items():
        if not m.get("gdp_growth"):
            continue
        gdp = m.get("gdp_growth", 0)
        cons = m.get("consumption_growth", gdp)
        infl = m.get("inflation", 2)
        unemp = m.get("unemployment", 5)
        youth = m.get("youth_unemployment", unemp * 2.2)
        infl_pen = max(infl - 2, 0) * 0.5       # 通胀超2%部分折半惩罚
        job_pen = max(unemp - 5, 0) * 0.4 + max(youth - 12, 0) * 0.15
        raw = gdp + cons - infl_pen - job_pen
        rows.append({"iso": iso, "gdp": gdp, "cons": cons, "infl": infl,
                     "unemp": unemp, "youth": youth,
                     "gdp_data_year": m.get("gdp_growth_data_year"),
                     "unemp_data_year": m.get("unemployment_data_year"),
                     "raw_raw": round(raw, 2), "raw": raw})
    if not rows:
        return []
    lo, hi = min(r["raw"] for r in rows), max(r["raw"] for r in rows)
    span = (hi - lo) or 1
    for r in rows:
        r["score"] = round((r["raw"] - lo) / span * 100, 1)
        r["raw"] = round(r["raw"], 2)
    return rows


def score_categories(series_map: dict[str, list[dict]]) -> list[dict]:
    """品类需求趋势分：近期vs前期增幅(70%) + 斜率(30%)，归一 0-100。"""
    rows = []
    for cat in CATEGORIES:
        series = series_map.get(cat["key"], [])
        if not series:
            continue
        growth = _recent_vs_prior(series)
        slope = _linear_slope(series) * 100
        raw = growth * 0.7 + slope * 0.3
        rows.append({**{k: cat[k] for k in ("key", "name", "recess_beta", "note")},
                     "growth_pct": round(growth, 1), "slope_pct": round(slope, 2),
                     "raw": raw, "monthly": series})
    if not rows:
        return []
    lo, hi = min(r["raw"] for r in rows), max(r["raw"] for r in rows)
    span = (hi - lo) or 1
    for r in rows:
        r["score"] = round((r["raw"] - lo) / span * 100, 1)
    return rows


def build_segments(macro: dict, countries: list[dict]) -> list[dict]:
    """分群画像：按年龄段切市场，每段给证据数字 + 推断需求。
    数据限制说明：世界银行开放接口只到 15-64 / 65+ / 15-24 三段，
    更细的5岁组数据在 UN WPP（无稳定免费API）或付费报告（Euromonitor等）中，
    这里用三段+多指标交叉（人口结构×失业×消费）做三角验证。"""
    segs = []
    for c in countries:
        m = macro.get(c["iso"], {})
        wa = m.get("working_age_share")
        el = m.get("elderly_share")
        if wa is None:
            continue
        # 青年段 15-24
        youth_unemp = c.get("youth", 0)
        segs.append({"iso": c["iso"], "segment": "🧑‍🎓 青年段 15-24",
                     "evidence": {"青年失业率%": youth_unemp, "GDP增速%": c["gdp"],
                                  "数据年": m.get("youth_unemployment_data_year")},
                     "needs": ["副业/零工", "技能培训", "性价比消费", "考证/求职服务"],
                     "reading": ("就业压力大，副业与省钱类需求最硬" if youth_unemp >= 14
                                 else "就业相对健康，新消费品牌接受度高")})
        # 中坚段 15-64
        cons = c.get("cons", 0)
        segs.append({"iso": c["iso"], "segment": "💼 中坚段 25-54（含在劳动年龄内）",
                     "evidence": {"劳动年龄占比%": wa, "居民消费增速%": cons,
                                  "总失业率%": c.get("unemp")},
                     "needs": ["家庭刚需", "育儿/教育", "居住改善", "防御型保险"],
                     "reading": ("人口主力+消费在涨，家庭刚需与育儿是基本盘" if cons > 3
                                 else "消费增速弱，主打'降本增效'与替代性消费")})
        # 银发段 65+
        if el is not None:
            segs.append({"iso": c["iso"], "segment": "👵 银发段 65+",
                         "evidence": {"65+人口占比%": el},
                         "needs": ["医疗/慢病管理", "养老照护", "陪伴/适老产品", "传承/资产配置"],
                         "reading": ("深度老龄化（>14%），银发经济供给缺口大" if el >= 14
                                     else "老龄化早期，银发需求刚起步" if el >= 7
                                     else "年轻型社会，银发市场机会有限")})
    return segs


def build_opportunities(countries: list[dict], categories: list[dict]) -> list[dict]:
    """机会矩阵：macro_score × recess_beta × demand_score，取每品类 top3 国家。"""
    opps = []
    for cat in categories:
        for ctry in countries:
            o = (ctry["score"] / 100) * cat["recess_beta"] * (0.3 + cat["score"] / 100 * 0.7)
            opps.append({"category": cat["name"], "country": ctry["iso"],
                         "beta": cat["recess_beta"], "macro": ctry["score"],
                         "demand": cat["score"], "score": round(o * 100, 1)})
    opps.sort(key=lambda x: -x["score"])
    return opps[:30]
