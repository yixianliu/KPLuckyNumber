# 排列5 AI智能分析系统

> 基于多模型融合的彩票数据分析与预测平台，整合统计分析、AI大模型与专家观点，提供全方位的排列5号码分析服务。

---

## 目录

- [项目简介](#项目简介)
- [目录结构](#目录结构)
- [安装与运行](#安装与运行)
- [功能说明](#功能说明)
- [配置说明](#配置说明)
- [命令行接口](#命令行接口)
- [GUI 界面](#gui-界面)
- [数据流架构](#数据流架构)
- [贡献指南](#贡献指南)
- [免责声明](#免责声明)

---

## 项目简介

**排列5 AI智能分析系统**是一个面向中国体育彩票"排列5"玩法的智能分析平台。系统通过多维度数据整合与混合智能分析模型，为彩票数据研究提供技术支持。

### 核心特性

- **多源数据采集**：从 55128.cn、china-lottery.cn 等多个权威数据源实时爬取开奖数据与专家文章
- **混合预测模型**：融合频率加权、遗漏回归、趋势动量、马尔可夫链、形态延续五大统计算法，并与 AI 大模型输出加权融合
- **四步流水线分析**：★ 推荐架构（v2.0），四步串行分析：文章爬取→走势分析→专家整合→最终预测
- **混合预测模型**：融合频率加权、遗漏回归、趋势动量、马尔可夫链、形态延续五大统计算法，并与 AI 大模型输出加权融合
- **双阶段 AI 分析**：第一阶段结构化整理文章内容，第二阶段整合文章分析 + 走势数据 + 历史开奖，输出综合预测报告（旧版兼容）
- **自动化回测验证**：滚动窗口回测框架，量化评估模型 Top-1 / Top-3 命中率与综合得分
- **丰富特征工程**：提取频率、012路、连号、重隔号、和值跨度等多维度统计特征
- **两级运行模式**：提供 CLI 命令行工具（适合脚本自动化）与 Tkinter 桌面 GUI（适合日常使用）
- **多级缓存体系**：Redis 临时缓存 + MySQL 持久化存储，支持数据备份与恢复

---

## 目录结构

```
KPLuckyNumber/
├── main.py                  # CLI 主入口 — 聚合所有命令行子命令
├── gui.py                   # GUI 主入口 — Tkinter 桌面应用
├── config.py                # 全局配置 — 数据库/API/爬虫/分析参数
├── requirements.txt         # Python 依赖清单
│
├── modules/                 # 核心业务模块
│   ├── four_step_pipeline.py # ★ 四步串行流水线（v2.0推荐）：文章爬取→走势→专家整合→最终预测
│   ├── database_p5.py       # 数据库层 — MySQL 连接/建表/CRUD
│   ├── redis_client.py      # 缓存层 — Redis 客户端封装与键名设计
│   ├── spider_p5.py         # 爬虫层 — 多源历史开奖数据抓取
│   ├── ydniu_spider.py      # 文章爬虫 — 亿点牛网站专家文章抓取
│   ├── optimized_p5_predictor.py  # 预测引擎 — 五算法融合 + AI 集成
│   ├── ernie_ai_analyzer.py # AI 分析器 — AGNES API 封装与 prompt 管理
│   ├── article_analyzer.py  # 文章工作流 — 6步双阶段 AI 分析流水线（旧版兼容）
│   ├── article_processor.py # 文章处理器 — 简化4步流程（爬取→AI→Redis，旧版兼容）
│   ├── backtest_engine.py   # 回测引擎 — 滚动回测与可视化报告
│   ├── feature_engineering.py   # 特征工程 — 频率/012路/连号/重隔号/和值跨度
│   ├── prediction_validator.py  # 预测验证器 — 验证记录与性能统计（GUI专用）
│   └── html_cleaner.py      # HTML 清洗器 — 网页内容清理（GUI专用）
│
├── predictions/             # 预测结果输出目录（JSON 格式）
├── reports/                 # 分析报告输出目录（JSON/TXT）
│   └── features/            # 特征分析报告子目录
├── logs/                    # 运行时日志目录
│
├── tests/                   # 测试代码
│   └── test_basic.py        # 基础功能测试
│
└── .workbuddy/              # 项目记忆与配置
    └── memory/
```

- **CLI 命令入口**：`main.py`，提供 12 个子命令支持，覆盖数据采集、预测、回测、AI分析全流程
- **GUI 桌面应用**：`gui.py`，基于 Tkinter，提供暗色主题的图形界面

---

## 四步流水线架构（全新）

从 v2.0 起，系统引入全新的**四步串行流水线分析架构**（`modules/four_step_pipeline.py`），取代原有的单阶段分析模式：

| 步骤 | 名称 | 输入 | 输出 | 存储 |
|------|------|------|------|------|
| 1 | 专家文章爬取与结构化AI分析 | 目标期号 | 每篇文章的结构化JSON分析报告 | Redis（7天过期） |
| 2 | 走势图数据分析与AI预测 | 历史走势数据 | 走势规律+冷热号+推荐号码 | Redis（7天过期） |
| 3 | 专家报告整合分析 | 步骤1的所有报告+历史数据 | 专家共识+分歧分析+综合预测 | Redis（7天过期） |
| 4 | 最终预测结果生成与入库 | 步骤2+3的报告+历史数据 | 最终预测号码+推理过程 | MySQL（永久） |

**流程优势：**
- 每步独立错误处理与降级策略，前序步骤失败仍可尝试后续步骤
- Redis中间结果可跨会话复用，无需重复分析
- AI调用从 2 次增至 4 次，分析深度显著提升
- 完整的日志追踪，可定位每步耗时与输出

---

## 安装与运行

### 前置要求

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Python | ≥ 3.9 | 推荐使用 3.12+ |
| MySQL | ≥ 5.7 | 数据存储后端 |
| Redis | ≥ 6.0 | 缓存与临时数据存储 |
| 网络连接 | 稳定 | 用于爬取数据与调用 AI API |

### 安装步骤

```bash
# 1. 克隆项目
git clone <repository-url>
cd KPLuckyNumber

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 配置数据库
# 编辑 config.py 中的 DB_CONFIG，确保 MySQL 服务正在运行

# 4. 配置 Redis
# 确保 Redis 服务在 localhost:6379 可访问，或修改 config.py 中的 Redis 连接参数

# 5. （可选）配置 AI API
# config.py 中的 AGNES_API_CONFIG 已预置 API 密钥，如需更换请自行替换
```

### 启动方式

```bash
# CLI 模式（命令行）
python main.py <command> [options]

# GUI 模式（桌面应用）
python gui.py
```

---

## 功能说明

### 1. 数据采集模块

#### spider_p5.py — 多源历史数据爬虫

从多个权威彩票网站（55128.cn、china-lottery.cn）爬取排列5历史开奖数据与走势图。

**核心能力：**
- **多源备份**：同一数据从多个网站源获取，某源失效时可自动切换
- **数据校验**：自动验证期号格式、号码范围（0-9）、和值（0-45）、跨度（0-9）
- **增量爬取**：仅获取数据库未包含的新期数据，避免重复
- **智能重试**：指数退避 + 随机 UA 轮换，应对反爬策略
- **走势图解析**：专门处理中华彩讯网站的 JavaScript 变量混淆（`window.__NUXT__`）

**支持的数据源：**

| 数据源 | URL | 数据类型 |
|--------|-----|----------|
| 55128 | www.55128.cn | 历史开奖 + 走势图 |
| 中华彩讯 | m.china-lottery.cn | 基础走势 + 万/千/百/十/个位独立走势 + 和尾 + 后三 |
| 彩吧指南 | cpzhixun.com | 历史开奖 + 走势图 |

#### ydniu_spider.py — 专家文章爬虫

从亿点牛网站爬取彩票专家预测文章，提取文章内容、推荐号码、分析观点等结构化信息。

### 2. 数据库与缓存

#### database_p5.py — MySQL 数据持久化

**表结构：**

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `p5_history` | 历史开奖数据 | issue, numbers, hezhi, span, draw_date |
| `p5_trend` | 走势图数据 | issue, wan/qian/bai/shi/ge, odd_even_ratio, big_small_ratio |
| `p5_position_trend` | 各位置独立走势 | issue, wan_number/qian_number/..., omission, hot_level |
| `p5_ai_reports` | AI分析报告 | report_uuid, report_content, next_issue, risk_warning |
| `p5_prediction_records` | 预测记录 | predict_uuid, target_issue, fused_probabilities |
| `p5_prediction_verification` | 预测验证记录 | verification_status, accuracy_rate, match_count |

**关键方法：**
- `batch_insert(data)` — 批量插入历史数据
- `get_history_data(limit, order)` — 获取历史开奖数据
- `insert_ai_report(**fields)` — 保存 AI 分析报告
- `get_latest_ai_report()` — 获取最新 AI 报告

#### redis_client.py — Redis 缓存层

**键名设计规范：**

```
kpluckynumber:pl5:raw:{issue}           # 原始开奖数据
kpluckynumber:pl5:article:{article_id}  # 文章数据（7天过期）
kpluckynumber:pl5:ai:{issue}            # AI分析结果（7天过期）
kpluckynumber:pl5:expert:{name}         # 专家数据（3天过期）
kpluckynumber:pl5:trend_ai:{issue}      # 走势AI分析（7天过期）
kpluckynumber:pl5:combined:{issue}      # 综合分析（14天过期）
kpluckynumber:pl5:issue_articles:{issue}# 期号文章索引（Set）
```

**核心能力：**
- 有序集合（ZSET）维护数据访问时序
- 灵活的 TTL 过期策略（按数据类型差异化设置）
- 数据备份/恢复功能（JSON 格式序列化）

### 3. 预测引擎

#### optimized_p5_predictor.py — 多算法融合预测

**五大统计算法：**

| 算法 | 权重 | 原理 |
|------|------|------|
| frequency_weighted | 25% | 基于历史出现频率 + 拉普拉斯平滑 |
| omission_regression | 20% | 指数衰减遗漏回归模型 |
| trend_momentum | 20% | 线性回归趋势方向 + 高斯衰减 |
| markov_transition | 20% | 一阶/二阶马尔可夫状态转移 |
| pattern_continuation | 15% | 奇偶/大小/质合形态延续规律 |

**AI 模型集成：**
- 通过 `AGNES_API_CONFIG` 调用 AGNES AI 模型（默认 agnes-2.0-flash）
- AI 输出与统计模型加权融合（默认 AI 权重 40%，统计权重 60%）
- 响应解析容错：从第一个 `{` 到最后一个 `}` 提取 JSON 对象

**配置项（OptimizedP5PredictorConfig）：**

```python
DEFAULT_CONFIG = {
    'algorithms': {
        'frequency_weighted': {'enabled': True, 'weight': 0.25, 'params': {...}},
        'omission_regression': {'enabled': True, 'weight': 0.20, 'params': {...}},
        # ... 其余算法
    },
    'global': {
        'hot_threshold_percentile': 70,
        'cold_threshold_percentile': 30,
        'combination_count': 10,
        'position_top_n': 3,
        'enable_ai_model': True,
        'ai_model_weight': 0.4,
        'max_hot_ratio': 0.6,
        'min_cold_ratio': 0.1,
    }
}
```

**返回结构（必须兼容的字段）：**

```json
{
  "fused_probabilities": [...],   // 5 × 10 概率分布
  "top_combinations": [...],      // 推荐组合列表
  "predict_time": "...",          // 预测时间
  "predict_uuid": "...",          // 预测唯一标识
  "risk_warning": "..."           // 风险提示
}
```

### 4. AI 分析模块

#### ernie_ai_analyzer.py — AGNES AI 深度分析

- 封装 AGNES API 调用（POST `/v1/chat/completions`）
- 支持综合分析师模式：整合历史数据 + 走势图 + 趋势数据
- 解析 AI 返回的 JSON 格式预测报告并持久化到数据库

#### article_analyzer.py — 文章分析工作流（双阶段 AI）

**6步完整流水线：**

| 步骤 | 操作 | 数据流向 |
|------|------|----------|
| 1 | 爬取文章 | YDNiuSpider → 原始文章内容 |
| 2 | 第一次 AI 分析 | 原始文本 → 结构化 JSON |
| 3 | Redis 存储 | AI 结果 → Redis（7天过期） |
| 4 | Redis 加载 | Redis → 内存字典 |
| 5 | 第二次 AI 分析 | Redis数据 + 历史数据 → 综合预测 |
| 6 | 数据库存储 | 最终报告 → MySQL |

**3步新版流水线（GUI 中使用）：**

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 爬取文章 + 逐篇 AI 分析 | 获取 30 篇专家文章 → 逐篇结构化 → 存入 Redis |
| 2 | 走势图 AI 分析 | 最近 30 期走势数据 → AI 分析走势规律 → 存入 Redis |
| 3 | 最终整合分析 | 文章 AI + 走势 AI + 历史数据 → 综合预测报告 → 存 DB + 生成 TXT/JSON |

### 5. 特征工程

#### feature_engineering.py — 多维统计特征提取

| 特征类别 | 提取内容 |
|----------|----------|
| frequency | 各位置频率、热号/温号/冷号分级 |
| road_012 | 各位置 0 路/1 路/2 路比例 |
| consecutive | 连号出现统计（平均/最大/出现率） |
| repeat | 重号率 / 隔号率 / 无重复率 |
| sum_span | 和值范围/均值、跨度范围/均值 |
| omission | 遗漏值统计、回归概率 |

### 6. 回测引擎

#### backtest_engine.py — 历史回测与可视化

**三种回测模式：**

| 模式 | 说明 |
|------|------|
| compare | 对比新旧模型综合得分、Top-1/Top-3 命中率改善 |
| old | 仅测试旧模型性能 |
| new | 仅测试新模型性能 + 可视化图表 |

**回测指标：**
- Top-1 命中率（第一位推荐命中）
- Top-3 命中率（前三位推荐中至少一个命中）
- 综合得分（加权评分）
- 完全猜中次数（5 位全中）
- 概率校准得分

### 7. 预测验证

#### prediction_validator.py — 自动验证与性能报告

- 自动比对预测号码与实际开奖结果
- 计算准确率、完全匹配率、平均偏离度
- 生成性能评估报告

---

## 配置说明

### config.py — 全局配置

```python
# 数据库配置
DB_CONFIG = {
    'host': 'localhost',       # MySQL 主机
    'port': 3306,              # MySQL 端口
    'user': 'root',            # 用户名
    'password': 'root',        # 密码
    'database': 'lucky_number', # 数据库名
    'charset': 'utf8mb4'       # 字符集
}

# AI API 配置
AGNES_API_CONFIG = {
    'api_url': 'https://apihub.agnes-ai.com/v1/chat/completions',
    'api_key': '<YOUR_API_KEY>',   # AGNES AI API 密钥
    'model_name': 'agnes-2.0-flash',  # 模型名称
    'timeout': 60,                   # 请求超时（秒）
    'temperature': 0.7,              # 采样温度
    'max_tokens': 2048               # 最大输出 Token 数
}

# 爬虫配置
SPIDER_CONFIG = {
    'pages': 10,             # 爬取页数
    'timeout': 15,           # 请求超时（秒）
    'retry_count': 3,        # 重试次数
    'delay_min': 3,          # 请求间隔最小值（秒）
    'delay_max': 6           # 请求间隔最大值（秒）
}

# 分析配置
ANALYSIS_CONFIG = {
    'confidence_threshold': 0.8,   # 置信度阈值
    'min_data_count': 100          # 最小数据量要求
}

# 报告配置
REPORT_CONFIG = {
    'output_dir': 'reports/',  # 输出目录
    'chart_format': 'png',     # 图表格式
    'chart_dpi': 100           # 图表 DPI
}
```

### 预测器配置（可在 optimized_p5_predictor.py 中调整）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| hot_threshold_percentile | 70 | 热号百分位阈值 |
| cold_threshold_percentile | 30 | 冷号百分位阈值 |
| combination_count | 10 | 推荐组合数量 |
| position_top_n | 3 | 每位取 Top-N 号码 |
| enable_ai_model | True | 是否启用 AI 模型 |
| ai_model_weight | 0.4 | AI 模型融合权重 |
| max_hot_ratio | 0.6 | 最大热号比例（防极端） |
| min_cold_ratio | 0.1 | 最小冷号比例（保多样性） |

---

## 命令行接口

### 基本用法

```bash
python main.py <command> [options]
```

### 命令列表

| 命令 | 说明 | 选项 |
|------|------|------|
| `update` | 更新并入库最新开奖数据 | 无 |
| `predict` | 预测下一期号码 | `--model optimized\|old` |
| `backtest` | 历史回测验证 | `--mode compare\|old\|new`<br>`--start <N>` `--count <M>` |
| `analyze` | 分析历史数据特征 | 无 |
| `ernie` | AI 深度分析 | `--limit <N>`（期数） |
| `comprehensive` | 综合分析（二次深度） | `--limit <N>` |
| `article` | 文章分析工作流 | `--issue <期号>` `--limit <N>` |
| `save-articles` | 批量保存文章到 Redis | `--issue` `--max <N>` `--no-extract` |
| `process-article` | 处理单篇文章 | `--url <URL>` `--title <标题>` |
| `process-articles` | 批量处理文章 | `--max <N>` |
| `pipeline` | 四步流水线分析 | `--issue <期号>` `--limit <N>` |

### 使用示例

```bash
# 更新最新开奖数据
python main.py update

# 运行预测
python main.py predict --model optimized

# 执行回测对比
python main.py backtest --mode compare --start 50 --count 50

# AI 深度分析（最近30期）
python main.py ernie --limit 30

# 完整文章分析工作流
python main.py article --issue 2026165 --limit 30

# 批量处理文章（最多10篇）
python main.py process-articles --max 10
# 四步流水线分析（默认推算下一期）
python main.py pipeline

# 指定期数+数据量
python main.py pipeline --issue 2026166 --limit 50
```

---

## GUI 界面

启动桌面应用：

```bash
python gui.py
```

### 功能面板

| 面板 | 功能按钮 | 说明 |
|------|----------|------|
| **数据爬取** | 增量爬取 / 全量爬取 | 从多源抓取历史开奖数据 |
| **AI智能分析** | 执行AI分析 / 历史回测 / 特征分析 | 核心三步骤流水线 |
| **预测验证** | 验证预测 / 性能报告 | 自动比对与评估 |
| **系统操作** | 数据库检测 / 更新统计 / 清空输出 | 运维辅助 |

### 界面特性

- 暗色主题（基于 Tailwind CSS 色板）
- 异步任务管理（后台线程执行，UI 不阻塞）
- 实时进度条与状态栏
- 模块化输出面板（日志/报告）

---

## 数据流架构

### 四步流水线架构（v2.0 新增）

```
                    ┌─────────────┐
                    │  数据源层    │
                    │ 55128.cn    │
                    │ china-lottery│
                    │ ydniu.com   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼─────┐ ┌───▼──────┐ ┌───▼──────────┐
     │ 步骤1: 文章  │ │ 步骤2:   │ │ 步骤3:       │
     │ 爬取+AI分析  │ │ 走势分析 │ │ 专家整合分析 │
     │              │ │          │ │              │
     │ Redis存储    │ │ Redis存储│ │ Redis存储    │
     └──────┬───────┘ └────┬─────┘ └───┬──────────┘
            │              │           │
            │         ┌────┼───────────┘
            │         │  步骤4: 最终预测
            │         │  整合+入库
            │         ▼
     ┌──────────────────────┐
     │   MySQL: p5_ai_report │
     └──────────────────────┘
```

### 传统架构（legacy）

                        ┌─────────────┐
                        │  数据源层    │
                        │ 55128.cn    │
                        │ china-lottery│
                        │ ydniu.com   │
                        └──────┬──────┘
                               │
                    ┌──────────▼──────────┐
                    │   数据采集层         │
                    │ Spider / Article    │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐ ┌─────▼──────┐ ┌───────▼───────┐
     │  数据持久化层  │ │ 缓存层     │ │ 文章暂存层    │
     │  MySQL        │ │  Redis     │ │  Redis        │
     │  history/trend│ │ article/ai │ │  7天过期      │
     └────────┬──────┘ └─────┬──────┘ └───────┬───────┘
              │              │                │
              │     ┌────────┼────────┐       │
              │     │        │        │       │
┌─────────────▼─────┐ ┌────▼─────┐ ┌▼──────────────┐
│  统计分析引擎      │ │ AI 分析  │ │ 文章分析工作流 │
│ OptimizedP5Predict│ │ Analyzer │ │ ArticleAnalyzer│
│ 五大算法融合       │ │ AGNES    │ │ 双阶段AI       │
└─────────┬─────────┘ └────┬─────┘ └───────┬───────┘
          │                │                │
          │     ┌──────────┼────────────────┤
          │     │          │                │
┌─────────▼────▼────┐ ┌───▼────────┐  ┌────▼──────────┐
│  回测与验证引擎    │ │ 特征工程    │  │ 报告输出层     │
│ BacktestEngine    │ │ FeatureEng  │  │ TXT/JSON/DB   │
│ ValidationEngine  │ │             │  │               │
└───────────────────┘ └────────────┘  └───────────────┘
```

---

## 贡献指南

### 代码规范

1. **模块导入**：所有外部依赖（数据库/Redis/AI）采用**延迟导入（Lazy Import）**模式，在函数内部导入以避免环境依赖导致的加载失败
2. **日志输出**：各模块需写入 `logs/<module_name>.log`，使用标准 `logging` 模块
3. **错误处理**：关键操作必须 try-except 包裹，记录日志后优雅降级，不抛出未处理异常
4. **JSON 解析容错**：AI 返回结果必须通过 `_parse_ai_response()` 提取 JSON，不直接 `json.loads()` 全文

### 变更检查清单

- [ ] 新模块是否包含完整 docstring
- [ ] 数据库变更是否向后兼容（不删除已有列）
- [ ] 预测器返回结构是否保持兼容字段（`fused_probabilities`, `top_combinations` 等）
- [ ] Redis 键名是否遵循 `kpluckynumber:pl5:` 前缀规范
- [ ] 配置文件变更是否更新文档

### 开发流程

1. Fork 本项目
2. 创建功能分支：`git checkout -b feature/my-feature`
3. 提交更改：`git commit -m "feat: add my feature"`
4. 推送到分支：`git push origin feature/my-feature`
5. 提交 Pull Request

---

## 免责声明

> **⚠️ 重要声明**
>
> 本系统仅供数据研究与技术交流使用，**不构成任何投资或购彩建议**。
>
> 1. 彩票开奖本质上是随机事件，任何历史数据统计分析和 AI 预测模型**无法保证中奖结果**
> 2. 本系统输出的一切预测、推荐、分析仅供参考，使用者需自行判断并承担风险
> 3. 请理性购彩，量力而行，切勿沉迷
> 4. 本系统开发者不对任何因使用本系统而产生的损失承担责任

---

## 许可证

本项目仅供个人学习与技术研究使用。

---

## 快速参考卡

| 操作 | 命令 |
|------|------|
| 更新数据 | `python main.py update` |
| 预测号码 | `python main.py predict --model optimized` |
| 分析特征 | `python main.py analyze` |
| AI分析 | `python main.py ernie --limit 30` |
| 文章分析 | `python main.py article --issue 2026165` |
| 回测验证 | `python main.py backtest --mode compare` |
| 启动GUI | `python gui.py` |
