# 排列5 AI 智能分析系统

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![GUI](https://img.shields.io/badge/GUI-Tkinter-green)
![Version](https://img.shields.io/badge/version-v5.1-orange)
![License](https://img.shields.io/badge/license-BSD--3--Clause-lightgrey)

> **多算法融合 + AI 解读 + 全链路可解释的排列5数据分析平台**
> **诚实声明：本系统不承诺、也无法超越随机中奖基线；真实价值在于数据整合、分析透明度、结果可解释性与响应性能。**

---

## ⚠️ 核心免责声明（必读）

> **【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。请理性购彩，量力而行。**
>
> - 禁止使用「必中」「稳赚」「提高中奖率」「精准预测」等误导性表述
> - 所有统计规律均为事后归纳，无法用于事前预测
> - 系统诚实标注：排列5为公平摇号，**Top-1 命中率 ≈ 10% / Top-3 ≈ 30% / Top-5 ≈ 50%（随机基线），无法稳定超越**（口径唯一权威定义见 `docs/hitrate_spec.md`）
> - 若您询问「怎么中奖」「哪组号码会中」，必须明确告知：无法预测，请理性看待彩票的随机性本质

---

## 🚀 快速开始

### 环境要求
- Python 3.9+
- MySQL 8.x（本系统数据层依赖，自动建库建表）
- Redis（可选，缺失时优雅降级，缓存自动旁路）

### 安装
```bash
# 1. 克隆或解压到本地
cd KPLuckyNumber

# 2. 安装依赖（requirements.txt 已覆盖运行期全部硬依赖）
pip install -r requirements.txt

# 3. 配置敏感参数（数据库密码 / AI 密钥，绝不留存默认值）
#    编辑 config/.env（从 config/.env.example 复制模板，填真实值）
copy config\.env.example config\.env

# 4. 启动 GUI
python main.py
```

> **配置诚实边界**：`config.py` 为全局唯一配置源，敏感密钥（DB_PASSWORD / AGNES_API_KEY / REDIS_PASSWORD）默认空值，经环境变量或 `config/.env` 注入，**绝不硬编码**。`.env` 已被 `.gitignore` 排除（真实密钥不入库），`.env.example` 为占位符模板。

---

## 🎯 它解决了哪些实际问题

| 实际问题 | 本软件的做法 |
|----------|--------------|
| **数据收集靠手工** | 内置多源数据爬虫（`modules/data_fetcher.py`），增量 / 全量一键抓取，结果自动写入数据库，告别复制粘贴 Excel |
| **分析结果不可复现** | 统一融合框架，同一份输入得到可复现、可横向对比的分析结果 |
| **预测完不知对错** | 每期开奖后自动验证、统计命中率、记录历史，长期效果看得见 |
| **AI 解读落不了地** | JSON 修复 + 逐算法归因（`modules/json_repair.py` + `modules/ai_analyzer.py`），AI 解读稳定解析、入库、可回溯 |
| **环境缺组件就跑不起来** | 惰性加载（`main.py` `_LazyClass`）+ 优雅降级，缺 Redis / 缺可选库也能启动主界面 |

---

## ✨ 核心价值

### 1. 多算法融合，权重透明不藏私
七大核心算法统一融合（`modules/predictor.py`），权重公开可查、可冻结（频率 0.68 / 监督学习 0.14 / 贝叶斯 0.10 / 遗漏 0.06 / 趋势 0.01 / 马尔可夫 0.005 / 形态 0.003 / 特征 0.002；lookback=60）。

### 2. 全链路可解释
- 特征工程：`modules/features.py`（指纹缓存复用，v5.2 ALG-04）
- 在线学习闭环：`modules/online_learner.py`（Top-1 真实口径 / Top-3 覆盖口径分离）
- 回测与命中率评估：`modules/backtester.py`（`overall_score` 只读钉桩）+ `modules/stats_utils.py`（Wilson CI 单一实现）
- 自我进化引擎：`modules/self_evolution.py`（后台守护线程，六阶段流水线）

### 3. 诚实口径守卫（v5.1 核心）
- **Wilson 95% 置信区间单一实现**：`modules/stats_utils.py::wilson_ci`（全仓唯一定义，防口径漂移）
- **Top-1 / Top-3 双口径分离**：`modules/online_learner.py::_calculate_hits`，`partial_hits >= top1_hits` 不变量
- **命中率基线对照可视化**：每次输出附随机基线对照 + 95% Wilson CI，误差带透明

---

## 🗂 目录结构（v5.1）

```
KPLuckyNumber/
├── main.py                  # GUI 启动入口（main()，LotteryGUI 主窗口）
├── version.py               # 版本号唯一来源（APP_VERSION=v5.1 + CHANGELOG + KNOWN_ISSUES）
├── config.py                # 全局唯一配置源（DB_CONFIG / AGNES_API_CONFIG / ... 集中声明）
├── paths.py                 # 路径唯一源（PROJECT_ROOT / REPORTS_DIR / LOGS_DIR / ...）
├── requirements.txt         # 运行期依赖（numpy / scikit-learn / joblib 已补齐）
├── config/
│   ├── .env                 # 敏感配置（已被 .gitignore 排除，不入库）
│   └── .env.example         # 占位符模板（your_db_password_here / your_agnes_api_key_here）
├── gui/
│   ├── scheduler.py         # SchedulerMixin（任务调度 / 六阶段统一编排）
│   ├── dashboard.py         # DashboardMixin（仪表盘 / _dash_diff 增量差分）
│   └── panels/output_panel.py  # OutputPanelMixin（输出面板 / 结果标签页）
├── modules/                 # 46 个核心模块（全模块级 docstring 100%）
│   ├── pipeline.py          # ★ 预测流水线（两步：统计+贝叶斯 → 入库）
│   ├── predictor.py         # 七算法融合预测引擎 + 权重冻结
│   ├── database.py          # MySQL 数据层（自动建库建表 / 连接池回退 / 分类重连）
│   ├── data_fetcher.py      # 多源数据爬虫（P5Spider）
│   ├── ml_predictor.py      # 多源监督学习（numpy / joblib / sklearn）
│   ├── self_evolution.py    # 自我进化引擎（后台守护线程）
│   ├── evolution_tuner.py   # 深度调优器（组件缓存 + 坐标下降）
│   ├── stats_utils.py       # 统计口径单一实现（wilson_ci，全仓唯一）
│   ├── ai_analyzer.py       # AI 辅助解读（密钥缺失降级不静默）
│   └── task_manager.py      # 线程安全任务管理（_running_lock / 队列满 False）
├── docs/
│   ├── hitrate_spec.md      # ★ 命中率口径唯一权威定义（ALG-06）
│   ├── dev_schedule_v52_20261019.md   # v5.2 开发排期
│   └── project_upgrade_plan_20261019_013100.md  # v5.2 升级实施方案
├── tests/                   # 主测试目录（50 个文件，含口径对照 A34）
├── scripts/tests/           # 审计专项测试目录（44 个文件）
└── reports/                 # 审计报告 / 回测 / 诊断产物
```

---

## 🧪 测试

```bash
# 主测试目录（文档口径对照 A34 在此）
python -m pytest tests/ -q

# 审计专项测试目录
python -m pytest scripts/tests/ -q
```

> **诚实边界**：`docs/hitrate_spec.md` 的每条口径均有 `tests/test_stats_utils.py` 断言对照（wilson_ci 边界/钳制/已知值、Top-1/Top-3 双口径、`partial_hits >= top1_hits` 不变量、overall_score 只读钉桩、随机基线 k/10）。

---

## 📄 版本与变更

版本号**唯一来源** `version.py`（`APP_VERSION` / `CHANGELOG` / `KNOWN_ISSUES`），GUI「版本与更新」标签页展示。CHANGELOG 从新到旧降序（v5.1 → 最早），支持 `data/changelog.json` 外部覆盖。变更记录见 `version.py` 内置 CHANGELOG（43 条）。

---

## 🛡 许可证

本项目基于 **BSD-3-Clause** 许可证分发（详见 `LICENSE`）。

---

## 诚实声明（不可突破）

- 排列5 每期五位置**独立公平摇号**，历史上任何统计模式都不改变未来开奖概率
- 本系统所有分析仅供娱乐与学术研究，**不构成任何购彩建议**
- 任何「提升命中率」说法必须同时给出点估计与 Wilson 95% CI；CI 与随机基线重叠即视为**无统计显著超额**
- 系统默认自动触发自我进化引擎（后台线程），但**不增强、不承诺超越随机基线**的预测能力
