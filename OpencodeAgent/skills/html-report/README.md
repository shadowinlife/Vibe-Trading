# VibeTrading HTML Report

为金融分析报告生成美观的交互式 HTML 页面，支持 ECharts 图表、暗色/亮色主题切换、CJK 字体。

## 功能特性

- **6 种专用模板**: Vibe-Trading 回测、Alpha158 Walk-Forward、七看八问、基本面分析、缠论分析、信号报告
- **ECharts 交互图表**: 蜡烛图、收益曲线、回撤图、雷达图、饼图
- **主题切换**: 右上角按钮切换浅色/深色/跟随系统
- **当前状态头**: 回测报告顶部显示当前信号、建议仓位、判断依据
- **多策略 Tab**: Vibe-Trading 回测支持多策略对比展示
- **本地归档**: 复制到 `/home/opencode/.vibe-trading/reports/`
- **手工导出**: 仅用于用户明确要求的独立 HTML 文件；产品回测报告由控制面渲染

## 适用范围

- 量化回测结果可视化（Alpha158、Vibe-Trading、缠论等）
- 基本面分析报告（七看八问、财务报表）
- 交易信号报告（买入/卖出信号 + 价格图表）
- 任何需要美观 HTML 报告的金融分析场景

## 环境依赖

- Python >= 3.10
- Jinja2 >= 3.0
- pandas >= 1.5
- markupsafe >= 2.0
- ECharts 5 (CDN, 需要网络访问)

## 安装

```bash
pip install jinja2 pandas markupsafe
```

## 快速开始

### 1. 生成回测报告

```python
from scripts.reports.html_renderer import render_vibe_backtest_html

html = render_vibe_backtest_html(
    ts_code="588000.SH",
    name="科创50ETF",
    strategy_name="双均线+动量评分 v5",
    kpis={
        "总收益": {"value": "+41.2%", "change": "+3.8%", "change_positive": True},
        "年化收益": {"value": "+6.7%", "change": "+0.6%", "change_positive": True},
        "最大回撤": {"value": "-25.8%", "change": "+12.1%", "change_positive": True},
        "Sharpe": {"value": "0.60", "change": "+0.15", "change_positive": True},
    },
    equity_curve={
        "dates": ["2020-01", "2021-01", "2022-01", "2023-01", "2024-01", "2025-01"],
        "strategy": [0, 12, 18, 28, 35, 41.2],
        "buyhold": [0, 8, 12, 18, 20, 24.4],
    },
    current_status={
        "signal": "买入",
        "signal_date": "2025-06-26",
        "position": 80,
        "position_reason": "基于 v5 策略评分 >= 3",
        "reason": "MA10 上穿 MA30（金叉），RSI(14) = 58.3 > 50，MACD 柱为正。",
        "key_metrics": {"MA10": "1.120", "MA30": "1.095", "RSI": "58.3"},
    },
    coverage={"start": "2019-12-01", "end": "2025-06-26", "bars": 1350},
)

with open("report.html", "w") as f:
    f.write(html)
```

### 2. 多策略对比

```python
strategies = [
    {
        "name": "v5 双均线+动量",
        "is_best": True,
        "kpis": [{"label": "总收益", "value": "+41.2%", "change": "+3.8%", "change_positive": True}],
        "equity_curve": {"dates": [...], "strategy": [...], "buyhold": [...]},
        "trades": [{"date": "2024-03-15", "direction": "买入", "price": "1.052", "pnl": "+6.5%", "hold_days": "12", "trigger": "金叉"}],
    },
    {
        "name": "v4 双均线",
        "is_best": False,
        "kpis": [{"label": "总收益", "value": "+37.4%"}],
        "equity_curve": {"dates": [...], "strategy": [...], "buyhold": [...]},
    },
]

html = render_vibe_backtest_html(
    ts_code="588000.SH", name="科创50ETF", strategy_name="多策略对比",
    kpis={}, strategies=strategies,
)
```

### 3. 七看八问报告

```python
from scripts.reports.html_renderer import render_seven_look_html

html = render_seven_look_html(
    ts_code="600519.SH",
    name="贵州茅台",
    scores={"profit_quality": 8, "cost_structure": 7, "growth": 9, "business_mix": 6, "balance_sheet": 8, "efficiency": 7, "roe": 9},
    score_details={
        "profit_quality": {"score": 8, "status": "green", "summary": "毛利率稳定在 90%+", "key_metrics": {"毛利率": "91.5%", "净利率": "52.3%"}},
    },
    moat={"brand": 5, "tech": 3, "cost": 4, "scale": 5},
)
```

### 4. 导出到本地归档

```bash
python scripts/reports/deploy_report.py report.html --stock 588000.SH
# 默认写入 /home/opencode/.vibe-trading/reports/588000.SH/report.html
```

本镜像不提供 nginx，也不提供报告 URL。需要分享时由调用方自行处理文件。

## 模板说明

| 模板 | 函数 | 适用场景 |
|------|------|---------|
| Vibe-Trading 回测 | `render_vibe_backtest_html()` | 12 KPI + 收益曲线 + 交易表 |
| Alpha158 Walk-Forward | `render_alpha158_backtest_html()` | 多策略对比 + 最优策略高亮 |
| 七看八问 | `render_seven_look_html()` | 雷达图 + 财务趋势 + 同行对比 |
| 基本面分析 | `render_fundamental_html()` | KPI + 饼图 |
| 缠论分析 | `render_chanlun_html()` | K 线 + 分型/笔/中枢标注 |
| 信号报告 | `render_signal_html()` | 信号摘要 + 价格图 |
| Markdown 转换 | `render_from_markdown()` | 现有 .md 报告转 HTML |

## 归档位置

手工导出文件写入 `/home/opencode/.vibe-trading/reports/`；产品回测报告由控制面从 `run_dir` 渲染，不走本 skill。

## 安全说明

- 所有路径参数经过严格正则校验，防止命令注入和路径穿越
- Jinja2 模板启用 autoescape，防止 XSS

## License

Apache 2.0
