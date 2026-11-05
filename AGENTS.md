# AGENTS: KPLuckyNumber 项目速查（给 AI 编码代理）

简短说明：本文件为仓库中 AI 编码代理提供可直接使用的、与代码实现紧密相关的操作性提示。

**重要提示：本系统仅通过 GUI 运行，无命令行入口。**

## 入口
- **GUI**：`main.py`（`main()`，L1008；`LotteryGUI` 主窗口 L405）
  - 双击运行或命令行执行 `python main.py`
  - 四张功能卡片：数据爬取 / 智能分析中心 / 自我进化 / 智能分析与验证
  - ★「开始分析」（智能分析中心内）六阶段统一编排：开始分析（两步流水线）+ 走势引擎 + 快速预测 + 命中率优化 + 在线学习闭环 + AI 辅助解读
  - 当前版本：**v5.1**（版本号唯一来源 `version.py` 的 `APP_VERSION` / `CHANGELOG`，release 2026-10-18）

## 关键配置
- `config.py`：全局唯一配置源
  - `DB_CONFIG`：数据库连接（host/port/user/password/database/charset）
  - `AGNES_API_CONFIG`：AI 接口配置（api_url/api_key/model_name/timeout/max_retries/...）
  - `AI_CALL_BUDGET`：AI 调用分级超时 + run 级预算熔断
  - `DB_POOL_CONFIG` / `REDIS_CONFIG` / `SPIDER_CONFIG` / `COMPOSITION_RULES` / `DATA_ASSET_CONFIG`
- `config/.env`：敏感配置（DB_PASSWORD / AGNES_API_KEY / REDIS_PASSWORD），默认空 + 环境变量注入，**已被 `.gitignore` 排除（不入库）**；模板见 `config/.env.example`
- `paths.py`：路径唯一源（`PROJECT_ROOT` 经 `__file__` 定位 / REPORTS_DIR / LOGS_DIR / PREDICTIONS_DIR / DATA_DIR）
- `version.py`：版本号与变更日志（唯一来源；CHANGELOG 43 条降序）

## 核心模块（共 46 个 .py）
- `modules/pipeline.py`（★ 推荐）：预测流水线，**两步**：①`_calc_statistical_prediction`（统计+贝叶斯）→ ②`step4_final_prediction`（入库）。原 step1/step2/step3 已注释停用。
- `modules/predictor.py`：七算法融合预测引擎 + `AdaptiveWeightManager`（冻结权重：频率0.68/监督学习0.14/贝叶斯0.10/遗漏0.06/趋势0.01/马尔可夫0.005/形态0.003/特征0.002；lookback=60）。
- `modules/database.py`：MySQL 数据层（自动建库建表 / DBUtils 连接池 / 未安装回退单连接 / 连接错误分类重连 2006/2013/lost connection）。
- `modules/database_utils.py`：数据库泛型方法（五位分位走势数据通用读写）。
- `modules/data_fetcher.py`：多源数据爬虫（P5Spider）。
- `modules/ml_predictor.py`：多源监督学习（消费位走势/升平降/和值表，按位模型；numpy/joblib/sklearn；缺失降级）。
- `modules/self_evolution.py`：★ 自我进化引擎（后台守护线程，六阶段流水线）。
- `modules/evolution_tuner.py`：深度调优器（组件缓存 + 坐标下降，用于自我进化参数搜索）。
- `modules/cache.py`：Redis 缓存（缺失时优雅旁路）。
- `modules/stats_utils.py`：★ 统计口径单一实现（`wilson_ci`，全仓唯一定义，防漂移）。
- `modules/online_learner.py`：在线学习闭环（`_calculate_hits` Top-1/Top-3 双口径分离，`partial_hits >= top1_hits` 不变量）。
- `modules/ai_analyzer.py`：AI 辅助解读（api_key 空 → `ai_available=False` + warning 不静默降级）。
- `modules/features.py`：特征工程（贝叶斯推断 log-space；指纹缓存复用）。
- `modules/task_manager.py`：线程安全任务管理（`_running_lock` / `_cancelled` 协作式取消 / 队列满返回 False）。

## 诚实边界（不可突破）
- **排列5 每期五位置独立公平摇号**，历史统计模式不改变未来开奖概率；Top-k 随机基线恒为 k/10（Top-1=10% / Top-3=30% / Top-5=50%）
- 任何「提升命中率」说法必须同时给出点估计与 Wilson 95% CI（`stats_utils.wilson_ci`）；CI 与随机基线重叠即视为**无统计显著超额**
- 命中率口径唯一权威定义：`docs/hitrate_spec.md`（ALG-06），口径变更必须走文档修订 + 单测同步，**不允许代码先行**
- 系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议

## 关键测试
- **口径对照（A34）**：`tests/test_stats_utils.py`（wilson_ci 边界/钳制/已知值/跨实现一致性 + Top-1/Top-3 双口径 + overall_score 只读钉桩 + 随机基线 k/10）
- **防泄漏**：`tests/test_no_lookahead.py`（walk-forward 窗口与 `_assert_no_leak` 断言 100% 覆盖）
- 主测试目录 `tests/`（50 文件）+ 审计专项 `scripts/tests/`（44 文件）

## 已知限制
- 本系统无命令行入口（仅 GUI）；`main.py` 有 `multiprocessing.freeze_support()`（Windows spawn 防重复导入）
- 本机 Edge 无头不可用，视觉回归靠 CSS/HTTP 冒烟（见 docs/dev_schedule_v52_20261019.md）
