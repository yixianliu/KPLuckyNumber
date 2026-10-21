"""
配置文件（全局唯一配置源）

职责：
    集中存放整个「排列5 AI 智能分析系统」所需的运行期配置，供各业务模块通过
    ``from config import XXX`` 的方式统一读取，避免在代码中硬编码散落的魔法数。

主要配置一览：
    - ``DB_CONFIG``           ：MySQL 数据库连接参数（host/port/user/password 等）。
    - ``DB_POOL_CONFIG``      ：数据库连接池参数（最小/最大连接数、阻塞等待）。
    - ``AGNES_API_CONFIG``    ：AGNES 大模型 API（v1/chat/completions）的地址与鉴权信息，
                                供 ``ai_analyzer`` 等模块调用 AI 分析能力。
    - ``SPIDER_CONFIG``       ：网页爬虫的并发页数与重试/延时策略，供 ``web_scraper`` /
                                ``data_fetcher`` 使用。
    - ``REDIS_CONFIG``        ：Redis 缓存连接参数，供 ``cache.py``/``redis_storage_manager.py`` 使用。

与其它模块的关系：
    - 数据库连接由 ``database.py`` 消费 ``DB_CONFIG`` 与 ``DB_POOL_CONFIG``。
    - AI 分析请求由 ``modules/ai_analyzer.py`` 消费 ``AGNES_API_CONFIG``。
    - 爬虫参数由 ``modules/web_scraper.py`` / ``modules/data_fetcher.py`` 消费 ``SPIDER_CONFIG``。
    - Redis 连接由 ``modules/cache.py`` / ``modules/redis_storage_manager.py`` 消费 ``REDIS_CONFIG``。

说明：
    早期版本曾存在 ``QIANYAN_API_CONFIG``（百度千帆，已废弃）、``ANALYSIS_CONFIG``、
    ``PREDICTOR_CONFIG``、``REPORT_CONFIG`` 等配置块；经全仓库 grep 确认它们均未被活跃代码
    引用（预测器改用自身的 ``P5PredictorConfig``，分析/报告配置无调用方），已于本版本删除，
    配置保持单一数据源，避免「改了 config 却无效」的问题。

安全性说明：
    敏感配置（数据库密码、API Key）通过环境变量注入，禁止在代码中硬编码。
    本地开发请复制 .env.example 为 .env 并填入真实值。
    生产环境请使用密钥管理系统或容器环境变量注入。
"""

import os
from pathlib import Path

# 尝试加载 .env 文件（可选依赖，不强制要求 python-dotenv）
try:
    from dotenv import load_dotenv
    # 依次尝试项目根目录 .env 和 config/.env（后者为当前项目实际位置）
    _root = Path(__file__).parent
    for _env_path in [_root / '.env', _root / 'config' / '.env']:
        if _env_path.is_file():
            load_dotenv(_env_path, override=False)
            break
except Exception:
    # python-dotenv 未安装或 .env 不存在，忽略，使用系统环境变量
    pass


def _get_env(key: str, default: str = '') -> str:
    """获取环境变量，支持默认值"""
    return os.getenv(key, default)


def _get_env_int(key: str, default: int = 0) -> int:
    """获取整型环境变量"""
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _get_env_bool(key: str, default: bool = False) -> bool:
    """获取布尔型环境变量"""
    val = os.getenv(key, str(default)).lower()
    return val in ('1', 'true', 'yes', 'on')


# ============ 数据库配置 ============
DB_CONFIG = {
    'host': _get_env('DB_HOST', 'localhost'),
    'port': _get_env_int('DB_PORT', 3306),
    'user': _get_env('DB_USER', 'root'),
    'password': _get_env('DB_PASSWORD', ''),  # 生产环境必须通过环境变量注入
    'database': _get_env('DB_NAME', 'lucky_number'),
    'charset': _get_env('DB_CHARSET', 'utf8mb4'),
}

# ============ 数据库连接池配置 ============
# 生产环境建议 max_connections=20~30，避免 ThreadPoolExecutor 并发时连接饥饿
DB_POOL_CONFIG = {
    'min_connections': _get_env_int('DB_POOL_MIN_CONNECTIONS', 2),
    'max_connections': _get_env_int('DB_POOL_MAX_CONNECTIONS', 24),
    'blocking': _get_env_bool('DB_POOL_BLOCKING', True),
}

# ============ AGNES API 配置 ============
# 依据 D:\PythonProject\api\api.txt 中的官方技术规范统一对齐：
#   - 端点：https://api.agnes-ai.cn/v1/chat/completions
#   - 默认模型：agnes-3.0-flash（api.txt 推荐版本，可经环境变量切换）
#   - 认证：Authorization: Bearer <api_key>
#   - 请求体字段：model / messages / max_tokens / temperature / (可选)tools / stream / top_p
#   - 支持 OpenAI 兼容的 function call（tools 数组），供 ai_analyzer._call_ai_model 透传使用。
#
# 生产环境必须通过环境变量 AGNES_API_KEY 注入密钥；本段不硬编码任何密钥。
AGNES_API_CONFIG = {
    # --- 基础连接 ---
    'api_url': _get_env('AGNES_API_URL', 'https://api.agnes-ai.cn/v1/chat/completions'),
    'api_key': _get_env('AGNES_API_KEY', ''),
    # --- 模型选择（对齐 api.txt：默认 agnes-3.0-flash，回退 agnes-2.5-flash） ---
    'model_name': _get_env('AGNES_MODEL_NAME', 'agnes-3.0-flash'),
    'backup_model': _get_env('AGNES_BACKUP_MODEL', 'agnes-2.5-flash'),
    # --- 运行参数 ---
    'timeout': _get_env_int('AGNES_TIMEOUT', 60),
    'temperature': float(_get_env('AGNES_TEMPERATURE', '0.7')),
    'max_tokens': _get_env_int('AGNES_MAX_TOKENS', 2048),
    'top_p': float(_get_env('AGNES_TOP_P', '1.0')),
    'stream': _get_env_bool('AGNES_STREAM', False),
    # --- 重试与连接池（应对 429/5xx/SSL EOF） ---
    'max_retries': _get_env_int('AGNES_MAX_RETRIES', 3),
    'retry_backoff_factor': float(_get_env('AGNES_RETRY_BACKOFF', '0.5')),
    'pool_connections': _get_env_int('AGNES_POOL_CONNECTIONS', 5),
    'pool_maxsize': _get_env_int('AGNES_POOL_MAXSIZE', 10),
    # --- 可选能力开关 ---
    # 是否强制请求 JSON 输出（response_format={"type":"json_object"}）；
    # 部分模型不支持，可通过 AGNES_RESPONSE_FORMAT=false 关闭以保兼容。
    'response_format': _get_env('AGNES_RESPONSE_FORMAT', 'json_object'),
}

# ============ AGNES AI 调用预算（v5.2 ALG-02：分级超时 + 预算熔断） ============
# 依据项目升级方案 PUP-20261019-013100 ALG-02（原 4 次固定退避 + 60s 标量超时，
# 单次调用最坏可达分钟级，改为分级超时与 run 级预算熔断）：
#   - soft_timeout / hard_timeout 为「分级超时」两级（秒）：
#       soft_timeout  —— 单次 HTTP 尝试的读超时第一级（响应慢但未死时快速切断）；
#       hard_timeout  —— 单次 _call_ai_model 调用总墙钟硬上限（A22：30s，墙钟 ≤35s）。
#   - per_run_max_calls × hard_timeout = 单期 predict 的 AI 总预算（A23：≤60s），
#     本 run 累计 AI 墙钟超过该值即熔断降级（后续 AI 调用直接返回 None，不废弃请求）；
#   - enabled=False 为应急通道（R6：外部 API 变慢时仅保留单次硬上限、关闭 run 级熔断）。
# 变更经 reload_agnes_config() 免重启生效部分见 ai_analyzer；本段为进程级配置源。
AI_CALL_BUDGET = {
    'enabled': _get_env_bool('ENABLE_AI_BUDGET', True),
    'soft_timeout': float(_get_env('AI_SOFT_TIMEOUT', '20')),
    'hard_timeout': float(_get_env('AI_HARD_TIMEOUT', '30')),
    'per_run_max_calls': _get_env_int('AI_PER_RUN_MAX_CALLS', 2),
}

# ============ AGNES AI 提示词配置 ============
# §6.1.2 / §6.1.4：prompt 相关可调项统一在此，AI 仅作为「辅助解读」，
# 不得声称超越随机基线。历史期数越小 token 越少，可按需收敛。
AGNES_AI_PROMPT_CONFIG = {
    # 注入 prompt 的历史开奖期数（默认 20；越大越贵，越小越省）
    'history_periods': _get_env_int('AGNES_HISTORY_PERIODS', 20),
    # 是否向 prompt 注入「目标期号」，使 AI 明确分析对象
    'inject_target_issue': _get_env_bool('AGNES_INJECT_TARGET_ISSUE', True),
    # 是否注入 few-shot 合规 JSON 样例（降低格式错误率）
    'few_shot': _get_env_bool('AGNES_PROMPT_FEW_SHOT', True),
    # 是否注入诚实边界约束（不得编造超越随机基线的命中率）
    'honesty_boundary': _get_env_bool('AGNES_PROMPT_HONESTY', True),
    # AI 响应不合规时是否切换 backup_model 重试一次（仍失败则降级不崩溃）
    'retry_on_invalid': _get_env_bool('AGNES_RETRY_ON_INVALID', True),
    # 历史期数下限/上限（防止误配置把 token 打爆或数据过少）
    'history_periods_min': _get_env_int('AGNES_HISTORY_PERIODS_MIN', 5),
    'history_periods_max': _get_env_int('AGNES_HISTORY_PERIODS_MAX', 60),
}

# ============ 爬虫配置 ============
SPIDER_CONFIG = {
    'pages': _get_env_int('SPIDER_PAGES', 10),
    'timeout': _get_env_int('SPIDER_TIMEOUT', 15),
    'retry_count': _get_env_int('SPIDER_RETRY_COUNT', 3),
    'delay_min': _get_env_int('SPIDER_DELAY_MIN', 3),
    'delay_max': _get_env_int('SPIDER_DELAY_MAX', 6),
}

# ============ Redis 缓存配置 ============
REDIS_CONFIG = {
    'host': _get_env('REDIS_HOST', 'localhost'),
    'port': _get_env_int('REDIS_PORT', 6379),
    'db': _get_env_int('REDIS_DB', 0),
    'password': _get_env('REDIS_PASSWORD', '') or None,
    'key_prefix': _get_env('REDIS_KEY_PREFIX', 'kpluckynumber:pl5:'),
}

DATA_WINDOW_PERIOD = 60  # 全模块统一数据窗口期数

# ============ 组合约束配置（对应 BUG-05） ============
# 说明：min/max/threshold 为 predictor.py 读取的可配置硬范围；
# score_min/score_max 为 selection_strategy 软约束打分带默认带宽（原硬编码 10/35）；
# extreme_min/extreme_max 为极端和值硬惩罚带（原硬编码 5/40）；
# ssd 的 low/mid/high 为 SSD 软约束打分带默认阈值（原硬编码 1.0/15.0/20.0）。
# 新增键仅作为 selection_strategy 的魔法数外置，不改变任何既有取值与行为。
COMPOSITION_RULES = {
    'hezhi': {'min': 0, 'max': 55, 'configurable': True,
              'score_min': 10, 'score_max': 35,
              'extreme_min': 5, 'extreme_max': 40},         # 和值范围 + 打分带
    'spread': {'min': 0, 'max': 9, 'configurable': True,
               'score_min': 3, 'score_max': 8},             # 跨度范围 + 打分带
    'ssd': {'threshold': 80, 'configurable': True,
            'low': 1.0, 'mid': 15.0, 'high': 20.0},         # SSD 阈值 + 打分带
    'variance': {'threshold': 60, 'configurable': True}      # 方差阈值
}

# ============ 数据资产安全与保留策略（P2 堵塞点） ============
# 说明：
#   1. 历史开奖、预测记录属于可再生的外部数据；AI 报告属于高成本生成内容，需重点保护。
#   2. 若开启自动清理，请先在测试环境校验任务是否影响回测准确性。
#   3. 敏感配置（DB_PASSWORD / AGNES_API_KEY / REDIS_PASSWORD）必须通过环境变量注入，
#      禁止提交到版本库；本段仅做数据分级与保留期的集中声明，不解决脱敏和密钥管理。
DATA_ASSET_CONFIG = {
    'enable_auto_cleanup': _get_env_bool('DATA_ENABLE_AUTO_CLEANUP', False),
    'cleanup_dry_run': _get_env_bool('DATA_CLEANUP_DRY_RUN', True),
    'retention_days': {
        'p5_history_data': 3650,
        'p5_prediction_record': 3650,
        'p5_ai_report': 3650,
        'p5_verification_detail': 3650,
        'p5_trend_data': 3650,
        'p5_*_trend_data': 3650,
        'p5_evolution_version': 3650,
        'p5_learning_history': 3650,
    },
    'emergency_archive_tables': [
        'p5_prediction_record_bak_20260719',
    ],
    'classification': {
        'public': ['p5_history_data', 'p5_trend_data'],
        'internal': ['p5_prediction_record', 'p5_*_trend_data'],
        'restricted': ['p5_ai_report', 'p5_bayesian_result'],
    },
}

# ============ 数据迁移与归档约束（P0/P1 合规前置） ============
# 1. AI 报告大字段（LONGTEXT）迁移/压缩前，必须校验 report_uuid 唯一性与分表外键完整性。
# 2. 预测记录归档前，必须同步清理 p5_verification_detail 关联引用，避免孤儿记录。
# 3. 删除或重命名表（如 p5_prediction_record_bak_*）前，必须全仓库 grep 所有 SQL/代码引用。
DATA_MIGRATION_CONFIG = {
    'ai_report_compaction': {
        'enabled': _get_env_bool('DATA_AI_REPORT_COMPACTION', False),
        'target_table_header': 'p5_ai_report_header',
        'target_table_body': 'p5_ai_report_body',
        'min_report_content_bytes': 1024 * 1024,
    },
    'orphan_check': {
        'enabled': _get_env_bool('DATA_ORPHAN_CHECK', True),
        'enable_before_delete': True,
    },
}

# ============ 在线学习回写开关（v3.70 roadmap 1.11） ============
# 将 OnlineLearner 会话内学到的建议权重真正回写至持久化存储
# （p5_weight_history 表 + Redis kpluckynumber:pl5:adaptive_weights 通道），
# 使下次 P5PredictorConfig 会话可通过 _load_adaptive_weight_history 回放。
# 默认开启：回写属于"跨会话生效"行为，需人工确认权重方向后再开启，
# 避免自动回写引入不可控的权重漂移。环境变量 ONLINE_LEARNER_WRITEBACK 可覆盖。
# 注意：最终值由 §3.8 的 data/self_evolution_config.json 统一决定（见下方
# "自我进化 v2 配置" 段的 ONLINE_LEARNER_WRITEBACK 重定义，JSON 优先级更高）。

# ============ 自我进化 v2 配置（v4.0） ============
# 搜索后端选择："coord" (坐标下降) | "ga" (遗传算法) | "both" (两者并行取优)
# 优先级：data/self_evolution_config.json（外部化覆盖） > 环境变量 > 硬编码默认。
# 该 JSON 由 §3.8 定义，允许运维在不改代码、不重打包的前提下调整进化引擎行为。

def _load_self_evo_config_file() -> dict:
    """读取 data/self_evolution_config.json（§3.8 外部化配置源）。

    文件缺失或 JSON 损坏时返回空 dict，不抛异常、不阻断启动（保守回退默认值）。
    """
    import json
    try:
        _cfg_path = Path(__file__).parent / 'data' / 'self_evolution_config.json'
        if _cfg_path.is_file():
            with open(_cfg_path, 'r', encoding='utf-8') as _f:
                _data = json.load(_f)
            if isinstance(_data, dict):
                return _data
    except Exception:
        pass
    return {}


_SEVO_CFG = _load_self_evo_config_file()


def _sevo(key: str, default):
    """从 §3.8 JSON 取键，缺省回退 default。"""
    return _SEVO_CFG.get(key, default)


SEARCH_BACKEND = _sevo('search_backend', _get_env('SEARCH_BACKEND', 'both'))

# 遗传算法搜索参数（JSON 的 genetic_algorithm 块覆盖）
_GA_JSON = _SEVO_CFG.get('genetic_algorithm', {}) if isinstance(_SEVO_CFG.get('genetic_algorithm'), dict) else {}
GA_CONFIG = {
    'population_size': _GA_JSON.get('population_size', _get_env_int('GA_POPULATION_SIZE', 12)),
    'generations': _GA_JSON.get('generations', _get_env_int('GA_GENERATIONS', 20)),
    'mutation_rate': float(_GA_JSON.get('mutation_rate', _get_env('GA_MUTATION_RATE', '0.15'))),
    'crossover': _GA_JSON.get('crossover', _get_env('GA_CROSSOVER', 'blend')),
    'search_space': _GA_JSON.get('search_space', {
        'weights': ['frequency_weighted', 'ml_supervised', 'bayesian_inference', 'markov_transition'],
        'lookback_range': [40, 80],
        'feature_subset': 'auto',
    }),
}
if 'enabled' in _GA_JSON:
    GA_CONFIG['enabled'] = bool(_GA_JSON['enabled'])

# 实验追踪配置（JSON 的 experiment_tracking 块覆盖）
_ET_JSON = _SEVO_CFG.get('experiment_tracking', {}) if isinstance(_SEVO_CFG.get('experiment_tracking'), dict) else {}
EXPERIMENT_TRACKING = {
    'enabled': _ET_JSON.get('enabled', _get_env_bool('EXPERIMENT_TRACKING_ENABLED', True)),
    'store_git_hash': _ET_JSON.get('store_git_hash', _get_env_bool('EXPERIMENT_TRACKING_GIT_HASH', True)),
    'local_log': _ET_JSON.get('local_log', _get_env('EXPERIMENT_TRACKING_LOG', 'data/evolution_runs.jsonl')),
}

# 在线学习回写开关（JSON 的 online_learning.writeback 覆盖 env 默认）。
# 默认关闭（False）：与全仓 docstring「默认关闭」及诚实边界一致——建议型回灌
# 不自动落盘权重历史（p5_weight_history）/Redis 自适应权重通道。开启需显式设置
# 环境变量 ONLINE_LEARNER_WRITEBACK=true，或在 data/self_evolution_config.json 的
# online_learning.writeback 置 true。仅改默认值，回写逻辑本身不变。
_OL_JSON = _SEVO_CFG.get('online_learning', {}) if isinstance(_SEVO_CFG.get('online_learning'), dict) else {}
ONLINE_LEARNER_WRITEBACK = bool(
    _OL_JSON.get('writeback', _get_env_bool('ONLINE_LEARNER_WRITEBACK', False))
)

# 模型监控建议型回灌开关（§3.4 反馈→建议，JSON 的 online_learning.feedback_enable
# 覆盖 env 默认）。默认开启：该开关只开启「监控洞察」的建议型回灌——把 ModelMonitor
# 每次达到反馈阈值时的命中率/告警摘要，以**诚实洞察**形式追加到 data/ 的 JSONL 供人工
# 在「自动评估」页复核，**绝不**自动调整冻结权重、不写 p5_weight_history、不写 Redis
# 自适应权重通道（那些仍由 ONLINE_LEARNER_WRITEBACK 门控，二者相互独立）。
# 排列5 为公平摇号，回灌只标注「属正常波动、不构成预测能力」，不声称超越随机基线。
MODEL_FEEDBACK_ENABLE = bool(
    _OL_JSON.get('feedback_enable', _get_env_bool('MODEL_FEEDBACK_ENABLE', True))
)

# 系统性能监控开关（§4.3，JSON 的 perf_monitor.enable 覆盖 env 默认）。默认开启：
# 1Hz 守护线程采集 CPU/内存/响应/任务耗时指标 + 资源压力越限自动优化（清缓存）+
# 趋势留存。纯内存 + 本地 JSONL（可选落盘），无 DB 依赖；psutil 不可用时 CPU/内存
# 指标优雅降级为 None（任务/耗时类指标纯标准库、恒可用）。该开关与在线学习回写
# （ONLINE_LEARNER_WRITEBACK）相互独立——性能监控不触碰冻结权重。
_PF_JSON = _SEVO_CFG.get('perf_monitor', {}) if isinstance(_SEVO_CFG.get('perf_monitor'), dict) else {}
PERF_MONITOR_ENABLE = bool(
    _PF_JSON.get('enable', _get_env_bool('PERF_MONITOR_ENABLE', True))
)
# 性能监控参数（JSON 的 perf_monitor.params 可覆盖默认；缺省用 PERF_MONITOR_CONFIG 默认）。
_PERF_PARAMS = _PF_JSON.get('params', {}) if isinstance(_PF_JSON.get('params'), dict) else {}
PERF_MONITOR_CONFIG = _PERF_PARAMS  # dict（可为空，空则用模块默认配置）

# 结果自我验证与优化建议开关（§4.4，JSON 的 result_verifier.enable 覆盖 env 默认）。
# 默认开启：对分析结果做一致性自检（命中率边界/概率归一/组合结构/窗口一致性/数据
# 一致性）+ 生成分级优化建议（高/中/低）。无 DB 依赖、纯内存 + 可选本地 JSONL。
# 该开关与在线学习回写（ONLINE_LEARNER_WRITEBACK）相互独立——自我验证只输出
# 「建议型记录」，绝不自动调整 v4.0 冻结权重、不写 p5_weight_history、不写
# Redis 自适应权重通道（排列5 为公平摇号，建议仅对照随机基线、标注正常波动）。
_RV_JSON = _SEVO_CFG.get('result_verifier', {}) if isinstance(_SEVO_CFG.get('result_verifier'), dict) else {}
RESULT_VERIFIER_ENABLE = bool(
    _RV_JSON.get('enable', _get_env_bool('RESULT_VERIFIER_ENABLE', True))
)
# 结果验证器参数（JSON 的 result_verifier.params 可覆盖默认；缺省用模块默认配置）。
_RV_PARAMS = _RV_JSON.get('params', {}) if isinstance(_RV_JSON.get('params'), dict) else {}
RESULT_VERIFIER_CONFIG = _RV_PARAMS  # dict（可为空，空则用模块默认配置）

# ============ 版本信息 ============
# 版本号唯一来源：version.py 中的 APP_VERSION / CHANGELOG
# 此处仅作兼容性预留，实际请使用 from version import APP_VERSION
try:
    from version import APP_VERSION as _APP_VERSION
except Exception:
    _APP_VERSION = '3.52'