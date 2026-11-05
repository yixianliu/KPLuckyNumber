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
    源码运行时通过 ``__file__`` 定位项目根目录；冻结（PyInstaller）运行时
    改用 exe 所在目录作为可写数据根（详见下方 ``IS_FROZEN`` 注释），
    确保无论从哪个工作目录启动，路径解析结果都一致且可写。
"""

import os
import sys

# ============================================================================
# 冻结（PyInstaller）与源码运行的双模式路径解析
# ----------------------------------------------------------------------------
# 源码运行：PROJECT_ROOT = 本文件所在目录（paths.py 位于项目根目录）。
# 冻结运行：PyInstaller 会把代码解包到临时/包内目录，此时 __file__ 指向
#   _internal（onedir）或 _MEIPASS 临时目录（onefile），该目录**不是**用户
#   可写的数据目录，且随程序升级被覆盖。因此：
#   - PROJECT_ROOT 改为「exe 所在目录」（便携式单目录分发的用户可写持久目录）。
#     分发要求：解压/安装到用户可写目录（不要放 Program Files），否则
#     config/.env、logs/ 等写入会失败；资源只读文件仍从 BUNDLE_ROOT 读取。
#   - BUNDLE_ROOT 指向随包只读资源目录（_MEIPASS），用于图标、配置模板等。
# ============================================================================


def _resolve_frozen_root() -> str:
    """冻结运行时的用户可写根目录：exe 所在目录（不读取任何环境变量）。"""
    return os.path.dirname(os.path.abspath(sys.executable))


# 是否为 PyInstaller 冻结环境
IS_FROZEN = bool(getattr(sys, 'frozen', False))

if IS_FROZEN:
    PROJECT_ROOT = _resolve_frozen_root()
else:
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 随包只读资源根目录（图标 / .env.example 等）；源码运行时与 PROJECT_ROOT 相同
BUNDLE_ROOT = getattr(sys, '_MEIPASS', PROJECT_ROOT)

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

# 模型缓存根目录（ml_predictor 的 joblib 指纹缓存），冻结后必须落在包外可写目录
MODEL_CACHE_ROOT = os.path.join(PROJECT_ROOT, 'models')

# ALG-07: 评估输出目录
EVAL_DIR = os.path.join(REPORTS_DIR, 'eval')
FIGURES_DIR = os.path.join(REPORTS_DIR, 'charts')