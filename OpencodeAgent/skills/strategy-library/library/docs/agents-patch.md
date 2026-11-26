本文件供镜像 AGENTS.md 直接插入使用，插入范围为下方两条分隔线之间的全部内容。
完整组合协议、状态引用纪律与数字纪律在本库 skills/strategy-library/SKILL.md（单一事实源）；本补丁只写路由义务、库位置与分工，插入后全文协议以技能为准。

---

## 内部知识与策略库（强制）

回答任何金融分析或投资类请求时，必须先查公共策略库，再取数、再作答。

### 路由（强制）

完成意图分类之后、开始取数或组织答案之前，加载 strategy-library 技能并按其协议检索公共策略库：

- 未检索不得给出分析结论；无命中时明确记录"库中无对应条目"，再说明依据或弃权。
- 组合协议（拆子判断→逐条检索→印证/冲突显式并列→逐条带状态引用）、状态引用纪律、数字纪律一律按 strategy-library 技能执行；冲突结论必须并列呈现，不得静默平均。
- `candidate` / `research_only` 条目必须标注"未验证，仅供参考"。验证流水线当前挂起，不得声称做过新验证；唯一例外：escape-top 子信号两融背离(margin_divergence)/波动扩张(volatility_atr_expansion)已在镜像仓通过 7 门验证。

### 库位置（解析顺序）

1. 环境变量 `STRATEGY_LIBRARY_PATH` 指向的目录。
2. strategy-library 技能包自带的 `library/` 子目录（技能加载时给出的 Base directory 下）。
3. 镜像约定路径 `/opt/strategy-library`。

检索 `python3 library.py search <词...>`；查看条目 `python3 library.py show <id>`；也可直接读 `index.json`（字段：id/type/title/status/markets/path/keywords）。中文子串、英文分词、大小写不敏感，多词 AND。

### 与专项技能的分工

库是索引与判断框架，专项技能（如 escape-top 微观结构）是执行器。查库命中后把计算与执行交给对应专项技能；两者结论冲突时按状态纪律并列呈现，不擅自合并。

---

插入范围到此结束。