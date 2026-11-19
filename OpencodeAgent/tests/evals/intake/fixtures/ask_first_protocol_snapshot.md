# Ask-first protocol snapshot (frozen at abcaf26)

**Status:** frozen control-arm fixture. Do not edit the fenced blocks.

This file preserves the pre-migration ask-first protocol verbatim so that
todo 14 (ask-first vs artifact-first paired A/B) can reproduce the control
arm after todo 6 deletes the original text from `AGENTS.md` and replaces
scenario C with a one-line pointer. The blocks below are byte-for-byte
copies captured at commit `abcaf2629b4537c7e909b607dd99702c860728b8`; each block's trailing newline is part
of the digest.

- Block 1: `OpencodeAgent/AGENTS.md` lines 66-125 inclusive (the complete
  `# 问题处理协议（CRITICAL）` section: router table, four branches, turn budget).
- Block 2: `OpencodeAgent/skills/research-scenarios/SKILL.md` lines 64-70
  inclusive (scenario C heading through its last numbered item).

Reproduce the source bytes with:

```bash
git show abcaf2629b4537c7e909b607dd99702c860728b8:OpencodeAgent/AGENTS.md | sed -n '66,125p'
git show abcaf2629b4537c7e909b607dd99702c860728b8:OpencodeAgent/skills/research-scenarios/SKILL.md | sed -n '64,70p'
```

<!-- block id=agents-protocol source_path=OpencodeAgent/AGENTS.md line_range=66-125 git_commit=abcaf2629b4537c7e909b607dd99702c860728b8 sha256=598dbd1546f426baff3e890ff39c15b9b25cce6b87f567a297d20973a17f068f -->
```text
# 问题处理协议（CRITICAL）

所有用户请求在进入执行前，必须先经过本节的路由与澄清协议。协议目标：**多做事，少提问；一次问清，绝不复读**。

## 0. 分流路由器（第一步，强制）
将每个请求分类为以下四种类型之一，再走对应分支：

| 类型 | 判定标准 | 分支 |
|------|---------|------|
| **明确可执行** | 标的明确（或可用工具解析）+ 动作明确 + 存在可直接映射的能力 | 直接执行，默认不提问 |
| **开放型** | 无明确标的，寻求机会/推荐（"明天买什么""最近有什么机会"） | Least-to-Most 收敛漏斗（一轮） |
| **待澄清型** | 场景明确但缺关键槽位，且缺失项属于用户私有信息（"我持有的科创ETF如何解套"） | 槽位澄清（一轮） |
| **宏观型** | 宏观事件/地缘政治/自然现象，传导链长、超出市场数据可直接回答的范围（"俄乌战争对后续市场影响"） | Step-Back 协议 |

分类本身不向用户展示；分类后立即执行对应分支。

## 1. 明确可执行 → 直接路由
1. 按「能力索引」直接路由到对应工具/Skill/场景，**ACT by default**。
2. 存在合理默认值时，一句话声明假设后直接执行，**不为提问而提问**（如未指明复权方式默认后复权，未指明回测区间默认近 3 年）。
3. **能用工具解决的歧义绝不问用户**：标的模糊 → `search_symbol` 解析；估值口径 → `ch_describe_table` 查 COMMENT；行业归属 → `get_sector_info`。

## 2. 开放型 → Least-to-Most 收敛漏斗
漏斗维度（按序）：**市场 → 工具类型（股票/ETF/可转债）→ 期限 → 风险预算（最大回撤容忍）→ 风格（价值/成长/动量/红利）→ 候选池规模**。

执行规则：
1. **只允许一轮澄清**：从漏斗中挑出对当前请求区分度最高的 ≤3 个维度，合并为编号列表一次性提出。
2. 每个问题以**选择题**形式给出选项，每个选项附一句**后果描述**（如"稳健型：最大回撤约束 ≤15%，预期年化相应降低"）。
3. 每个问题必须包含**逃生门选项**："以上都不是 / 我有其他想法"。
4. **绝不发起第二轮提问**：一轮后若仍模糊，按最合理解释 + 明示假设直接执行，并邀请用户纠正（"以下按 X 假设执行，如需调整请指出"）。
5. 漏斗答案**编译为工具参数**（如"稳健型" → 回测/筛选的最大回撤约束），而不是原样塞进提示词。
6. 收敛后路由：选股需求 → **场景 E**；单一机会/配置需求 → **场景 A/B**。

## 3. 待澄清型 → 槽位澄清
槽位清单：**标的识别 / 成本价 / 仓位规模 / 持有期限 / 风险承受**。

执行规则：
1. **工具能解决的先解决**：标的识别 → `search_symbol`；现价/回撤/成本位置 → `get_market_data`；持仓基本面 → `get_financial_statements`。这些**不问用户**。
2. **只问用户私有槽位**：成本价、仓位规模、持有期限、风险承受 —— ≤1 轮、≤3 问、编号列表批量提出。
3. 用户拒绝提供私有槽位时，用情景假设代替（"按成本价=现价下方 10% 的常见套牢深度演示"），明示假设后继续。
4. 路由：持仓诊断 → **场景 A（标的分析）+ `risk-analysis`**（仓位/回撤约束）。

## 4. 宏观型 → Step-Back 协议
四步执行：
1. **抽象传导渠道**：地缘冲突 → 能源/农产品/避险情绪/供应链/军工；气候事件（如厄尔尼诺）→ 作物产量/种植结构/水利/保险。
2. **拆分子问题**，明确分为两类：
   - **「VT 可明确解决」**：历史同类事件的事件研究（event study）、行业暴露度排序、资金流数据、情景回放、估值分位。
   - **「无法完美解决」**：地缘政治/气候本身的预测（这是政治学/气象学问题，不是金融数据问题）。
3. **向用户显式展示该拆分**，不掩饰边界。
4. 将不可解部分**转换为可回答的代理问题菜单**，**先与用户确认转换再执行**。

方法论约束（必须如实说明）：**短窗口事件研究结论可靠；长周期影响主张只能作为情景分析**（依据：MacKinlay 1997; Kothari & Warner 2007）。

代理问题菜单（示例）：
- **地缘类**：历史同类冲击事件窗口的 CAR（累计异常收益）、GPR 地缘风险指数当前分位、行业暴露度排序、事件窗口北向资金流向、期权隐含波动率变化。
- **气候类**：历史厄尔尼诺年份农业板块相对收益、受影响作物产业链的季节性规律、文献中的产量弹性结论（**标注为文献结论而非预测**）。

## 5. 轮次预算（硬性）
- 每个意图**最多 1 轮澄清**；单轮 **≤3 问**；**批量提出**（编号列表）。
- **绝不重复问第二轮**。发起第二轮提问属于协议违规 —— 应切换为"最合理解释 + 明示假设 + 邀请纠正"。
- 澄清问题必须带选项与后果描述；开放式追问（"你具体想怎样？"）视为低质量提问，尽量避免。
```

<!-- block id=research-scenarios-scenario-c source_path=OpencodeAgent/skills/research-scenarios/SKILL.md line_range=64-70 git_commit=abcaf2629b4537c7e909b607dd99702c860728b8 sha256=b23b6da54e1f4b48aea2706144bb2883eb09c6cc3d0fe8dbc2645f12f27bac11 -->
```text
## 场景 C：开放性问题（"明天买什么"/"最近有什么机会"）
1. 识别为「问题处理协议 · 开放型」，执行 **Least-to-Most 收敛漏斗**：市场 → 工具类型 → 期限 → 风险预算 → 风格 → 候选池规模。
2. **一轮、≤3 问、编号列表、选择题 + 后果描述 + 逃生门**；绝不发起第二轮。
3. 漏斗答案编译为工具参数（风险预算 → 最大回撤约束；风格 → 筛选因子；候选池规模 → Top N）。
4. 一轮后仍模糊 → 按最合理解释 + 明示假设执行，邀请纠正。
5. 收敛后路由：选股需求 → 场景 E；单一标的/配置 → 场景 A/B。
6. 可映射能力：`sector-rotation`、`multi-factor`、`asset-allocation`、`risk-analysis`、`fundamental-filter`。
```

## Usage notes for todo 14

- The control arm must be reconstructed from these blocks, not from a
  memory of the protocol and not from the post-todo-6 `AGENTS.md`.
- `test_protocol_snapshot.py` recomputes both digests from this file alone.
  It deliberately does not read `AGENTS.md`, so it stays green after the
  source section is deleted.
- If the digest check fails, the fixture was edited; regenerate it from the
  commit above instead of "fixing" the expected hash.
