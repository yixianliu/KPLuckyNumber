# AGENTS: KPLuckyNumber 项目速查（给 AI 编码代理）

简短说明：本文件为仓库中 AI 编码代理提供可直接使用的、与代码实现紧密相关的操作性提示。

**重要提示：本系统仅通过 GUI 运行，无命令行入口。**

## 入口
- **GUI**：`D:\PythonProject\KPLuckyNumber\main.py`
  - 双击运行或命令行执行 `python main.py`
  - 四个功能卡片：数据爬取 / 智能分析中心 / 智能分析与验证
  - ★「开始分析」（智能分析中心内）六阶段统一编排：开始分析（两步流水线）+ 走势引擎 + 快速预测 + 命中率优化 + 在线学习闭环 + AI 辅助解读（v3.42 将原独立的「在线学习引擎」「命中率优化引擎」卡片融合进来）
  - 当前版本：**v3.59**（版本号唯一来源 `version.py` 的 `APP_VERSION` / `CHANGELOG`）

## 关键配置
- `D:\PythonProject\KPLuckyNumber\config.py`
  - `DB_CONFIG`：数据库连接（host/user/password/database）
  - `AGNES_API_CONFIG`：AI 接口配置
- `D:\PythonProject\KPLuckyNumber\version.py`：版本号与变更日志（唯一来源）

## 核心模块（共 24 个 .py）
- `modules/pipeline.py`（★ 推荐）：预测流水线，**当前为两步**（v3.15 起简化）：①`_calc_statistical_prediction`（统计+贝叶斯）→ ②`step4_final_prediction`（入库）。原 step1 专家文章抓取 / step2 走势报告 / step3 综合报告 已注释停用，`pipeline_state` 中 `expert_article_report`/`trend_report`/`integrated_report` 在简化流水线恒为 None（属预期，相关告警已降级为 debug）。
- `modules/predictor.py`：七算法融合预测引擎 + `AdaptiveWeightManager`（冻结权重：频率0.68/监督学习0.14/贝叶斯0.10/遗漏0.06/趋势0.01/马尔可夫0.005/形态0.003/特征0.002；lookback=60）。
- `modules/database.py`：MySQL 数据库操作（自动建库建表、自动重连）。
- `modules/database_utils.py`：数据库泛型方法（五位分位走势数据通用读写）。
- `modules/data_fetcher.py`：多源数据爬虫（P5Spider）。
- `modules/ml_predictor.py`：v3.49 新增，多源监督学习（消费位走势/升平降/和值表，按位 GradientBoosting；SKLEARN 缺失降级）。
- `modules/self_evolution.py`：★ v3.50 新增，自我进化引擎（后台守护线程，六阶段流水线）。
- `modules/evolution_tuner.py`：深度调优器（组件缓存 + 坐标下降，用于自我进化参数搜索）。
- `modules/cache.py`：Redis 缓存。
- `modules/redis_storage_manager.py`：Redis 存储管理（键前缀 `kpluckynumber:pl5:{module}:{id}`，TTL 7-90d 统一管理）。
- `modules/mysql_storage_manager.py`：MySQL 存储管理。
- `modules/smart_cache.py`：三级智能缓存（LFU 长期 / LRU 短期 / AI 响应）。
- `modules/prediction_enhancer.py`：模式挖掘与异常检测（仅追加分析字段，不改预测）。
- `modules/online_learner.py`：在线学习引擎（由「开始分析」自动验证闭环统一调用，v3.42 不再有独立卡片；per-algo 归因优先读 `p5_ai_report.per_algo_predictions` 独立列）。
- `modules/features.py`：特征工程。
- `modules/calibration.py`：概率校准（三闸门 keep_baseline）。
- `modules/selection_strategy.py`：选号策略（被 predictor 核心链路使用，保留）。
- `modules/trend_analyzer.py`：走势引擎（默认 period=40 期）。
- `modules/backtester.py`：回测引擎（顶层 import matplotlib，缺失则无法 import 本模块）。
- `modules/json_repair.py`：AI 响应 JSON 修复。
- `modules/validator.py`：输入校验器（**经 `_LazyClass` 延迟绑定，删除前务必做引用扫描，见下**）。
- `modules/task_manager.py`：任务管理器（单工作者线程 + 队列）。
- `modules/logging_utils.py`：日志工具（带版本信息注入）。
- `modules/exceptions.py`：自定义异常定义。

## 项目约定
- 日志路径：`D:\PythonProject\KPLuckyNumber\logs\*.log`
- 预测输出：`D:\PythonProject\KPLuckyNumber\predictions\`
- AI 报告：`D:\PythonProject\KPLuckyNumber\reports\`
- Redis key 前缀：`kpluckynumber:pl5:`
- 预测器返回必须包含：`fused_probabilities`, `top_combinations`, `predict_time`, `predict_uuid`, `risk_warning`
- 保持懒加载模式（模块内部导入，避免启动时加载）
- ⚠️ **删除模块前的「三向引用扫描」铁律**：GUI 用 `_LazyClass('modules.x', 'X')` 延迟绑定重模块（如 `main.py:136 Validator = _LazyClass('modules.validator','Validator')`）。仅用 `grep "from modules.x import"` 会把这类模块误判为"零引用"而误删，导致运行时 `ImportError`。删除任何模块前必须用三向扫描确认：**静态 import / 动态 `import_module`·`__import__` / `_LazyClass` 字符串** 全部为零才可删（参考可复用 Skill `py-project-cleanup-docstrings`，其 `scripts/scan_references.py` 即做此扫描）。曾误删 `validator.py` 并已从回收目录恢复，教训已固化。

## 命中率基线（诚实声明）
- Top-1 命中率 ≈ 10%（随机基线）
- Top-3 命中率 ≈ 30%（随机基线）
- Top-5 命中率 ≈ 50%（随机基线）
- 系统诚实标注：排列5为公平摇号，无法稳定超越随机基线

## 工程治理记录（v3.47）
- 清理：删除零引用模块 `hitrate_tracker.py`；删除缓存目录 / 过期日志 / 空 SQLite / 回测断点缓存（经 `_LazyClass` 三向扫描确认零引用后，用 `shutil.move` 进 `.cleanup_trash_20260807` 回收目录再永久删除）。
- 注释：全量中文 docstring 覆盖率 100%（694 函数 / 27 类，AST 校验），覆盖 gui.py + 18 模块 + 顶层文件。
- 文档对齐：README.md 顶部标注 v3.47、重写 18 模块目录树、两步流水线架构、真实数据库表名、命中率按位置口径 + v3.46 Top-3 展示、删除模块前检查清单。
- 可复用 Skill：`py-project-cleanup-docstrings`（用户级 `~/.workbuddy/skills/`）沉淀了「三向引用扫描 + 回收目录安全删除 + AST docstring 注入（幂等/CRLF兼容/装饰器感知）」流程。

—— 结束 ——
