# KPLuckyNumber 项目全面架构评估与提升方案

> **评估时间**: 2026-08-19  
> **项目版本**: v3.51 (version.py) / v3.52 (self_evolution.py 重建版本)  
> **评估范围**: 全栈架构、性能、可扩展性、安全性、数据流转、代码质量

---

## 1. 项目全貌速览

### 1.1 系统定位
排列五彩票历史数据统计、概率建模、模拟预测与可视化分析系统。**核心立场**: 明确声明彩票开奖为完全独立随机事件，所有统计分析与模拟仅供娱乐与学术研究，不构成购彩建议，诚实标注无法稳定超越随机基线（Top-1≈10%、Top-3≈30%、Top-5≈50%）。

### 1.2 技术栈
- **语言**: Python 3.9+ (Anaconda 环境，含 scikit-learn)
- **GUI**: Tkinter (单进程单线程 + TaskManager 线程池)
- **数据库**: MySQL 8.0 (lucky_number schema, 22张表)
- **缓存**: Redis (三级智能缓存: LFU长期/LRU短期/AI响应) + 本地文件缓存
- **AI集成**: AGNES API (agnes-2.5-flash, JSON修复机制)
- **ML**: scikit-learn GradientBoostingClassifier (ml_predictor.py)
- **可视化**: matplotlib (Agg 后端)

### 1.3 核心模块架构 (22个 .py 文件，约 2.5万行)

| 层级 | 模块 | 行数 | 核心职责 |
|------|------|------|----------|
| **入口** | `main.py` | 5900+ | Tkinter GUI、四功能卡片、TaskManager、_LazyClass惰性代理、六阶段编排 |
| **配置** | `config.py` | ~100 | DB_CONFIG、AGNES_API_CONFIG、SPIDER_CONFIG 全局唯一配置源 |
| **数据层** | `database.py` | 3700+ | 22表CRUD、连接/重连/自动建库/补列、幂等写入、去重验证统计 |
| | `database_utils.py` | 260 | 5位走势表泛型读写、批量插入优化 |
| | `data_fetcher.py` | 3540 | 多源爬虫(55128/cpzhixun/ydniu)、NUXT解析、GraphQL、增量爬取、专家爬取 |
| **缓存层** | `cache.py` | 925 | Redis客户端封装、6大业务存取、备份恢复、兼容RedisKeyManager |
| | `smart_cache.py` | ~400 | 三级缓存编排(LFU/LRU/AI)、装饰器模式ResultCache |
| | `redis_storage_manager.py` | ~300 | Key命名规范、TTL策略、健康检查、清理机制 |
| **预测核心** | `predictor.py` | 2400+ | 8算法融合引擎、AdaptiveWeightManager(双信号EWMA)、贝叶斯推断、组合生成v3 |
| | `pipeline.py` | 4900+ | 两步式流水线(统计预测→最终预测)、多源融合、缓存复用、附加步骤(验证/学习/回测/特征) |
| **算法增强** | `ml_predictor.py` | 400 | 多源监督学习(GradientBoosting)、98维特征、懒加载优雅降级 |
| | `features.py` | 1135 | 5大类特征(基础/高级/时序/交叉/贝叶斯)、互信息重要性评估 |
| | `trend_analyzer.py` | 379 | 独立走势引擎、6信号源、诚实口径relative_hotness |
| **校准/选号** | `calibration.py` | ~400 | 温度缩放/均匀收缩、黄金分割搜索ε、免重跑拟合(断点复用) |
| | `selection_strategy.py` | ~600 | 配额分配(Hamilton)、互质步长错位轮转、5策略对比、覆盖优化 |
| **验证/回测** | `backtester.py` | 1100 | Walk-forward滚动验证、断点续跑、多维评估(Top-1/3/5、Brier Score、校准分) |
| | `validator.py` | 400 | 预测验证、按位置命中率、性能报告生成 |
| **AI集成** | `ai_analyzer.py` | 846 | AGNES API调用、鲁棒JSON解析、4类报告、per_algo_predictions独立列 |
| | `json_repair.py` | 171 | 4级兜底JSON修复(平衡大括号/单引号/裸key/Python常量/ast.literal_eval) |
| **进化/优化** | `self_evolution.py` | 1500+ | 六阶段流水线、后台守护线程、检查点续跑、DB版本持久化、联动状态 |
| | `evolution_tuner.py` | 535 | **深度调优核心**: 组件缓存+坐标下降，O(候选×窗口×重训)→O(窗口×重算+候选×重融合) |
| **辅助** | `prediction_enhancer.py` | ~330 | 模式挖掘(冷热/连号/间隔/周期)、异常检测(和值/奇偶/连号缺失) |
| | `version.py` | ~50 | 版本号与变更日志唯一来源 |

---

## 2. 数据库结构与数据分布分析

### 2.1 表清单 (lucky_number schema, 22张表)

| 表名 | 行数 | 核心字段 | 说明 |
|------|------|----------|------|
| `p5_history_data` | 1,041 | issue(PK), wan-qian-bai-shi-ge, hezhi, span, draw_date | 核心历史开奖数据，按期号唯一索引 |
| `p5_prediction_record` | 1,175 | report_uuid, target_issue, predicted_numbers, verification_status | 预测记录，verification_status: pending/verified |
| `p5_ai_report` | 218 | report_uuid, report_type, trend_analysis, per_algo_predictions | AI分析报告，含各算法独立预测(归因用) |
| `p5_bayesian_result` | 38 | issue, target_issue, bayes_json, top_numbers_json | 贝叶斯后验概率专用表，增量复用 |
| `p5_evolution_version` | 22 | version_tag, status(active/trial/rolledback), params_json, metrics_json | 自我进化版本归档 |
| `p5_artifact` | 214 | artifact_type, issue, ref_uuid, data_json, meta_json | 运行时产物统一存储(回测报告/提议等) |
| `p5_wan/qian/bai/shi/ge_trend_data` | ~1,058 each | issue, number, omission, hot_level, consecutive_count | 5位独立走势表 |
| `p5_spjzs_data` | 540 | issue, miss_json | 升平降方向遗漏 |
| `p5_hzzst_data` | 540 | issue, miss_json | 和值/和尾遗漏 |
| `p5_trend_data` | - | 基础走势表 | 综合走势 |
| `p5_back_three_trend_data` | - | 后三位走势 | |
| `p5_sum_end_trend_data` | - | 和尾走势 | |
| `p5_verification_detail` | 5,270+ | prediction_id, position, predicted_top3, actual, is_hit_top1/3/5 | 逐位验证明细 |
| `p5_performance_stats` | - | 统计汇总表 | 日度性能统计 |
| `p5_learning_history` | - | 学习历史 | 在线学习轨迹 |
| `p5_weight_history` | - | 权重历史 | 自适应权重演进 |
| `p5_expert_recommendation` | - | 专家推荐 | 爬虫采集专家数据 |

### 2.2 数据流转机制
```
[数据源] → data_fetcher.py(爬虫) → [22张表]
                            ↓
[历史数据] ← database.py(get_history_data) ← predictor/pipeline/backtester
                            ↓
[预测流水线] pipeline.run_four_step_pipeline()
  step1: 统计预测 → _calc_statistical_prediction (频率+贝叶斯)
  step2: 最终预测 → step4_final_prediction (融合+走势+约束组合+入库)
  附加: 验证闭环 → 预测验证 → 在线学习 → 回测 → 特征分析
                            ↓
[结果产出] → p5_prediction_record / p5_ai_report / p5_artifact / p5_bayesian_result
                            ↓
[验证闭环] validator.verify_all_pending() → p5_verification_detail → performance_stats
                            ↓
[自我进化] SelfEvolutionEngine → 深度调优 → p5_evolution_version → 联动注入预测器
```

### 2.3 关键数据质量观察
- **历史数据**: 1041期 (2023年至今)，基本完整，最新期 2026220 (2026-08-18)
- **预测记录**: 1175条，大部分验证完成，最近1条 pending (2026221)
- **进化版本**: 22个版本，**仅1个 active (evo-20260815-185331)**，其余 rolledback，**metrics_json 多为空或 {}**，最新4个版本有指标 (tested=8, Top1=5%, Top3=20%, Top5=45%)
- **贝叶斯结果**: 仅38条，top_numbers_json 近期固定为 `[3,7,9,0,3]` 疑似未更新
- **走势表**: 5位表 ~1058条，个位表仅1041条(与历史数据一致)，其他位多17条可能含脏数据

---

## 3. 系统性评估矩阵

### 3.1 架构设计 (⭐⭐⭐⭐☆ 4/5)

| 维度 | 评分 | 说明 |
|------|------|------|
| **模块化分层** | ⭐⭐⭐⭐⭐ | 数据/缓存/预测/验证/进化清晰分离，职责单一 |
| **懒加载模式** | ⭐⭐⭐⭐⭐ | 7大重量级模块 _LazyClass 代理，启动快、依赖解耦 |
| **流水线编排** | ⭐⭐⭐⭐ | 两步式简化(v3.15+)，附加步骤插件化，但 pipeline.py 4900行过大 |
| **缓存架构** | ⭐⭐⭐⭐⭐ | 三级缓存(LFU/LRU/AI)+Redis命名规范+TTL分级+原子写入 |
| **配置管理** | ⭐⭐⭐ | config.py 集中但硬编码分散(权重/阈值在 predictor/config/pipeline 多处) |
| **版本演进** | ⭐⭐⭐ | 版本表设计完善但状态管理混乱(多active/rolledback、metrics缺失) |

**核心问题**: 
- `main.py` (5900行) / `pipeline.py` (4900行) / `predictor.py` (2400行) **单文件过大**，违反单一职责
- 进化版本状态不一致：同时存在多个 `active`，历史版本未正确归档
- `_LazyClass` 机制虽好但增加了静态分析难度，删除模块需三向扫描

### 3.2 性能表现 (⭐⭐⭐☆☆ 3/5)

| 维度 | 现状 | 瓶颈 |
|------|------|------|
| **启动速度** | 优 | 惰性加载避免重模块启动时导入 |
| **单次预测延迟** | ~3-5秒 | predictor._run_algorithms 8算法串行、ml_predictor 子进程启动开销 |
| **回测速度** | 50期~10-30分钟 | 逐期重训 GBM、贝叶斯AI辅助限流、单线程串行 |
| **数据库查询** | 一般 | 无连接池、频繁 connect/disconnect、N+1查询模式 |
| **缓存命中** | 好 | 三级缓存设计完善，但 Redis 连接无池化 |
| **GUI响应** | 差 | TaskManager 单线程池，长任务阻塞主线程，进度回调150ms轮询 |

**量化数据** (源自 backtester.py 注释):
- 回测单期 ~40-70秒 (含贝叶斯AI辅助)
- 全量50期回测将卡死GUI数十分钟
- WF_MAX_TRAIN=10 限制 walk-forward 训练组数
- ml_predictor 子进程池 maxtasksperchild=50 防内存泄漏

### 3.3 可扩展性 (⭐⭐⭐☆☆ 3/5)

| 维度 | 评价 | 改进建议 |
|------|------|----------|
| **新算法接入** | 中等 | predictor._run_algorithms 需手工注册，建议插件化注册表 |
| **新数据源** | 良好 | data_fetcher 模块化，新增 Spider 子类即可 |
| **新特征** | 良好 | features.py 类方法式，易扩展 |
| **新选号策略** | 优 | selection_strategy 策略模式，新增策略函数即可 |
| **分布式部署** | 差 | 单进程架构、SQLite式本地缓存、无状态水平扩展能力 |
| **配置外部化** | 差 | 关键参数硬编码，需代码变更才能调整 |

### 3.4 安全性 (⭐⭐☆☆☆ 2/5)

| 风险点 | 严重度 | 现状 |
|--------|--------|------|
| **AI API Key 明文** | 🔴 高 | config.py 硬编码 `sk-nXcJVCLluIbranCCHGO9MIrmBFvhRl5E4goKMrsVt0F0fkFm` |
| **数据库密码明文** | 🔴 高 | config.py 硬编码 `root/root` |
| **SQL注入风险** | 🟡 中 | 部分 f-string 拼接表名(如 `f'SELECT * FROM {table}'`)，虽表名受控但隐患存在 |
| **子进程崩溃传播** | 🟡 中 | ml_predictor 子进程隔离但异常处理仅返回 None，静默失败 |
| **文件路径遍历** | 🟢 低 | paths.py 统一管理，但报告生成用用户可控 timestamp |
| **输入验证** | 🟢 低 | validator.py 存在但覆盖面有限 |

### 3.5 代码质量与维护性 (⭐⭐⭐☆☆ 3/5)

| 指标 | 现状 | 问题 |
|------|------|------|
| **类型注解** | 部分 | 核心模块有，但部分旧代码缺失 |
| **Docstring覆盖** | 优 | AGENTS.md 称 100% (694函数/27类 AST校验) |
| **单元测试** | ❌ 无 | 完全缺失，重构无安全网 |
| **循环复杂度** | 高 | predictor._run_algorithms、pipeline.execute_pipeline、main._execute_unified_analysis 超长函数 |
| **死代码/注释代码** | 存在 | pipeline.py step1/2/3 大段注释、predictor 多算法冻结权重 |
| **魔法数字** | 多 | 权重 0.68/0.14/0.10...、阈值 3.0/0.7/60... 分散硬编码 |

---

## 4. 核心瓶颈识别

### 4.1 性能瓶颈 Top 5
1. **GUI 单线程阻塞** - TaskManager 单线程池，回测/爬虫/进化等长任务冻结界面
2. **数据库连接开销** - 无连接池，每次操作 connect/disconnect，高频调用下延迟累积
3. **ML 子进程启动开销** - ml_predictor 每次预测 spawn 子进程，回测逐期重训极慢
4. **流水线串行执行** - pipeline 8算法顺序执行，可并行的独立算法未并行
5. **贝叶斯 AI 辅助限流** - 回测期间 API 调用串行，成为长杆

### 4.2 架构债务 Top 5
1. **巨石模块** - main/pipeline/predictor 三大文件占总代码 50%+，耦合度高
2. **配置分散** - 算法权重、lookback、阈值分散在 config/predictor/pipeline/self_evolution
3. **版本管理混乱** - p5_evolution_version 22条记录状态不一致，无清晰发布流程
4. **缺失测试体系** - 无单测/集成测试，回归风险极高
5. **错误处理不统一** - 部分模块吞异常返回空/None，部分抛出，调试困难

### 4.3 功能缺口
- **无实时监控告警** - Redis健康检查仅手工调用，无自动告警
- **无增量回测** - 每次全量回测，无法增量更新最新期验证
- **专家爬虫脆弱** - NUXT 混淆 JS 解析依赖 _find_matching_brace，极易失效
- **导出格式单一** - 仅 TXT/数据库，缺 Excel/HTML/JSON 结构化导出
- **多期预测不支持** - 仅单期预测，无连续多期滚动预测能力

---

## 5. 具体可行提升方案

### 5.1 性能优化 (高优先级)

#### P1-1: GUI 异步化重构 (预计收益: 界面零卡顿)
```python
# 方案: 引入 asyncio + 线程池分离 + 消息总线
# main.py 拆分: GUI层(仅渲染) + 任务调度层 + 业务层
# 使用 concurrent.futures.ThreadPoolExecutor(max_workers=4)
# 长任务提交到线程池，通过 queue.Queue 回传进度/结果
# 进度回调改为事件驱动而非 150ms 轮询
```

#### P1-2: 数据库连接池化 (预计收益: DB操作延迟降低 60%+)
```python
# database.py 引入 PooledDB (DBUtils) 或 mysql.connector.pooling
# 连接池大小: min=2, max=10
# 复用连接，去除 connect/disconnect 开销
# 事务边界明确化，支持批量操作
```

#### P1-3: ML 预测器预热与模型复用 (预计收益: 回测加速 5-10×)
```python
# evolution_tuner 已有组件缓存模式，推广到 ml_predictor
# 训练模型持久化到磁盘(joblib)，回测时仅加载增量更新
# walk-forward 窗口滑动仅增量 1 期样本，支持 warm_start 增量训练
# 移除 subprocess 隔离，改用线程池 + 共享内存模型 (需验证 sklearn 线程安全)
```

#### P1-4: 算法并行化 (预计收益: 单次预测 3-5秒 → 1-2秒)
```python
# predictor._run_algorithms 8算法相互独立 → ThreadPoolExecutor 并行
# 需注意: ml_supervised 子进程隔离、贝叶斯 AI 辅助串行依赖
# 其余 6 个统计算法 (频率/遗漏/趋势/马尔可夫/形态/特征) 完全可并行
```

#### P1-5: 回测增量化 (预计收益: 日常回测从分钟级→秒级)
```python
# backtester 增加 incremental 模式
# 仅回测「上次回测结束期号」到「最新已开奖期号」的增量区间
# 复用历史窗口组件缓存 (evolution_tuner 已有模式)
```

### 5.2 架构改进 (中优先级)

#### P2-1: 巨石模块拆分 (技术债偿还)
| 模块 | 拆分建议 | 目标文件数 |
|------|----------|------------|
| `main.py` | `gui_main.py`(入口) + `gui_panels/`(4卡片) + `task_manager.py` + `lazy_loader.py` | 8-10 |
| `pipeline.py` | `pipeline_core.py`(编排) + `pipeline_steps/`(step1-4+附加) + `pipeline_cache.py` | 6-8 |
| `predictor.py` | `predictor_core.py` + `algorithms/`(8算法各文件) + `fusion.py` + `combination.py` | 12-15 |

#### P2-2: 配置外部化与版本化
```python
# 新增 config/ 目录
# config/
#   ├── database.yaml      # DB连接(支持环境变量覆盖)
#   ├── ai.yaml            # AGNES API (Key从环境变量/密钥管理读取)
#   ├── algorithms.yaml    # 8算法权重/开关/参数
#   ├── backtest.yaml      # 回测参数
#   ├── evolution.yaml     # 进化参数
#   └── logging.yaml       # 日志配置
# 引入 pydantic Settings 管理，支持热重载
```

#### P2-3: 进化版本发布流程规范化
```python
# 状态机: draft → evaluating → trial → active / rejected / rolledback
# 同一时刻仅允许 1 个 active 版本
# 发布需满足: beat_baseline=True + metrics完整 + 人工确认(或自动阈值)
# 归档策略: 保留最近 20 个 trial + 所有 active/rolledback
```

#### P2-4: 插件化算法注册表
```python
# predictor/algorithms/registry.py
ALGORITHM_REGISTRY = {
    'frequency_weighted': FrequencyWeightedAlgo(),
    'omission_regression': OmissionRegressionAlgo(),
    # ...
}
# 新算法仅需实现 AlgorithmBase 接口并装饰 @register_algorithm
# pipeline 自动发现，无需修改核心代码
```

### 5.3 功能增强 (中优先级)

#### P3-1: 多期滚动预测与组合优化
```python
# 新增 MultiPeriodPredictor
# 支持: 预测未来 N 期、滚动更新、组合去重、投注成本控制
# 集成 selection_strategy 的 weighted_coverage 策略
```

#### P3-2: 结构化报告导出 (Excel/HTML/JSON)
```python
# 复用 mcp_Excel / pandas / jinja2
# 导出模板: 回测报告、对比报告、预测记录、进化版本对比
# 支持定时自动生成并归档到 p5_artifact
```

#### P3-3: 实时监控仪表板 (复用现有数据)
```python
# 指标: 预测延迟、回测命中率趋势、缓存命中率、DB连接池状态、AI调用成功率/费用
# 存入 p5_artifact(type=monitoring) 或独立 monitoring 表
# 复用 matplotlib/seaborn 生成趋势图
```

#### P3-4: 专家爬虫重构 (降级策略)
```python
# 现状: 依赖 NUXT 混淆 JS 解析，极不稳定
# 方案: 
#   1. 引入 playwright 无头浏览器渲染获取数据 (稳健但重)
#   2. 维护多源降级链: 官方API → 备用站点 → 缓存历史专家数据
#   3. 专家数据标记置信度，预测融合时按置信度加权
```

### 5.4 资源配置与安全优化 (高优先级)

#### P4-1: 敏感配置外部化 (必须修复)
```bash
# 方案 A: 环境变量 (最简)
export AGNES_API_KEY="sk-xxx"
export DB_PASSWORD="xxx"

# config.py 读取:
AGNES_API_CONFIG = {
    'api_key': os.getenv('AGNES_API_KEY', ''),
    ...
}

# 方案 B: 密钥管理 (生产推荐)
# 使用 keyring / HashiCorp Vault / AWS Secrets Manager
```

#### P4-2: 依赖锁定与环境复现
```bash
# 生成 requirements-lock.txt (pip freeze)
# 新增 pyproject.toml (PEP 621) 管理依赖分组
# [project.optional-dependencies]
# gui = ["tkinter"]  # 系统自带
# ml = ["scikit-learn", "joblib", "numpy", "pandas"]
# ai = ["requests", "openai"]
# dev = ["pytest", "black", "mypy", "pre-commit"]
```

#### P4-3: CI/CD 流水线 (GitHub Actions / GitLab CI)
```yaml
# .github/workflows/ci.yml
# - 代码风格检查 (black/ruff/mypy)
# - 单元测试 (pytest + coverage)
# - 集成测试 (TestContainers MySQL + Redis)
# - 安全扫描 (bandit/safety)
# - Docker 镜像构建 (可选)
```

#### P4-4: 观测性完善 (日志/指标/追踪)
```python
# 统一结构化日志: structlog + JSON 格式
# 关键路径埋点: 预测耗时、各算法耗时、DB查询耗时、AI调用耗时/Token消耗
# Prometheus metrics 暴露: /metrics 端点
# 分布式追踪: OpenTelemetry (可选，单体暂不需要)
```

---

## 6. 实施路线图

### Phase 1: 安全与稳定性 (第 1-2 周) 🔴 **必须优先**
| 任务 | 工作量 | 验收标准 |
|------|--------|----------|
| 敏感配置外部化 (API Key/DB密码) | 1天 | 配置文件无明文密钥，环境变量生效 |
| 数据库连接池引入 | 2天 | 连接复用，connect/disconnect 调用减少 90%+ |
| 关键路径异常处理统一 | 3天 | 统一错误码、结构化日志、无静默吞异常 |
| 版本号统一 (version.py 单一真源) | 0.5天 | 所有版本显示一致 |

### Phase 2: 性能突破 (第 3-5 周) 🟠 **高收益**
| 任务 | 工作量 | 预期收益 |
|------|--------|----------|
| GUI 异步化重构 (TaskManager → asyncio/线程池) | 5天 | 界面零卡顿，长任务可取消/暂停 |
| ML 模型持久化与增量训练 | 4天 | 回测 50期 30分钟 → 3分钟 |
| 算法并行执行 (ThreadPoolExecutor) | 2天 | 单次预测 5秒 → 1.5秒 |
| 回测增量模式 | 2天 | 日常回测 分钟级 → 秒级 |

### Phase 3: 架构治理 (第 6-9 周) 🟡 **技术债偿还**
| 任务 | 工作量 | 交付物 |
|------|--------|--------|
| main/pipeline/predictor 拆分 | 10天 | 模块化代码库，单文件 < 800行 |
| 配置外部化 (YAML + pydantic) | 3天 | config/ 目录，热重载支持 |
| 算法插件化注册表 | 3天 | 新算法零侵入接入 |
| 进化版本状态机规范化 | 2天 | 单一 active，清晰发布流程 |

### Phase 4: 质量保障 (第 10-12 周) 🟢 **长期护城河**
| 任务 | 工作量 | 目标 |
|------|--------|------|
| 单元测试框架搭建 (pytest + fixtures) | 3天 | 核心模块覆盖率 > 80% |
| 集成测试 (TestContainers) | 3天 | 关键流程端到端验证 |
| CI/CD 流水线 | 2天 | 每提交自动测试/扫描 |
| 文档站生成 (Sphinx/MkDocs) | 2天 | API文档/架构文档在线化 |

### Phase 5: 功能增强 (持续迭代) 🔵 **价值创新**
- 多期滚动预测、结构化报告导出、监控仪表板、专家爬虫重构

---

## 7. 资源配置建议

### 7.1 硬件资源 (当前单机部署)
| 资源 | 当前 | 建议 | 理由 |
|------|------|------|------|
| **CPU** | 未知 | 8核+ | 并行算法/回测多进程受益 |
| **内存** | 未知 | 16GB+ | 模型缓存、walk-forward 窗口数据、Redis |
| **磁盘** | 未知 | SSD 100GB+ | 数据库、缓存、模型文件、报告归档 |
| **网络** | 本地 | 低延迟访问 AGNES API | AI调用超时控制在 60s 内 |

### 7.2 软件依赖版本锁定 (建议)
```
python>=3.9,<3.13
pymysql>=2.1
redis>=5.0
requests>=2.31
beautifulsoup4>=4.12
numpy>=1.24
pandas>=2.0
scikit-learn>=1.3  # ml_predictor 依赖
matplotlib>=3.7
pydantic>=2.5      # 配置管理
pyyaml>=6.0
DBUtils>=3.0       # 连接池
joblib>=1.3        # 模型持久化
structlog>=24.1    # 结构化日志
pytest>=7.4        # 测试框架
```

### 7.3 运维建议
- **备份策略**: 每日全量备份 MySQL (mysqldump)、Redis RDB/AOF、模型文件
- **日志轮转**: logs/ 目录按天轮转，保留 30 天，ERROR 单独文件保留 90 天
- **监控告警**: 
  - DB 连接池使用率 > 80% 告警
  - AI API 调用失败率 > 10% 告警
  - 回测任务超时 > 2小时 告警
  - 磁盘使用率 > 85% 告警

---

## 8. 诚实边界与风险提示 (必须遵守)

> 【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。请理性购彩，量力而行。

本评估报告基于代码静态分析与数据库现状，**不代表系统具备预测能力**。所有性能指标（命中率、校准分、进化版本指标）均为**历史拟合结果**，受随机波动影响大，**无法用于事前预测**。提升方案旨在改善**工程质量、系统性能、可维护性**，而非提高中奖概率。

---

## 9. 附录: 关键文件清单

### 9.1 必读核心文件 (按依赖序)
1. `config.py` - 全局配置唯一来源
2. `version.py` - 版本号真源
3. `modules/database.py` - 数据库核心操作
4. `modules/predictor.py` - 预测引擎核心
5. `modules/pipeline.py` - 流水线编排
6. `modules/selection_strategy.py` - 选号策略
7. `modules/calibration.py` - 概率校准
8. `modules/backtester.py` - 回测引擎
9. `modules/self_evolution.py` - 自我进化引擎
10. `modules/evolution_tuner.py` - 深度调优核心
11. `modules/ml_predictor.py` - 监督学习
12. `modules/features.py` - 特征工程
13. `modules/ai_analyzer.py` - AI集成
14. `modules/main.py` - GUI入口

### 9.2 关键数据库表
- `p5_history_data` (1041期) - 核心训练数据
- `p5_prediction_record` (1175条) - 预测记录与验证状态
- `p5_evolution_version` (22版本) - 进化版本归档
- `p5_artifact` (214条) - 运行时产物统一存储
- `p5_verification_detail` (5270+条) - 逐位验证明细

### 9.3 关键配置参数 (建议外部化)
| 参数 | 当前值 | 位置 | 建议 |
|------|--------|------|------|
| 算法权重 | 频率0.68/监督0.14/贝叶斯0.10/遗漏0.06/趋势0.01/马尔可夫0.005/形态0.003/特征0.002 | predictor.py AdaptiveWeightManager | algorithms.yaml |
| lookback | 60 | predictor/config | algorithms.yaml |
| 贝叶斯AI辅助回测上限 | 10 | backtester.py | backtest.yaml |
| WF_MAX_TRAIN | 10 | evolution_tuner.py | evolution.yaml |
| 缓存TTL | 7-90天分级 | redis_storage_manager.py | cache.yaml |

---

**文档版本**: v1.0  
**维护者**: KPLuckyNumber 架构评估组  
**下次评估**: 建议每季度或重大版本发布后