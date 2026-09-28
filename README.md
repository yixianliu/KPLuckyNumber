# 排列5 AI 智能分析系统

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![GUI](https://img.shields.io/badge/GUI-Tkinter-green)
![Version](https://img.shields.io/badge/version-v3.70-orange)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

> **多算法融合 + AI 解读 + 全链路可解释的排列5数据分析平台**  
> **诚实声明：本系统不承诺、也无法超越随机中奖基线；真实价值在于数据整合、分析透明度、结果可解释性与响应性能。**

---

## ⚠️ 核心免责声明（必读）

> **【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。请理性购彩，量力而行。**
>
> - 禁止使用"必中"、"稳赚"、"提高中奖率"、"精准预测"等误导性表述
> - 所有统计规律均为事后归纳，无法用于事前预测
> - 系统诚实标注：排列5为公平摇号，**Top-1 命中率 ≈ 10% / Top-3 ≈ 30% / Top-5 ≈ 50%（随机基线），无法稳定超越**
> - 若您询问"怎么中奖""哪组号码会中"，必须明确告知：无法预测，请理性看待彩票的随机性本质

---

## 🎯 它解决了哪些实际问题

| 实际问题 | 本软件的做法 |
|----------|--------------|
| **数据收集靠手工** | 内置多源数据爬虫，增量 / 全量一键抓取，结果自动写入数据库，告别复制粘贴 Excel |
| **分析结果不可复现** | 统一融合框架，同一份输入得到可复现、可横向对比的分析结果 |
| **预测完不知对错** | 每期开奖后自动验证、统计命中率、记录历史，长期效果看得见 |
| **AI 解读落不了地** | JSON 修复 + 逐算法归因，AI 解读稳定解析、入库、可回溯 |
| **环境缺组件就跑不起来** | 惰性加载 + 优雅降级，缺 Redis / 缺可选库也能启动主界面 |

---

## ✨ 核心价值

### 1. 多算法融合，权重透明不藏私
七大核心算法统一融合，权重公开可查、可冻结：
- **频率加权** (0.68) — 核心主信号，基于历史频率分布
- **监督学习** (0.14) — GradientBoosting 多分类，sklearn 可用时激活
- **贝叶斯推断** (0.10) — 对数空间似然累加 + softmax 归一化
- **遗漏回归** (0.06) — 仅保留低权重，实证为噪声信号
- **趋势分析** (0.01) / **马尔可夫链** (0.005) / **形态识别** (0.003) / **特征工程** (0.002) — 极低权重辅助

核心逻辑不进黑箱，`AdaptiveWeightManager` 支持权重冻结与历史回放。

### 2. 全链路可追溯
所有关键数据入库，字段均有注释：
- 历史开奖：`p5_history_data`
- 预测记录：`p5_prediction_record`（含 Top-1 真实预测力口径）
- AI 报告：`p5_ai_report`（含 `per_algo_predictions` 独立列）
- 贝叶斯结果：`p5_bayesian_result`
- 进化版本：`p5_evolution_version`
- 走势数据：`p5_{wan,qian,bai,shi,ge}_trend_data`、升平降 `p5_spjzs_data`、和值 `p5_hzzst_data`

### 3. 诚实边界，不忽悠用户
- 明确声明：排列5 为公平摇号，无法稳定超越随机基线
- 所有策略效果带 Wilson 95% 置信区间诚实披露
- walk-forward 回测 2060 次独立试验，所有策略 CI 均与随机基线重叠

### 4. 自我进化引擎（自动重训 + 无偏评估）
- 新数据积累到阈值后自动触发重训（六阶段流水线：collect→baseline→evolve→evaluate→persist→done）
- 走窗样本外评估；候选方案未超越历史基线则归档为 trial，绝不擅自改动线上融合权重
- 进程隔离：ML 子进程超时降级，主 GUI 进程不被拖垮

### 5. 响应快，不卡界面
- 三级智能缓存：LFU 长期 (500条/1h) + LRU 短期 (100条/5min) + AI 响应 (200条/5min)
- 惰性加载：重模块（matplotlib~2.7s、numpy~1.2s、bs4~1.0s）首次使用时才 import
- 耗时任务全部后台线程执行，`TaskManager` 单工作者 + 优先级队列

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        GUI (Tkinter)                            │
│  ┌─────────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │ 数据爬取     │  │ 智能分析中心      │  │ 智能分析与验证     │  │
│  │ - 增量/全量  │  │ - 六阶段统一编排  │  │ - 命中率统计       │  │
│  │ - 走势修复   │  │ - 走势引擎       │  │ - 回测分析         │  │
│  │ - 数据概览   │  │ - 快速预测       │  │ - 特征分析         │  │
│  └─────────────┘  │ - 命中率优化      │  └───────────────────┘  │
│                   │ - 在线学习闭环    │  ┌───────────────────┐  │
│                   │ - AI 辅助解读     │  │    自我进化        │  │
│                   └──────────────────┘  │ - 版本管理/日志    │  │
│                                         └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
      ┌───────────────┐ ┌─────────────┐ ┌──────────────┐
      │  MySQL 数据库  │ │   Redis     │ │  AGNES AI    │
      │  (持久化存储)  │ │  (三级缓存)  │ │  (大模型)    │
      └───────────────┘ └─────────────┘ └──────────────┘
```

### 核心模块（24 个 .py）

| 模块 | 职责 | 关键特性 |
|------|------|----------|
| `main.py` | GUI 入口 | 惰性加载、主题自适应、TaskManager |
| `modules/pipeline.py` | 预测流水线 | 两步流水线：统计预测 → 最终入库 |
| `modules/predictor.py` | 七算法融合引擎 | 冻结权重、lookback=60、贝叶斯 log-space |
| `modules/ml_predictor.py` | 监督学习 | GradientBoosting + SHAP 筛选 + 优雅降级 |
| `modules/self_evolution.py` | 自我进化 | 六阶段后台守护线程、进程隔离 |
| `modules/evolution_tuner.py` | 深度调优 | 组件缓存 + 坐标下降搜索 |
| `modules/online_learner.py` | 在线学习 | 验证闭环、逐算法归因、Top-1 评级 |
| `modules/calibration.py` | 概率校准 | 按位置独立 ε + Platt Scaling + 三闸门 |
| `modules/selection_strategy.py` | 选号策略 | 动态槽位 + 多样性约束 + A/B walk-forward |
| `modules/features.py` | 特征工程 | 位置交叉关联 + 近期形态标记 |
| `modules/trend_analyzer.py` | 走势引擎 | period=60 默认、多源融合 |
| `modules/database.py` | MySQL 操作 | 自动建表、连接池、自动重连 |
| `modules/cache.py` / `smart_cache.py` | 缓存层 | Redis + 三级智能缓存 |
| `modules/data_fetcher.py` | 多源爬虫 | 历史 + 走势 + 修复 draw_date/hot_level |
| `modules/ai_analyzer.py` | AI 解读 | Session 连接池、function call、JSON 强制 |
| `modules/backtester.py` | 回测引擎 | walk-forward + Wilson CI |
| `modules/validator.py` | 验证器 | Top-1/Top-3 双口径、严格/容错 |

---

## 🚀 快速开始

### 环境要求
- Python 3.9+
- MySQL 5.7+/8.0
- Redis 6.0+（可选，缺失时优雅降级）
- 可选：`scikit-learn`、`shap`、`matplotlib`、`pandas`（缺失时自动降级）

### 安装依赖
```bash
# 核心依赖
pip install pymysql redis requests beautifulsoup4 python-dotenv

# 可选增强（缺失不影响主流程，仅部分高级功能降级）
pip install scikit-learn shap matplotlib pandas numpy
```

### 配置文件
复制 `.env.example` 为 `.env` 并填入真实值：
```env
# 数据库（必填）
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=lucky_number

# AGNES AI（可选，用于 AI 辅助解读）
AGNES_API_KEY=your_api_key
AGNES_API_URL=https://api.agnes-ai.cn/v1/chat/completions
AGNES_MODEL_NAME=agnes-3.0-flash

# Redis（可选，缺失时使用内存缓存）
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
```

### 运行
```bash
# 方式1：双击运行
python main.py

# 方式2：命令行
cd D:\PythonProject\KPLuckyNumber
python main.py
```

> **注意：本系统仅通过 GUI 运行，无命令行入口。**

---

## 🖥️ GUI 使用指南

### 界面布局（四卡片 + 右侧标签页）

**左侧功能卡片：**
1. **数据爬取** — 增量/全量抓取、走势数据修复、数据库状态概览
2. **智能分析中心** — ★ 核心入口，「开始分析」六阶段统一编排
3. **智能分析与验证** — 历史命中率统计、回测、特征分析

**右侧标签页：**
- **运行日志** — 实时流式输出，支持搜索/清空/导出
- **自我进化** — 版本树、实时日志、指标磁贴、回滚功能

### 「开始分析」六阶段流水线
1. **统计预测** — 八算法融合生成概率分布
2. **走势引擎** — 60 期窗口多源趋势分析
3. **快速预测** — 基于融合概率的 Top-3 候选生成
4. **命中率优化** — 选号策略对照 / 概率校准状态 / 三闸门调参
5. **在线学习闭环** — 验证统计 / 自适应权重 / 归因覆盖率
6. **AI 辅助解读** — 贝叶斯后验点评 + 单次轻量预测解读

---

## 📁 目录结构

```
KPLuckyNumber/
├── main.py                 # GUI 入口（惰性加载、主题、任务管理）
├── config.py               # 全局配置（环境变量注入、单一数据源）
├── version.py              # 版本唯一来源（APP_VERSION/CHANGELOG/KNOWN_ISSUES）
├── paths.py                # 路径常量（日志/报告/预测/数据目录）
├── modules/                # 核心业务模块 (24 个 .py)
│   ├── pipeline.py         # 两步预测流水线
│   ├── predictor.py        # 七算法融合引擎
│   ├── ml_predictor.py     # 监督学习
│   ├── self_evolution.py   # 自我进化引擎
│   ├── evolution_tuner.py  # 深度调优器
│   ├── online_learner.py   # 在线学习闭环
│   ├── calibration.py      # 概率校准
│   ├── selection_strategy.py # 选号策略 + A/B 测试
│   ├── features.py         # 特征工程
│   ├── trend_analyzer.py   # 走势引擎
│   ├── database.py         # MySQL 操作
│   ├── database_utils.py   # 泛型走势读写
│   ├── data_fetcher.py     # 多源爬虫
│   ├── ai_analyzer.py      # AI 解读
│   ├── backtester.py       # 回测引擎
│   ├── validator.py        # 验证器
│   ├── smart_cache.py      # 三级智能缓存
│   ├── cache.py            # Redis 缓存
│   ├── redis_storage_manager.py
│   ├── mysql_storage_manager.py
│   ├── prediction_enhancer.py
│   ├── json_repair.py
│   ├── task_manager.py     # 任务管理器
│   ├── logging_utils.py
│   └── exceptions.py
├── scripts/                # 工具脚本
│   ├── verify/             # 端到端验证脚本
│   ├── optimize/           # 优化脚本
│   ├── backtest/           # 回测脚本
│   ├── migrate/            # 数据迁移
│   └── diagnose/           # 诊断脚本
├── tests/                  # 单元测试
├── maint/                  # 维护脚本
├── logs/                   # 运行日志
├── reports/                # AI 报告、图表
├── predictions/            # 预测导出
└── data/                   # 数据文件、changelog.json(可选覆盖)
```

---

## 🗄️ 数据库核心表结构

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `p5_history_data` | 历史开奖 | issue(主键), draw_date, n1-n5(五位号码), test_num |
| `p5_prediction_record` | 预测记录 | predict_uuid, target_issue, predicted_numbers, actual_numbers, top1_match_count, top1_accuracy_rate, verification_detail |
| `p5_ai_report` | AI 分析报告 | report_uuid, target_issue, report_content, per_algo_predictions(JSON) |
| `p5_bayesian_result` | 贝叶斯后验 | issue, prior, likelihood, posterior(JSON) |
| `p5_evolution_version` | 进化版本 | version_tag, status(active/trial/rolledback), metrics_json, parent_version |
| `p5_{wan,qian,bai,shi,ge}_trend_data` | 五位独立走势 | issue, number, frequency, hot_level, draw_date, trend_json |
| `p5_spjzs_data` | 升平降方向 | issue, pos1_direction-pos5_direction |
| `p5_hzzst_data` | 和值重心 | issue, hezhi, zx1-zx5 |

> 完整建表 DDL 见 `modules/database.py` 的 `create_tables()` 方法。

---

## 🔬 核心算法说明

### 七算法融合架构
```python
# 权重冻结基线 (v3.14 确立，后续版本维持)
weights = {
    'frequency_weighted': 0.68,      # 频率加权 - 核心主信号
    'ml_supervised': 0.14,           # 监督学习 - GBML 多分类
    'bayesian_inference': 0.10,      # 贝叶斯推断 - log-space 似然
    'omission_regression': 0.06,     # 遗漏回归 - 仅保留低权重
    'trend_analysis': 0.01,          # 趋势分析
    'markov_chain': 0.005,           # 马尔可夫链
    'pattern_recognition': 0.003,    # 形态识别
    'feature_engineering': 0.002,    # 特征工程
}
lookback_periods = 60  # 回望窗口
```

### 关键技术细节
- **贝叶斯 log-space**：`posterior = softmax(log(prior) + Σ log(likelihood))`，解决长期运行无界漂移
- **监督学习降级**：sklearn 缺失 → 纯 numpy 加权滑动频率路径
- **SHAP 筛选三级降级**：TreeExplainer → feature_importances_ → 方差回退
- **按位置独立校准**：Platt Scaling + 三闸门 keep_baseline
- **动态槽位分配**：按概率熵分配候选槽位数，集中分布减槽位、分散增槽位

---

## 📊 命中率评估体系

### 双口径并存（v3.67 引入）
| 口径 | 定义 | 基线 |
|------|------|------|
| **Top-1 真实预测力** | 首推号精确命中 (`pred[pos][0] == actual`) | 10% |
| **宽松参考口径** | 集合包含 / ±1 容错 | 30%~50% |

### 评估流程
1. **Walk-forward 样本外**：按时间顺序滚动训练/测试
2. **Wilson 95% CI**：所有策略置信区间与随机基线重叠
3. **A/B 对照框架**：`run_strategy_ab_walk_forward`，前 prob_window 期等频频率作无偏基准
4. **学习闭环 Top-1 评级**：消除宽松口径自强化误判

---

## 📈 版本历史精选

| 版本 | 日期 | 核心变更 |
|------|------|----------|
| **v3.70** | 2026-09-18 | Phase 1 全量：特征增强 + sklearn 条件激活 + SHAP + 按位校准 + 动态槽位 + A/B walk-forward + 缓存键修复 + 权重回写 |
| **v3.68** | 2026-09-16 | AGNES API 对齐官方规范：agnes-3.0-flash + Session 池 + 备份降级 |
| **v3.67** | 2026-09-15 | 命中率口径修正：新增 Top-1 真实预测力，消除宽松口径虚高 |
| **v3.60** | 2026-08-25 | ml_predictor 激活真实 GBML、贝叶斯 log-space、自我进化评估放宽、窗口统一 60 期 |
| **v3.50** | 2026-08-15 | 新增自我进化引擎 + 重构右侧结果显示 + 移除系统管理面板 |
| **v3.49** | 2026-08-09 | 多源监督学习接入 + 权重再平衡（诚实优化） |
| **v3.47** | 2026-08-07 | 工程治理：清理冗余、全量中文 docstring、同步文档 |

> 完整变更日志见 `version.py` 的 `CHANGELOG` 或 GUI「版本与更新」标签页。

---

## 🛠️ 开发与维护

### 运行测试
```bash
# 全量测试（忽略 predictor 的耗时测试）
python -m pytest tests/ -q --ignore=tests/test_predictor.py

# 单模块测试
python -m pytest tests/test_calibration.py -v
python -m pytest tests/test_features.py -v
```

### 常用维护脚本
```bash
# 端到端数据库验证
python scripts/verify/verify_e2e_db.py

# 语法检查
python scripts/tools/_verify_syntax.py

# 数据库结构检查
python maint/check_db_structure.py
```

### 删除模块前的「三向引用扫描」铁律
```bash
# 必须三向全为零才可删：
# 1. 静态 import：grep -r "from modules.xxx import"
# 2. 动态导入：grep -r "import_module.*xxx\|\__import__.*xxx"
# 3. _LazyClass 字符串：grep -r "_LazyClass.*xxx"
# 曾误删 validator.py 并从回收目录恢复，教训已固化
```

---

## 📝 更新日志外部化

支持非开发者维护更新日志：在 `data/changelog.json` 放入同结构数组，系统启动时自动优先加载。

```json
[
  {
    "version": "v3.71",
    "date": "2026-09-20",
    "summary": "新增功能描述",
    "features": ["功能1", "功能2"],
    "fixes": ["修复1"],
    "notes": ["说明1"]
  }
]
```

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/xxx`
3. 遵循 PEP8、类型注解、中文 docstring 规范
4. 通过全量测试与语法检查
5. 提交 PR，说明变更动机与验证方式

---

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。

---

## 📚 文档导航

| 文档 | 说明 | 位置 |
|------|------|------|
| **README.md** | 项目总览、快速开始、架构设计、核心算法 | 本文件 |
| **version.py** | 版本号唯一来源、完整变更日志、已知问题 | `version.py` |
| **config.py** | 全局配置项说明、环境变量映射表 | `config.py` |
| **AGENTS.md** | AI 编码代理速查：入口、配置、模块、约定、治理记录 | `AGENTS.md` |
| **modules/*.py** | 各模块顶部 docstring 含功能说明、参数、关键逻辑 | `modules/` 目录 |
| **data/changelog.json** | 可选外部更新日志（非开发者维护） | `data/` 目录 |

> **提示**：所有核心模块均已补齐中文 docstring（覆盖率 100%），可直接 `help(modules.xxx)` 或阅读源码头部注释快速上手。

---

## 🔗 相关资源

- **AGNES API 官方规范**：`D:\PythonProject\api\api.txt`
- **问题反馈**：GitHub Issues
- **技术交流**：欢迎提交 PR 或讨论

---

**排列5 AI 智能分析系统** · 数据驱动 · 诚实边界 · 技术至上

*最后更新：2026-09-18 | 版本 v3.70*