# KPLuckyNumber 算法与策略全面审查报告（v3.8）

> 审查日期：2026-07-13
> 审查范围：核心预测逻辑、数据流程、性能瓶颈、边界情况、异常处理
> 配套改动：`gui.py`（副标题/版本号/功能列表）、`pipeline.py`（step3 优雅降级）、`modules/json_repair.py`（AI JSON 鲁棒解析）

---

## 1. 核心处理逻辑概览

系统存在**两套并行预测栈**，最终在 step4 融合：

### 栈 A：P5Predictor（五算法加权融合）
- 入口：`predict()` → `_run_algorithms()`（`predictor.py:915`）
- 算法与权重（来自 `config`，见 working memory v3.0 优化）：
  - 频率加权 `frequency_weighted` 35%
  - 遗漏回归 `omission_regression` 25%
  - 趋势动量 `trend_momentum` 12%
  - 马尔可夫 `markov_transition` 10%
  - 形态延续 `pattern_continuation` 8%
  - 贝叶斯推断 `bayesian_inference` 10%
  - （可选）特征工程 `feature_engineering`
- 各算法输出 `List[Dict[int,float]]`（5 位置 × 10 号码后验/打分）。

### 栈 B：Pipeline 多源走势融合
- 入口：`_predict_trend_multi_source()`（`pipeline.py:1600+`）
- 数据源：**历史走势 + 基础走势 + 万千百十个独立走势表 + 升平降方向(spjzs) + 和值重心(hzzst) + 贝叶斯后验**
- 打分权重：
  - 有贝叶斯数据：频率 0.35 + 遗漏 0.25 + 动量 0.15 + 贝叶斯 0.25
  - 无贝叶斯数据：频率 0.45 + 遗漏 0.35 + 动量 0.20
- 指数衰减加权（`halflife=10`），升平降方向偏置、和值重心偏置叠加。

### 最终融合
- `step4_final_prediction()` → `_build_constrained_combinations()`（`pipeline.py:1884`）：
  对每位置 Top-4 候选做笛卡尔积（≤4⁵），用和值区间（自适应带宽）、升平降方向一致性做组合级约束与打分，产出 `recommended_combinations`。

### 在线学习闭环
- 开奖后验证 → `OnlineLearner.track_prediction_result` 增量更新权重（DB `p5_weight_history`）
- 贝叶斯推断 `_algo_bayesian_inference` 读取验证记录文件 `weights_history.json` 计算似然。

---

## 2. 数据流程

```
[ydniu GraphQL 爬虫]
       │
       ▼
[MySQL]  p5_history_data / p5_trend_data / p5_spjzs_data / p5_hzzst_data
         + p5_wan/qian/bai/shi/ge_trend_data
       │
       ├─► P5Predictor.predict()  ──► algorithm_probs (5算法)
       └─► Pipeline._predict_trend_multi_source() ──► 多源融合 Top-4
                   │
                   ▼
       step4 组合约束 ──► 最终预测 ──► [MySQL predictions/artifacts] + [Redis 综合报告/软约束]
                   │
       ┌───────────┴──────────── 开奖后 ────────────┐
       ▼                                            ▼
  手动/自动验证 ──► DB 验证记录 ──► OnlineLearner ──► 权重更新
                                              │
                                              ▼
                              贝叶斯读取 weights_history.json ──► 下一轮后验
```

**缓存机制**：step4 命中 `prediction_stat` artifact（按 issue + history_count 唯一）时跳过 `P5Predictor.predict()` 重算，复用专用表 `p5_bayesian_result`。

---

## 3. 性能瓶颈

| # | 位置 | 问题 | 严重度 |
|---|------|------|--------|
| P1 | `_predict_trend_multi_source` | 每次调用发起多次 DB 查询（hzzst 1 次、spj 方向 1 次、bayes 1–2 次）；回测 50 期 → 50× 多次查询 | 中 |
| P2 | `_algo_bayesian_inference` | 每次 `predict()` 从**磁盘文件** `weights_history.json` 全量读入；回测场景 50× 文件 IO | 中 |
| P3 | `_algo_bayesian_inference` 似然循环 | 每条验证记录对 9 个非预测号码乘 `1.02`（累积 `1.02³⁰≈1.81×`），对预测号码乘 `0.85`；乘性漂移未逐期归一 | 中（正确性） |
| P4 | `_build_constrained_combinations` | 笛卡尔积 4⁵=1024，随 Top-N 增长指数膨胀 | 低（当前 N=4 可控） |
| P5 | 全量重算 | 任一新开奖即失效 `prediction_stat` 缓存 → 完整重算（含最重的贝叶斯） | 低（单次可接受） |
| P6 | 局部 `from collections import Counter` | 方法内重复 import（非性能关键，仅风格） | 低 |

> 注：O(n×10) 的遗漏扫描、Counter 统计等均为常量级，非瓶颈。

---

## 4. 边界情况与潜在风险

| # | 场景 | 现状 | 风险 |
|---|------|------|------|
| B1 | 某位置走势表数据不足 | `n==0 → continue`，该位置不写入 `out` | **中**：下游若按 5 位置全量读取会 KeyError |
| B2 | **DB 数值类型** | DictCursor 返回字符串；`freq` 用原始 `v`（可能 str），后续以 `int d` 查 `freq.get(d)` → 不匹配 → 频率全 0 → 退化为均匀分布 | **高（静默正确性）** |
| B3 | `hist_asc` 为空 | bayes 以 `''` 查询（已兜底） | 低 |
| B4 | `_bayes_posterior` 长度≠5 | `_pos_index < len` 已守卫 | 低 |
| B5 | spj 行数 < 2 / `basic` 为空 | `if basic` / `rows<2` 已守卫 | 低 |
| B6 | 除零 | `total_freq/total_om/total_bayes` 均 `or 1`；`mom_range or 1.0` | 低（已处理） |

**B2 是最值得修复的潜在 bug**：频率融合是打分主干，若因类型不匹配静默退化为均匀，预测质量会无提示地下降。建议统一在读取 DB 数值时 `int()` 归一。

---

## 5. 策略健壮性与正确性审视

1. **随机性前提**：排列5 为独立均匀随机抽样，频率/遗漏/动量等启发式**本质上不具预测力**。当前"命中率提升"主要靠 **覆盖扩展（Top-5）+ 容错±1**，而非信号增强。应在 UI/报告中诚实标注，避免过度承诺。
2. **双栈信号重复**：频率信号同时出现在栈 A（frequency_weighted 35%）与栈 B（频率 0.35–0.45），存在重复计数；两套权重体系也增加调参复杂度。
3. **验证记录源不一致**：在线学习写 DB 验证表，贝叶斯却读 `weights_history.json` 文件——两处可能不同步，导致后验基于过期/缺失反馈。
4. **贝叶斯似然设计**（P3）：乘性 boost 机制会让"非预测号码"系统性上漂，且未逐期归一，长期偏向均匀化。
5. **step3 AI 失败曾导致整条流水线中止**（用户已多次遇到）——**本次已修复为优雅降级**（见第 6 节 O1）。

---

## 6. 优化方向与增强方案（含预期效果与影响范围）

### ✅ O1. step3 优雅降级（已实现，v3.8）
- **改动**：`pipeline.py` step3 —— AI 不可用/JSON 解析失败时，不再 `return error` 中止，而是调用新增 `_build_fallback_integrated_report()` 基于步骤2 走势图预测合成综合报告，置 `fallback_strategy=True` 继续 step4；AI 不可用检查由硬返回改为标记。
- **预期效果**：流水线在 AI 限流/欠费/网络故障下**不再中断**，始终产出基于统计信号的预测。
- **影响范围**：仅 step3→step4 路径；step1/step2/验证/学习不受影响。GUI 已同步展示降级提示。

### 🔧 O2. 数值类型归一化（建议，高优先）
- **改动**：在 `_predict_trend_multi_source` 构建 `freq`/`omission` 时对 DB 值统一 `int()`（参考 bayes 已用 `str(d)`、spj 已用 `int(va)`）。
- **预期效果**：消除"频率全 0 → 均匀退化"的静默错误，恢复频率信号区分度。
- **影响范围**：栈 B 融合打分质量；低风险、局部改动。

### 🔧 O3. 重构贝叶斯似然（建议）
- **改动**：`_algo_bayesian_inference` 似然改为"仅对预测号码 vs 实际号码做奖惩，且每期做归一化（softmax/比例归一）"，去掉 9 号码 `×1.02` 的累积 boost。
- **预期效果**：后验分布更贴合真实命中反馈，避免长期均匀化漂移。
- **影响范围**：贝叶斯后验 → 栈 B 融合权重（0.25）→ 最终组合；中等改动，需回归测试。

### 🔧 O4. 运行内缓存（建议，性能）
- **改动**：step4 单次运行内预加载 spj 方向、hzzst 重心、bayes 后验各一次，传参而非每次查询；`predict()` 内对 `weights_history.json` 加内存缓存（带 TTL）。
- **预期效果**：回测/批量场景 DB 查询与文件 IO 显著下降（回测 50 期可省数十次查询）。
- **影响范围**：性能层；不影响输出正确性。

### 🔧 O5. 验证记录统一存储（建议，一致性）
- **改动**：贝叶斯改从 DB 验证表读取（与在线学习同一数据源），移除对 `weights_history.json` 文件的依赖。
- **预期效果**：消除文件/DB 不一致，在线学习闭环自洽。
- **影响范围**：贝叶斯模块 + 在线学习；需确认 DB 验证表字段覆盖。

### 🔧 O6. 单预测栈收敛（建议，架构）
- **改动**：将栈 A 五算法与栈 B 多源融合统一为单一可解释加权框架，权重由在线学习统一调节，去除频率信号重复计数。
- **预期效果**：调参复杂度下降、信号不重复加权、策略更一致可解释。
- **影响范围**：架构级；高改动，建议分阶段（先统一权重配置，再合并实现）。

### 🔧 O7. 缺失位置兜底（建议，健壮性）
- **改动**：任一位置无数据时回退到全局频率先验（栈 A 的 `frequency_weighted`）而非静默跳过，保证 5 位置恒有输出。
- **预期效果**：避免下游 KeyError（B1），提升极端数据缺失下的可用性。
- **影响范围**：栈 B 输出完整性。

### 🔧 O8. 任务异常兜底（建议，GUI 体验）
- **改动**：后台任务异常后统一重置 `progress` 标签与状态为"就绪/异常"，避免 GUI 卡在"运行中"。
- **预期效果**：操作体验更稳，用户能及时重试。
- **影响范围**：GUI TaskManager。

---

## 7. 异常处理现状

- ✅ 多数 DB 读取包 `try/except` 返回 `[]`；AI 调用带 3 次重试 + **鲁棒 JSON 解析**（`modules/json_repair.py`，已处理单引号/裸 key/尾随逗号/代码块）。
- ✅ `_predict_trend_multi_source` 整体 `try/except` 返回 `{}`；除零均兜底。
- ✅ step3 AI 失败现已优雅降级（O1）。
- ⚠️ 残留：部分 GUI 任务异常后进度条未复位（O8）；验证记录文件/DB 源不一致（O5）。

---

## 8. 结论

核心算法已实现多源融合 + 在线学习闭环，结构合理。最紧迫的两点是 **O2（数值类型静默错误）** 与 **O1（已修复的 step3 中止）**；性能与一致性可通过 **O4/O5** 收敛；长期建议 **O6** 统一双预测栈以提升可维护性与信号纯度。所有启发式策略应配合"命中率靠覆盖与容错、非靠预测信号"的诚实表述。
