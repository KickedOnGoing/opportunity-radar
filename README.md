# 📡 Opportunity Radar 机会雷达

不靠刷视频建立世界观——用**免费开放数据**看大势、看行业需求、找机会点。

## 它回答三个问题

1. **哪些市场钱包在变大？** 各国宏观动能分（GDP 增速 + 居民消费增速 − 通胀惩罚）
2. **哪些品类在萧条里反而涨？** 品类需求趋势（近 12 月 vs 前 12 月）× 抗萧条 β 系数
3. **钱和市场交界处的机会在哪？** 机会点矩阵 = 宏观动能 × 抗萧条 β × 需求趋势

## 数据源（全部免费、无需 API Key）

| 来源 | 用途 |
|---|---|
| 世界银行开放数据 API | GDP 增速、居民消费增速、通胀 |
| OECD SDMX API | 消费者信心指数（可选，失败自动降级） |
| Wikimedia Pageviews API | 品类需求热度代理指标 |

## 运行

```bash
pip install fastapi uvicorn requests
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000` 即可查看仪表盘。启动后每 12 小时自动后台刷新，也可点击"刷新数据"手动触发。

## 项目结构

```
app/
  sources.py    # 数据源适配层（世界银行 / OECD / Wikimedia）
  scoring.py    # 评分模型 + 品类定义（含抗萧条 β 与 Google Trends 预留接口）
  main.py       # FastAPI 服务 + 后台定时刷新线程
static/index.html  # 仪表盘
data/cache.json    # 数据缓存
```

## 如何扩展

- **加市场**：`app/sources.py` 的 `COUNTRIES` 增删 ISO3 代码
- **加品类**：`app/scoring.py` 的 `CATEGORIES` 添加条目（`recess_beta` 为抗萧条系数 0-1）
- **接 Google Trends**：在 `sources.py` 实现同名适配函数替换维基代理指标即可

## 使用须知

仪表盘输出的机会点分数是**筛选起点，不是投资建议**——动手前仍需：① 1688/Alibaba 供给端查重（有没有白牌和品牌卡位）② 预售页最小验证 ③ 单位经济测算。

## License

MIT
