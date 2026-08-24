# KPLuckyNumber 项目文件结构说明

> 版本：v3.58 · 更新日期：2026-08-22

---

## 一、根目录文件总览

```
KPLuckyNumber/
├── main.py                  # ★ GUI 唯一入口（LotteryGUI 主类）
├── version.py               # ★ 版本号唯一来源（APP_VERSION / CHANGELOG / KNOWN_ISSUES）
├── config.py                # 全局配置（DB / AGNES API / SPIDER / REDIS）
├── paths.py                 # 输出路径集中化（REPORTS_DIR / LOGS_DIR 等）
├── requirements.txt         # Python 依赖清单
├── .env.example             # 环境变量模板（复制为 .env 使用）
├── .env                     # 本地环境变量（已忽略，含敏感信息）
├── .gitignore               # Git 忽略规则
├── favicon.ico              # 应用图标
├── README.md                # 项目说明（面向用户与开发者）
├── AGENTS.md                # AI 编码代理速查表
└── 程序说明.txt             # 面向终端用户的中文说明
```

**设计原则**：根目录仅保留启动必需的顶层文件，所有辅助代码归类至子目录。

---

## 二、核心模块目录（modules/）

共 **24 个 Python 模块**，按职责分为五组：

### 2.1 预测核心（3 个）

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 预测流水线（两步式：统计预测 → 入库；原四步已停用） |
| `predictor.py` | 七算法融合预测引擎 + AdaptiveWeightManager |
| `ml_predictor.py` | 多源监督学习（GradientBoosting，消费位走势/升平降/和值表） |

### 2.2 数据存取（4 个）

| 文件 | 职责 |
|------|------|
| `database.py` | MySQL 数据库操作（自动建库建表、自动重连） |
| `database_utils.py` | 数据库泛型方法（五位分位走势数据通用读写） |
| `data_fetcher.py` | 多源数据爬虫（P5Spider） |
| `mysql_storage_manager.py` | MySQL 存储管理（统一键前缀与 TTL） |

### 2.3 缓存层（3 个）

| 文件 | 职责 |
|------|------|
| `cache.py` | Redis 缓存（直接操作） |
| `redis_storage_manager.py` | Redis 存储管理（键前缀 `kpluckynumber:pl5:{module}:{id}`） |
| `smart_cache.py` | 三级智能缓存（LFU 长期 / LRU 短期 / AI 响应） |

### 2.4 分析与验证（7 个）

| 文件 | 职责 |
|------|------|
| `trend_analyzer.py` | 走势引擎（默认 period=40 期） |
| `features.py` | 特征工程 |
| `calibration.py` | 概率校准（三闸门 keep_baseline） |
| `selection_strategy.py` | 选号策略（被 predictor 核心链路使用） |
| `backtester.py` | 回测引擎（matplotlib 缺失则不可导入） |
| `validator.py` | 输入校验器（_LazyClass 延迟绑定） |
| `prediction_enhancer.py` | 模式挖掘与异常检测（仅追加分析字段） |

### 2.5 进化与学习（4 个）

| 文件 | 职责 |
|------|------|
| `self_evolution.py` | 自我进化引擎（六阶段流水线，后台守护线程） |
| `evolution_tuner.py` | 深度调优器（组件缓存 + 坐标下降） |
| `online_learner.py` | 在线学习引擎（验证闭环，per-algo 归因） |
| `json_repair.py` | AI 响应 JSON 修复 |

### 2.6 基础设施（3 个）

| 文件 | 职责 |
|------|------|
| `task_manager.py` | 任务管理器（单工作者线程 + 队列，异步执行耗时任务） |
| `logging_utils.py` | 日志工具（带版本信息注入） |
| `exceptions.py` | 自定义异常定义 |

---

## 三、脚本目录（scripts/）

运维、验证、调试脚本，按功能分类：

| 脚本 | 用途 |
|------|------|
| `backtest_multisource.py` | 走窗回测（诚实评估，Wilson 95% CI） |
| `bench_evo_tuning.py` | 深度调优性能基准测试 |
| `headless_verify_v350.py` | v3.50 无头端到端验证 |
| `verify_three_fixes.py` | 三 bugfix 快速自检 |
| `check_issue_lag.py` | 预测期号滞后检测 |
| `apply_optimization.py` | 优化方案应用脚本 |
| `verify_optimization.py` | 优化效果验证 |
| `test_mysql_storage.py` | MySQL 存储功能测试 |
| `test_wechat_copy.py` | 微信复制功能测试 |
| `check_constraints.py` | 数据库约束检查 |
| `enable_check_constraints.py` | 启用数据库检查约束 |
| `migrate_redis_to_mysql.py` | Redis → MySQL 数据迁移 |
| `diag_eval_offbyone.py` | off-by-one 缺陷诊断 |
| `check_mysql_version.py` | MySQL 版本检查 |
| `final_verify.py` | 最终验证脚本 |
| `TASK_COMPLETE_REPORT.md` | 任务完成报告 |
| `migrate_redis_to_mysql_README.md` | 迁移说明文档 |

---

## 四、维护脚本目录（maint/）

数据维护与修复工具，非日常运行所需：

| 脚本 | 用途 |
|------|------|
| `analyze_db_issues.py` | 数据库问题诊断分析 |
| `check_db_structure.py` | 数据库表结构检查 |
| `check_issues.py` | 通用问题检查 |
| `fill_trend_data.py` | 趋势数据补全 |
| `fix_trend_data.py` | 趋势数据修复 |

---

## 五、测试目录（tests/）

单元测试与验收测试：

| 文件 | 用途 |
|------|------|
| `test_db_fix.py` | 数据库修复测试 1 |
| `test_db_fix2.py` | 数据库修复测试 2 |

---

## 六、文档目录（docs/）

技术文档与架构分析：

| 文件 | 用途 |
|------|------|
| `architecture_analysis_v350.md` | v3.50/v3.51 架构分析与模块依赖说明 |
| `ARCHITECTURE_ASSESSMENT.md` | 架构评估报告（含改进建议与实施计划） |
| `evolution_optimization_plan.md` | 自我进化深度调优方案 |
| `optimization_multisource_v349.md` | v3.49 多源监督学习优化方案 |
| `system_architecture_v2.md` | 系统架构 v2.0 文档 |
| `algorithm_review_2026-07-13.md` | 算法与策略全面审查报告 |
| `program_guide.txt` | 程序使用指南（终端友好格式） |
| `排列5_v3.16交付纪要.md` | v3.16 交付记录 |

---

## 七、运行时生成目录

这些目录在程序运行时自动创建，无需手动维护：

| 目录 | 内容 |
|------|------|
| `data/` | 数据文件（进化版本 JSON、检查点等） |
| `reports/` | 分析报告（backtest/、diagnostic/、monitor/、charts/） |
| `predictions/` | 预测产物与权重历史 |
| `logs/` | 运行日志（各模块独立日志文件） |

---

## 八、文件引用关系图

```
main.py (GUI)
  ├── version.py          ← APP_VERSION / CHANGELOG
  ├── config.py           ← DB_CONFIG / AGNES_API_CONFIG
  ├── paths.py            ← REPORTS_DIR / LOGS_DIR 等
  │
  ├── modules/pipeline.py ★ 预测流水线
  │     └── modules/predictor.py    ★ 七算法融合
  │     ├── modules/ml_predictor.py (监督学习)
  │     ├── modules/features.py
  │     ├── modules/calibration.py
  │     └── modules/selection_strategy.py
  │
  ├── modules/database.py ★ 数据库层
  │     └── modules/database_utils.py
  │
  ├── modules/trend_analyzer.py  走势引擎
  │
  ├── modules/self_evolution.py  自我进化
  │     ├── modules/evolution_tuner.py
  │     └── modules/ml_predictor.py
  │
  ├── modules/online_learner.py  在线学习
  ├── modules/backtester.py      回测
  ├── modules/validator.py       校验
  ├── modules/smart_cache.py     智能缓存
  ├── modules/cache.py           直接Redis缓存
  ├── modules/redis_storage_manager.py
  ├── modules/mysql_storage_manager.py
  ├── modules/prediction_enhancer.py
  ├── modules/json_repair.py
  ├── modules/task_manager.py    任务调度
  └── modules/logging_utils.py   日志
```

---

## 九、设计原则总结

1. **单一真源**：版本号（version.py）、配置（config.py）、路径（paths.py）各有一个权威来源
2. **关注点分离**：modules/ 只含业务逻辑，scripts/ 含一次性运维脚本，maint/ 含数据维护工具
3. **懒加载**：重模块（predictor/backtester/validator/ml_predictor 等）通过 `_LazyClass` 延迟绑定，启动不阻塞
4. **诚实边界**：所有统计结论标注随机基线，不承诺超越
5. **可回滚**：删除模块前必须三向引用扫描（静态 import / 动态 import_module / _LazyClass 字符串）

---

*本文档版本：v3.58 · 2026-08-22*
