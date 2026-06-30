# KPLuckyNumber Project Memory

## Project Overview
排列5 AI智能分析系统 — 基于多模型融合的彩票数据分析与预测平台。

## Key Decisions
- 采用延迟/懒加载模式加载所有外部依赖（数据库/Redis/AI），避免导入失败
- AI响应解析统一使用 `{` 到 `}` 区间提取 JSON，兼容非纯净返回
- Redis 键名统一使用前缀 `kpluckynumber:pl5:`
- 预测器返回必须包含 `fused_probabilities`, `top_combinations`, `predict_time`, `predict_uuid`, `risk_warning` 字段
- 所有日志写入 `logs/*.log`

## Module Map
- `modules/spider_p5.py` — 多源历史开奖爬虫（55128/china-lottery/cpzhiXun）
- `modules/ydniu_spider.py` — 亿点牛文章爬虫
- `modules/optimized_p5_predictor.py` — 五算法融合预测引擎 + AI集成
- `modules/ernie_ai_analyzer.py` — ERNIE/千帆 AI 深度分析（保留）
- `modules/article_analyzer.py` — 6步双阶段AI文章分析流水线（旧版兼容）
- `modules/article_processor.py` — 简化4步流程（旧版兼容）
- `modules/backtest_engine.py` — 历史回测框架
- `modules/feature_engineering.py` — 多维特征提取
- `modules/prediction_validator.py` — 预测验证（GUI专用）
- `modules/html_cleaner.py` — HTML清洗（GUI专用）
- `modules/database_p5.py` — MySQL CRUD层
- `modules/redis_client.py` — Redis缓存封装
- `modules/four_step_pipeline.py` — ★ 四步串行流水线（v2.0推荐核心）：文章爬取→走势分析→专家整合→最终预测入库
