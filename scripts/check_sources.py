"""数据源自检脚本
用法：python3 scripts/check_sources.py
用途：在你的部署环境里逐项验证三个数据源是否可达，明确告诉你哪个会用真实数据、哪个会降级。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sources  # noqa: E402


def check_worldbank():
    print("\n[1/3] 世界银行开放数据 API（宏观指标）")
    try:
        m = sources.fetch_worldbank()
        ok = [k for k, v in m.items() if v.get("gdp_growth") is not None]
        print(f"  状态：✅ 可用，成功获取 {len(ok)}/{len(sources.COUNTRIES)} 个国家")
        for iso in ("USA", "CHN", "IND", "FRA"):
            if iso in m and m[iso]:
                print(f"    {sources.COUNTRIES.get(iso, iso)}: {m[iso]}")
        return True
    except Exception as e:
        print(f"  状态：❌ 不可用 -> {e}")
        return False


def check_oecd():
    print("\n[2/3] OECD SDMX API（消费者信心指数）")
    try:
        cci = sources.fetch_oecd_cci()
        if cci:
            sample = list(cci.items())[:2]
            print(f"  状态：✅ 可用，{len(cci)} 个经济体；样例：{sample}")
            return True
        print("  状态：⚠️ 无数据（服务会降级，不影响主流程）")
        return False
    except Exception as e:
        print(f"  状态：⚠️ 降级 -> {e}")
        return False


def check_wiki():
    print("\n[3/3] Wikimedia Pageviews API（品类需求热度）")
    try:
        s = sources.fetch_wiki_topic("Restaurant")
        if s:
            print(f"  状态：✅ 可用，样例词条 Restaurant 共 {len(s)} 个月度数据点")
            print(f"    最近3个月：{s[-3:]}")
            return True
        print("  状态：❌ 不可达 -> 品类趋势将使用【演示数据】")
        print("    常见原因：公司网络/国内防火墙拦截 wikimedia.org；可改接 Google Trends 适配器")
        return False
    except Exception as e:
        print(f"  状态：❌ 不可达 -> {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("机会雷达 · 数据源自检")
    print("=" * 60)
    r1 = check_worldbank()
    r2 = check_oecd()
    r3 = check_wiki()
    print("\n" + "=" * 60)
    print(f"结论：宏观数据 {'真实' if r1 else '不可用'}；"
          f"消费者信心 {'真实' if r2 else '降级'}；"
          f"品类需求 {'真实' if r3 else '演示数据'}")
    if not r3:
        print("👉 若 Wikimedia 不可达，请在 app/sources.py 中实现 Google Trends 适配器替换 fetch_wiki_topic")
    print("=" * 60)
