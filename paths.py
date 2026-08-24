"""
路径配置模块（全局唯一路径源）

职责：
    集中定义整个「排列5 AI 智能分析系统」的输出目录常量，供各业务模块通过
    ``from paths import ...`` 统一引用，避免在代码中硬编码散落的路径字符串。

目录结构：
    reports/
        backtest/    - 回测结果与断点缓存
        diagnostic/  - 诊断报告
        monitor/     - 监控状态
        charts/      - 生成的图表
    logs/           - 各类日志文件
    predictions/    - 预测结果与权重历史

使用方式：
    from paths import PROJECT_ROOT, REPORTS_DIR, LOGS_DIR, ...

说明：
    本模块通过 ``__file__`` 定位项目根目录，确保无论从哪个工作目录启动脚本，
    路径解析结果都一致。
"""

import os

# 项目根目录：本文件所在目录（paths.py 位于项目根目录）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# ============ 输出目录 ============
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
LOGS_DIR = os.path.join(PROJECT_ROOT, 'logs')
PREDICTIONS_DIR = os.path.join(PROJECT_ROOT, 'predictions')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')

# ============ 子目录 ============
REPORTS_BACKTEST_DIR = os.path.join(REPORTS_DIR, 'backtest')
REPORTS_DIAGNOSTIC_DIR = os.path.join(REPORTS_DIR, 'diagnostic')
REPORTS_MONITOR_DIR = os.path.join(REPORTS_DIR, 'monitor')
REPORTS_CHARTS_DIR = os.path.join(REPORTS_DIR, 'charts')

# ============ 日志文件 ============
LOG_AI_ANALYZER = os.path.join(LOGS_DIR, 'ernie_ai_analyzer.log')
LOG_CACHE = os.path.join(LOGS_DIR, 'redis_client.log')
LOG_DATABASE = os.path.join(LOGS_DIR, 'database_p5.log')
LOG_DATA_FETCHER = os.path.join(LOGS_DIR, 'data_fetcher.log')
LOG_PIPELINE = os.path.join(LOGS_DIR, 'pipeline.log')
LOG_PREDICTOR = os.path.join(LOGS_DIR, 'optimized_p5_predictor.log')
LOG_VALIDATOR = os.path.join(LOGS_DIR, 'prediction_validator.log')
LOG_GUI_RUN = os.path.join(LOGS_DIR, 'gui_run.log')

# ============ 其他固定路径 ============
FREEZE_BASELINE_PATH = os.path.join(REPORTS_BACKTEST_DIR, 'freeze_baseline_v315.json')
