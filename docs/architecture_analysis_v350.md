# KPLuckyNumber 架构分析与模块依赖说明（v3.50 / v3.51 修正）

> 本文档基于 v3.50 源码（`gui.py` + `modules/` 19 个模块 + `self_evolution.py`）实际梳理，
> 用于说明整体架构、核心算法、数据流程、关键模块调用关系与依赖，以及本次「自我进化 / 结果显示重构 / 系统管理移除」三处改动。

---

## 1. 系统总览

KPLuckyNumber 是一个**排列5（P5）彩票分析预测**桌面程序。核心定位：**诚实标注排列5为公平摇号，不声称稳定超越随机基线**（Top-1≈10% / Top-3≈30% / Top-5≈50%）。

| 维度 | 实现 |
|------|------|
| GUI 框架 | `tkinter`（`ttk.Notebook` + `Canvas` 虚拟滚动），**非 PyQt5**（旧文档已过时） |
| 异步模型 | `ThreadPoolExecutor` + `queue.Queue` + `root.after(150~200ms)` 轮询；重任务走 `TaskManager` 单工作者线程 |
| 存储 | MySQL（`pymysql`，自动建库建表、自动重连）+ Redis（键前缀 `kpluckynumber:pl5:`，TTL 7~90d） |
| 监督学习 | `sklearn` `GradientBoostingClassifier`（仅 Anaconda/生产环境含；托管 python 不含→优雅降级） |
| 唯一版本源 | `version.py`（`APP_VERSION` / `CHANGELOG` / `KNOWN_ISSUES`） |
| 运行入口 | **仅 GUI**（无命令行入口）：`python gui.py` |

---

## 2. 目录结构与模块清单

```
gui.py                      GUI 主程序（LotteryGUI + TaskManager + _LazyClass 延迟绑定）
config.py                   DB_CONFIG / AGNES_API_CONFIG / SPIDER_CONFIG
version.py                  版本单一来源
paths.py                   路径集中化
modules/
  pipeline.py              ★ 预测流水线（execute_pipeline / _calc_statistical_prediction / step4_final_prediction / _predict_trend_multi_source）
  predictor.py             ★ 七算法融合引擎 + AdaptiveWeightManager（P5PredictorConfig 冻结权重）
  ml_predictor.py          v3.49 多源监督学习（消费位走势/升平降/和值表，按位 GradientBoosting；SKLEARN 缺失降级）
  online_learner.py        在线学习闭环（per-algo 归因优先读 p5_ai_report.per_algo_predictions）
  self_evolution.py        ★ v3.50 新增：自我学习/训练/进化引擎（后台守护线程）
  database.py              MySQL 操作（自动重连）
  database_utils.py        五位分位走势数据泛型读写
  data_fetcher.py          多源数据爬虫（P5Spider）
  ai_analyzer.py           AGNES API AI 分析器（类名 ERNESIAIAnalyzer）
  trend_analyzer.py        走势引擎（默认 period=40）
  features.py / calibration.py / selection_strategy.py / evaluation.py / param_tuner.py
  smart_cache.py / prediction_enhancer.py / redis_storage_manager.py / cache.py
  backtester.py / validator.py / json_repair.py
scripts/                   回归/验证脚本（含 headless_verify_v350.py）
```

---

## 3. 核心算法

### 3.1 七算法 + 监督学习融合（冻结权重，v3.49 再平衡）
`P5PredictorConfig.DEFAULT_CONFIG['algorithms']` 权重（v3.49 起冻结）：

| 算法 | 权重 | 说明 |
|------|------|------|
| frequency_weighted | 0.68 | 频率（主力，经验学习各位分布） |
| ml_supervised | 0.14 | v3.49 新增：GradientBoosting 监督学习（替代被证伪的冷号信号） |
| bayesian_inference | 0.10 | 贝叶斯 |
| omission_regression | 0.06 | 遗漏（v3.49 由 0.34 降至 0.06，实证为噪声/赌徒谬误） |
| trend / markov / shape / feature | 0.01 / 0.005 / 0.003 / 0.002 | 辅助信号 |

- 核心 `lookback=60`（与趋势窗口 40 期相互独立，均冻结）。
- 融合入口 `predictor.predict(history_data, current_issue)` → 返回含 `fused_probabilities`(List[Dict[int,float]]，长5，每位和=1) / `top_combinations` / `predict_time` / `predict_uuid` / `risk_warning`。

### 3.2 多源聚合（GUI「开始分析」展示层）
`pipeline._predict_trend_multi_source` 融合 **四步流水线 + 快速预测 + 走势引擎** 三源，注入核心 `fused_probs`（含 ml_supervised），逐位多数投票选主推；v3.49 已校正其与核心预测器权重割裂。

### 3.3 诚实边界（不可违反）
walk-forward 回测（2060 试验，Wilson 95% CI）显示所有策略 95% CI 均与随机基线重叠，**无稳定超额信号**。任何「优化」不得引入赌徒谬误类信号，候选策略未超越基线一律归档为 `trial`，不改动线上权重。

---

## 4. 端到端数据流程

```
数据爬取(data_fetcher.P5Spider)
   │  INSERT
   ▼
MySQL: p5_history_data / p5_{wan..ge}_trend_data / p5_spjzs_data / p5_hzzst_data
   │  SELECT
   ▼
预测流水线 pipeline.execute_pipeline(target_issue)
   ├─ step1  _calc_statistical_prediction  → P5Predictor.predict()（七算法+监督学习融合）
   │            └─ 贝叶斯/趋势/马尔可夫/形态/特征/遗漏 各算法并行打分
   │            └─ ml_predictor.predict_next() 按位监督学习（消费多源表）
   └─ step2  step4_final_prediction        → 入库 p5_prediction_record / p5_bayesian_result
   │
   ▼
GUI 展示层 _show_result_dashboard（三源聚合 + 分类展示）
   │
   ▼
开奖后验证 → online_learner（验证统计/权重调度/归因覆盖率，per-algo 归因写 p5_ai_report.per_algo_predictions）
   │
   ▼
（可选）AI 辅助解读 ai_analyzer.ERNESIAIAnalyzer（AGNES API）
```

> 注：`pipeline.execute_pipeline` 文档字符串仍写「五步流水线」，但 v3.15 起 step1（专家文章）/step2（走势报告）/step3（综合报告）已**注释停用**，`pipeline_state` 中对应报告恒为 None（属预期，告警已降级 debug）。实际仅两步（统计预测→入库）。

---

## 5. 关键模块调用关系与依赖

### 5.1 调用图（关键边）
```
gui.LotteryGUI._execute_unified_analysis
   └─> pipeline.execute_pipeline  ──(lazy import)──> modules.pipeline
          ├─> _calc_statistical_prediction
          │     └─> predictor.P5Predictor.predict  ──> modules.predictor
          │           ├─> ml_predictor.predict_next  ──> modules.ml_predictor
          │           └─> features / calibration / selection_strategy
          └─> step4_final_prediction  ──> database.insert_ai_report / insert_prediction

gui.LotteryGUI._init_evolution_engine  ──(lazy)──> modules.self_evolution.SelfEvolutionEngine
   └─> engine.start(auto=True)  → 后台守护线程 _run_wrapper → _run
          ├─> _collect_training_data    ──> database(_connect_db)
          ├─> _capture_weight_snapshot   ──> predictor.P5PredictorConfig
          ├─> _drive_ml_retrain          ──> ml_predictor.predict_next（DictCursor 行→{issue,numbers}）
          ├─> _evaluate(full)            ──> ml_predictor.predict_next（滑动窗口 OOS）
          └─> _persist_version          ──> MySQL p5_evolution_version（惰性建表）/ 本地 evolution_versions.json 回退

gui.LotteryGUI._show_result_dashboard
   ├─> _extract_source_data / _aggregate_recommendation（多源聚合）
   └─> 渲染 issue_frame/rec_frame/source_matrix/analysis_frame/alt_frame/risk_frame（均打 _rcat 标记供分类筛选）

TaskManager（单工作者线程）: gui 所有耗时任务经 submit → 线程执行 → emit(log/progress/status/finished/error/...) → gui 主线程 root.after 轮询渲染
```

### 5.2 依赖要点
- **懒加载**：重模块经 `_LazyClass('modules.x','X')` 延迟绑定（gui.py:61）；`predictor/backtester/validator/ml_predictor` 等首次使用时才 import，避免启动阻塞。
- **删除模块铁律**：删除任何模块前必须三向扫描（静态 import / `import_module`·`__import__` / `_LazyClass` 字符串），全零引用才可删（曾误删 `validator.py` 已恢复）。
- **数据顺序陷阱**：`get_history_data(limit=N, order_by='issue ASC')` 返回最旧 N 期；需「最近数据」须 `ORDER BY issue DESC LIMIT N` 再反转。
- **契约**：`predict()['fused_probabilities']` 是 `List[Dict[int,float]]`（非 dict）；`get_verification_stats()` 返回 `*_accuracy` 百分比（无 `*_hits` 计数键）。

---

## 6. 自我进化模块（v3.50 新增）架构

`modules/self_evolution.py` — `SelfEvolutionEngine`，常驻后台、可版本化、可恢复。

- **线程模型**：独立 `threading.Thread(daemon=True)`；经线程安全 `queue.Queue` 与 GUI 通信；GUI `root.after(200ms)` 轮询 `_poll_evolution` 投递消息，不阻塞主界面。
- **自动触发**：`gui._init_evolution_engine()` 在 `LotteryGUI.__init__` 内调用 `engine.start(auto=True)`。自动启动 = **轻量进化**（`full = not self.auto`）：仅当自上次进化新增 ≥`AUTO_RETRAIN_MIN_NEW`(5) 期时才重训；评估走轻量占位，避免每次启动跑耗时滑动窗口。手动「立即进化」按钮 `run_now(full=True)` 才走完整评估。
- **六阶段**：`collect → baseline → evolve → evaluate → persist → done`，每阶段写检查点。
- **训练数据**：`p5_history_data`（开奖历史）、5 张独立位走势表、升平降表、和值表；统计样本量与完整性。
- **参数迭代**：`_capture_weight_snapshot` 读 `P5PredictorConfig` 当前融合权重；`_drive_ml_retrain` 调 `ml_predictor.predict_next` 驱动按位 GB 重训（含 SKLEARN 缺失降级）。
- **评估与回溯**：`_evaluate(full)` 滑动窗口样本外 Top-1/3/5；候选指标 ≥ 基线方可 `active`，否则 `trial`（线上参数不变）。
- **持久化/版本**：DB 表 `p5_evolution_version`（惰性 `_ensure_table`）；DB 不可用回退本地 `data/evolution_versions.json`。
- **异常中断恢复**：`data/self_evolution_state.json` 检查点（阶段+进度+中间产物），重启/中断后从最近检查点续跑。
- **消息类型**：`log`(level) / `progress`(value,text) / `status`(text,color) / `stage`(name,index,total) / `version`(data) / `done`(summary)，全部经 `gui._handle_evolution_msg` 渲染到左侧卡片与「自我进化」标签页。

> **v3.50 修复的两处引擎缺陷**（否则引擎在真实 DB 下静默失效）：
> 1. `_connect_db` 默认返回元组游标，但三处采集/评估代码按字典行访问 → 统一改用 `DictCursor`；`_row_to_dict` 同步兼容字典输入。
> 2. `_evaluate` 把原始 DB 行直接传给 `predict_next`（其要求 `{issue, numbers:[...]}` 格式）→ 样本数解析为 0、永远算不出真实命中率。新增 `_row_to_sorted` 转换，并将滑动窗口起点对齐 `predict_next` 有效最小样本数（`ML_EVAL_MIN=161`）。

> **v3.51 评估诚实性修正（关键）**：v3.50 的走窗评估存在 **off-by-one 前视泄漏**——`window = rows[:idx+1]` 把被评估期 `idx` 本身纳入训练窗口，而 `predict_next` 实际预测的是 `idx+1`，却拿 `idx` 的实开奖号做比对，导致样本外指标被虚高至 ~55–63%（远超随机基线，违背诚实边界）。v3.51 改为 `window = rows[:idx]`（严格只用被评估期之前的数据），正确走窗指标落回 Top1≈8–10% / Top3≈30% / Top5≈43%，与随机基线吻合。同步修正 `_drive_ml_retrain` 轻量代理窗口。另新增 `_MLPredictorPool`：在独立 `spawn` 子进程运行 `predict_next`，隔离其偶发的 sklearn 原生段错误（Python 层不可捕获），确保全量评估（默认 `eval_periods=30`）不会拖垮主 GUI 进程。

---

## 7. GUI 架构

- **左栏**：功能卡片（数据爬取 / 智能分析中心 / 智能分析与验证 / 自我进化[v3.50 新增] / 历史命中率）。
- **右栏**：`output_nb`（ttk.Notebook）标签页：
  - 预测结果（合并仪表盘，Canvas 虚拟滚动 + 分类筛选 全部/预测结论/分位信号/算法依据）
  - 运行日志
  - 历史命中率
  - **自我进化（v3.50 新增）**：概览 / 实时状态 / 进度条 / 进化日志 / 版本树 / 导出 / 清空
- **状态栏**：底部状态 + 进度。

### 7.1 本次 v3.50 受影响的文件清单

**新增**
- `modules/self_evolution.py`（自我进化引擎）
- `scripts/headless_verify_v350.py`（无头 e2e 验证）

**修改**
- `gui.py`：
  - 新增「自我进化」左侧卡片（状态/进度/当前版本 + 「立即进化」「查看日志」按钮）
  - `_build_output_panel` 新增「自我进化」标签页（第 4 个 tab）
  - 新增 `_init_evolution_engine` / `_poll_evolution` / `_handle_evolution_msg` / `_on_evo_now` / `_focus_evolution_tab` / `_refresh_evolution_versions` / `_export_evolution_versions` / `_clear_evolution_log`
  - `_build_result_tab` 重构：`dash_container` 改为 `Canvas` + `dash_inner` 虚拟滚动；新增分类 ComboBox + 导出/清空/复制按钮；各区块打 `_rcat` 标记
  - **移除**「系统管理」卡片及 4 个方法：`_check_database` / `_execute_view_bayesian_result` / `_update_quick_stats` / `_clear_backtest_resume`（经引用扫描确认仅被该卡片按钮调用，删除后程序正常启动）
- `version.py`： bump `v3.49 → v3.50` + CHANGELOG 条目
- `modules/self_evolution.py`：见第 6 节修复

**移除/清理**
- 「系统管理」面板全部 UI 组件、业务逻辑、独立入口；相关引用/import/config 残留已清理（仅保留 docstring 中历史说明）。

---

## 8. 验证结论（v3.50 无头 e2e）

`/d/anaconda3/python.exe scripts/headless_verify_v350.py` 全部通过（11/11）：
1. GUI `__init__` 启动无异常（含系统管理移除 + 自我进化自动触发 + 结果显示重构）
2. 自我进化引擎启动即自动初始化
3. 自动启动轻量进化正常结束（后台非阻塞）
4. 完整进化（full=True）在真实 DB 下六阶段跑通，无字典游标（tuple/indices）错误
5. 进化版本持久化（`get_versions >= 1`，DB 表或本地 JSON 回退）
6. 分类筛选「分位信号」正确仅显示源矩阵、其余隐藏；「全部」恢复可见
7. 仪表盘区块 `_rcat` 标记完整（预测结论/分位信号/算法依据）
