# D2-1 Track A 裁决：孪生仲裁门禁（2026-08-29）

> 门禁（冻结于 `../../../../HARNESS_EVOLUTION_D2_PLAN.md` §3.1）：within−full top-1 命中率
> 差的 Newcombe CI 下界 > −10pp；语料规模由 A0 功效分析定稿（quant N=240 /
> webdocs N=320，query×判官配对池化）。判官面板：qwen3.8-max + kimi-k3
> （judge_config_a5a8.yaml）。within 臂 = 白名单语料 + 协议 v2 政策块
> （生产仲裁句钉版，policy sha256 89a8d89a…/…）；full 臂 = 全表面、v1 协议。
> 复现：`python -m src.evals.tool_selection.d_batch.d2_track_a_report`。

## 结果

| 域 | N | within | full | Δ | CI95(unpaired) | McNemar | 门禁 |
|---|---|---|---|---|---|---|---|
| quant | 240 | 230 (95.8%) | 214 (89.2%) | **+6.67pp** | **[+1.95, +11.61]** | b/c=23/7, p=0.005 | ✅ **PASS** |
| webdocs | 320 | 301 (94.1%) | 194 (60.6%) | **+33.44pp** | **[+27.36, +39.28]** | b/c=117/10, p<0.001 | ✅ **PASS** |

**D2-1 通过：两域 CI 下界均为正，远越 −10pp 界。** D2-2（主循环收敛）与
D2-3（D4 铺开评审）按计划解锁。

## 解读（如实边界）

1. **效应方向是"优"而非"非劣"**：within 臂两域均显著优于全表面。机制：
   语料按设计富集孪生邻近查询，全表面上 59+90 候选里同名孪生持续混淆；
   within 臂的小面 + 仲裁句把混淆消除了。这正是 D 批"小面落在舒适区"
   论点的功效充足证实。
2. **绝对值不可与 D 批直接比**：本次语料是孪生富集设计（D 批为自然混合），
   有效读数是配对 Δ，不是单方准确率。
3. **判官格式噪声（保守方向）**：within 臂 19 个 miss 中 10 个是 kimi-k3
   以裸名作答（`web_search` 而非 `tool:web_search`），按 D 批冻结的严格
   top-1 口径计为 miss。若按 lenient 口径这些全部翻正——即真实差距比
   报告值更大，裁决方向不变且被低估。记录为判官纪律观察，不改协议。
4. **残余真实 miss**：quant 域 7 个 full-only 命中里，2 个是真实孪生误选
   （D2Q-015/016：alpha_zoo 工具↔技能、factor-research↔alpha-zoo 邻近族），
   5 个为非孪生的次优选择（write_file 先于 backtest 等）。孪生误选率从
   D 批的 100%-of-misses 降到 2/240。
5. **门禁认证范围**（A0 已声明）：本门禁认证"子代理化不劣于 −10pp"，
   不单独认证仲裁句的增量效应（那需要 v1/v2 prompt 双臂，未排期）。

## 产物

-  traces：`llm_judge_trace_{qwen3.8-max,kimi-k3}_post_d2-{quant,webdocs}-{within,full}.jsonl`（1120 调用全记录，含 template/policy hash）
-  语料：`d_batch/queries_d2_{quant,webdocs}.yaml`（224 新条，动词消歧纪律）
-  验证器：`d_batch/d2_corpus_validate.py`；分析：`d_batch/d2_track_a_report.py`；
  功效：`d_batch/d2_power_analysis.py` + `power_analysis.md`
