# 日内导航 runtime

个人自用的结构许可循环。规格在上一级 [日内交易辅助agent-开发文档.md](../日内交易辅助agent-开发文档.md)。这里的 `config/`、`jobs/`、`inbox/` 对应该文档第 3 节。

不做下单，不接实盘行情，不调用模型。`config/llm.yaml` 的 `model` 为空时，决策段全部来自 `prompts/card.txt`。Telegram 写成 `outbox/*.jsonl`，成功后再在 inbox 追加 `telegram_ack`。

## 运行

```bash
cd product-design/交易辅助agent/runtime
python3 -m venv .venv
.venv/bin/pip install pytest PyYAML
.venv/bin/python -m pytest
.venv/bin/python -m jobs.bar_loop --day 2026-11-11
```

第二条命令用合成 RTH 日跑完整决策循环，写入 `inbox/2026-11-11.jsonl`。

历史回放：

```bash
.venv/bin/python -m jobs.backtest import --csv bars.csv --sqlite data/market.sqlite
.venv/bin/python -m jobs.backtest replay --day 2026-11-11 --sqlite data/market.sqlite --out replay/v0.1.0/2026-11-11
.venv/bin/python -m jobs.backtest diff --live inbox/2026-11-11.jsonl --sim replay/v0.1.0/2026-11-11/sim.jsonl
```

CSV 列：`symbol,bar_end,open,high,low,close,volume,timeframe,vwap,source`。`timeframe` 用 `1m` 或预热用的 `decision`。

收盘复盘只写 Markdown，不改 inbox：

```bash
.venv/bin/python -m jobs.recap --inbox inbox/2026-11-11.jsonl --out recap/2026-11-11.md
```

## 目录

| 路径 | 作用 |
| --- | --- |
| `nav/state.py` | 许可状态机 |
| `nav/features.py` | 决策特征与冲突 |
| `nav/bar_loop.py` | 1 分钟聚合成决策桶并逐拍推进 |
| `nav/backtest.py` | import / replay / diff / summary |
| `nav/ingest.py` | 离线秒桶 |
| `nav/review.py` | MFE/MAE、证伪卡、复盘稿 |
| `docs/开发日志.md` | 实现过程与和规格的差异 |

过程记录见 [docs/开发日志.md](docs/开发日志.md)。
