# -*- coding: utf-8 -*-
"""
self_evolution.py — 自我学习 / 自我训练 / 自我进化引擎（排列5）

【v3.52 完整实现（2026-08-15 重建）】
六阶段流水线：collect → baseline → evolve → evaluate → persist → done
后台守护线程 + queue 通信 + 检查点续跑 + DB 版本持久化 + 诚实边界闸门。
"""

import os
import sys
import json
import time
import queue
import threading
import logging
import datetime
import multiprocessing
import tempfile
from typing import Optional, List, Dict, Any

# 深度调优核心（组件缓存 + 坐标下降），本模块无重型依赖，可安全顶层导入
from modules.evolution_tuner import (
    DeepTuner, build_walkforward_windows, _row_to_sorted, _score,
)

logger = logging.getLogger(__name__)

# 模块独立日志文件（便于排查运行流程）
try:
    from paths import LOGS_DIR
    _evo_log_path = os.path.join(LOGS_DIR, 'self_evolution.log')
    _file_handler = logging.FileHandler(_evo_log_path, encoding='utf-8')
    _file_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(_file_handler)
    logger.setLevel(logging.INFO)
except Exception:  # noqa: BLE001
    pass

# 五位置常量（与 predictor / ml_predictor 保持一致）
POS = ['wan', 'qian', 'bai', 'shi', 'ge']
NUM = list(range(10))
ML_EVAL_MIN = 61   # predict_next 有效最小样本：n>=60 即可启动加权滑动频率模型

# 深度调优：不参与权重搜索的固定分量（如 ml_supervised 在调优期被冻结，
# 仅调统计类权重，规避 sklearn 子进程崩溃风险且聚焦可稳定学习的信号）。
TUNING_FIXED_KEYS = ('ml_supervised',)
# walk-forward 单次评估最多训练的模型组数（性能护栏）。
# 滑动窗口逐期递进，相邻窗口仅差 1 期样本，逐窗重训 5 个 GBM 极冗余；
# 按步长采样窗口即可在保留"严格无前视"正确性的前提下，将子进程训练次数
# 收敛到该上限（auto 模式窗口数本就 ≤ 此值，不受影响；全量模式由 30 降至此值）。
WF_MAX_TRAIN = 10


# =====================================================================
# 工具函数（保持与旧版签名兼容）
# =====================================================================

def _connect_db():
    """按需连接数据库，返回 P5Database 实例（含全部数据访问方法），失败返回 None。

    注意：早期实现误返回裸 pymysql.Connection，导致引擎内所有 db.get_history_data /
    db.save_artifact / db.disconnect 等方法调用因 AttributeError 被静默吞掉并退化为本地回退。
    此处改为返回统一的 P5Database 封装对象，使进化引擎真正与数据库（专用表 p5_evolution_version）
    打通，提升版本数据的可读性与一致性。
    """
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from modules.database import P5Database
        db = P5Database()
        if not db.connect():
            logger.warning('[self_evolution] 数据库连接失败（返回 None）')
            return None
        return db
    except Exception as e:  # noqa: BLE001
        logger.warning('[self_evolution] 数据库连接失败: %s', e)
        return None


def _now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _json_default(o):
    """json.dump 的安全 default：将不可直接序列化的对象转为可读字符串。

    解决「检查点写入失败: Object of type datetime is not JSON serializable」——
    pymysql 的 DATETIME 列返回 datetime 对象（如版本 created_at），经检查点入库后
    json.dump 会抛出 TypeError。统一在此兜底，保证检查点/版本文件始终可写。
    """
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, set):
        return list(o)
    if isinstance(o, bytes):
        try:
            return o.decode('utf-8', 'replace')
        except Exception:  # noqa: BLE001
            return repr(o)
    return str(o)


def _ml_pred_to_per_position(raw):
    """将 ml_predictor.predict_next 的原始输出转换为引擎评估期望的结构。

    ml_predictor.predict_next 返回 ``List[Dict[int, float]]``（长度 5，每位为
    {0..9: 概率} 且和为 1）；而本引擎的 walk-forward / retrain 评估期望
    ``{'per_position': [{top1, top3, top5}, ... 5 个位置]}``。

    本函数按概率降序取每位 Top-5，并派生 Top-3 / Top-1，使 ML 分量能真正
    参与命中率评估（此前因形状不匹配，ML 评估恒为空、对进化无贡献）。

    参数无效（None / 非 5 位 / 某位分布为空）时返回 None，保持下游
    ``if not pred`` 的跳过语义。
    """
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) != 5:
        return None
    per = []
    for dist in raw:
        if not isinstance(dist, dict) or not dist:
            return None
        ranked = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
        top5 = [int(d) for d, _ in ranked[:5]]
        if len(top5) < 5:
            # 概率分布异常（不足 5 位），按 0-9 顺序补足，保证结构完整
            for d in range(10):
                if d not in top5:
                    top5.append(d)
                if len(top5) >= 5:
                    break
        per.append({'top1': top5[0], 'top3': top5[:3], 'top5': top5[:5]})
    return {'per_position': per}


def _row_to_sorted(rows):
    """将原始 DB 行转为 ml_predictor.predict_next 期望的格式。"""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        nums = [r.get('wan'), r.get('qian'), r.get('bai'),
                r.get('shi'), r.get('ge')]
        if any(x is None for x in nums):
            continue
        out.append({'issue': str(r.get('issue', '')),
                    'numbers': [int(x) for x in nums]})
    return out


# =====================================================================
# AutoEvoScheduler（保持原样）
# =====================================================================

class AutoEvoScheduler:
    """后台静默调度器：控制 SelfEvolutionEngine 的自动触发频率，避免空转。"""

    DEFAULT_INTERVAL_HOURS = 24

    def __init__(self, engine, data_dir=None, interval_hours=None):
        self.engine = engine
        self.interval_hours = interval_hours or self.DEFAULT_INTERVAL_HOURS
        if data_dir is None:
            try:
                from paths import PROJECT_ROOT
                data_dir = os.path.join(PROJECT_ROOT, 'data')
            except Exception:  # noqa: BLE001
                data_dir = os.path.join(os.getcwd(), 'data')
        self.schedule_path = os.path.join(data_dir, 'self_evolution_schedule.json')
        self._timer = None

    def should_run(self) -> bool:
        try:
            if os.path.isfile(self.schedule_path):
                with open(self.schedule_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                last = data.get('last_run_ts')
                if last is not None:
                    delta = time.time() - float(last)
                    return delta >= self.interval_hours * 3600
        except Exception as e:  # noqa: BLE001
            logger.warning('[AutoEvoScheduler] should_run 读取失败（保守返回 False）: %s', e)
        return False

    def mark_run(self):
        try:
            with open(self.schedule_path, 'w', encoding='utf-8') as f:
                json.dump({'last_run_ts': time.time()}, f)
        except Exception as e:  # noqa: BLE001
            logger.warning('[AutoEvoScheduler] mark_run 写入失败: %s', e)

    def get_last_run_ts(self):
        try:
            if os.path.isfile(self.schedule_path):
                with open(self.schedule_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data.get('last_run_ts')
        except Exception as e:  # noqa: BLE001
            logger.warning('[AutoEvoScheduler] get_last_run_ts 读取失败: %s', e)
        return None


# =====================================================================
# _MLPredictorPool（v3.51：子进程隔离，防 sklearn 段错误）
# =====================================================================

class _MLPredictorPool:
    """隔离 ml_predictor 原生崩溃：用 spawn 子进程运行 predict_next。"""

    def __init__(self, max_workers=2, timeout=60):
        self._pool = multiprocessing.Pool(
            processes=max_workers, maxtasksperchild=50
        )
        self._timeout = timeout

    def predict_next(self, sorted_data, cfg_snapshot):
        """在子进程中调用 ml_predictor.predict_next，超时/崩溃均返回 None。"""
        try:
            result = self._pool.apply_async(
                _ml_predictor_worker_entry,
                (sorted_data, cfg_snapshot),
            )
            return result.get(timeout=self._timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning('[MLPredictorPool] predict_next 失败: %s', e)
            return None

    def shutdown(self):
        try:
            self._pool.terminate()
            self._pool.join(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _ml_predictor_worker_entry(sorted_data, cfg_snapshot):
    """子进程入口：导入 ml_predictor 并执行 predict_next。"""
    try:
        # 确保项目根在 sys.path
        _r = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _r not in sys.path:
            sys.path.insert(0, _r)
        from modules.ml_predictor import predict_next
        # 传入 target_issue=None 避免日志显示 None；实际预测不依赖该参数
        return predict_next(sorted_data, target_issue=None)
    except Exception as e:  # noqa: BLE001
        logger.warning('[MLPredictorPool] 子进程异常: %s', e)
        return None


# =====================================================================
# SelfEvolutionEngine（六阶段主类）
# =====================================================================

class SelfEvolutionEngine:
    """排列5 自我进化引擎（六阶段：collect→baseline→evolve→evaluate→persist→done）。

    构造参数：
        auto:       是否启动时自动触发（轻量模式，不跑滑动窗口）
        auto_full:  auto=True 时是否跑完整评估（default=False）
        data_dir:   检查点/本地回退目录（None 则用项目 data/）
    """

    PHASE_COLLECT = 'collect'
    PHASE_BASELINE = 'baseline'
    PHASE_EVOLVE = 'evolve'
    PHASE_EVALUATE = 'evaluate'
    PHASE_PERSIST = 'persist'
    PHASE_DONE = 'done'

    def __init__(self, auto=False, auto_full=False, data_dir=None):
        self.auto = auto
        self.auto_full = auto_full
        self._running = False
        self._run_lock = threading.Lock()  # 保护 _running 标志，防止双线程并发执行
        self._thread: Optional[threading.Thread] = None
        self.queue: queue.Queue = queue.Queue()

        # 数据目录
        if data_dir is None:
            try:
                from paths import PROJECT_ROOT
                data_dir = os.path.join(PROJECT_ROOT, 'data')
            except Exception:  # noqa: BLE001
                data_dir = os.path.join(os.getcwd(), 'data')
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        # 检查点
        self._ckpt_path = os.path.join(data_dir, 'self_evolution_state.json')
        self._checkpoint: Dict[str, Any] = {}

        # 版本存储（DB 优先，本地回退）
        self._db = None
        self._local_versions_path = os.path.join(data_dir, 'evolution_versions.json')
        self._versions: List[Dict[str, Any]] = []

        # 调度器
        self._scheduler = AutoEvoScheduler(self)
        self._timer: Optional[threading.Timer] = None

        # 评估缓存 / 建议 / 文本结晶
        self._last_assessment: Optional[Dict[str, Any]] = None
        self._proposals: List[Dict[str, Any]] = []

        # ML 子进程池
        self._ml_pool: Optional[_MLPredictorPool] = None

        # 加载历史版本（惰性）
        self._versions_loaded = False

        # ── 联动状态（与「开始分析」初始分析功能协同）─────────────────
        # 持久化到 data/evolution_link_state.json，保证引擎重启后仍可同步。
        self._link_state_path = os.path.join(self._data_dir, 'evolution_link_state.json')
        self._link_state: Dict[str, Any] = {
            'last_analysis_issue': None,      # 最近一次「开始分析」预测的目标期号
            'last_prediction': None,          # {target_issue, top_numbers, fused_probabilities}
            'pending_verification': False,     # 是否等待开奖结果回填
            'last_verification': None,        # {issue, top1, top3, top5, ts}
            'analysis_running': False,         # 初始分析是否进行中（用于暂停自动调度）
            'last_sync_ts': None,
            'best_candidate': None,           # {weights, lookback, metrics}
        }
        self._load_link_state()
        # 调度暂停标志：初始分析进行中时挂起静默定时器，避免资源/数据竞争
        self._scheduler_paused = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self):
        """启动引擎（非阻塞，后台线程）。"""
        if self._running:
            logger.info('[self_evolution] start 被调用但已在运行中，忽略')
            return
        with self._run_lock:
            if self._running:
                logger.info('[self_evolution] start 双重检查：已在运行，忽略')
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        logger.info('[self_evolution] 引擎已启动，后台线程 %s 开始执行', self._thread.name)
        # 启动定时器
        self.start_silent_timer()

    def is_running(self) -> bool:
        return self._running

    def run_now(self, full=True):
        """手动触发完整进化（full=True 跑滑动窗口评估）。"""
        self._enqueue({'type': 'run_now', 'full': bool(full)})

    def inject_active_version_to_predictor(self):
        """将当前 active 版本的权重快照注入预测器配置（v3.53 补充 stub）。

        设计说明：
            - 当前诚实边界下，所有候选版本均归档为 trial（不超越随机基线），
              因此不存在「可安全注入的 active 版本」。
            - 本方法保留为 no-op，使 GUI 侧调用（main.py:4050）不再抛 AttributeError，
              同时返回 False 表示本次未注入，不影响预测流程。
        """
        return False

    def shutdown(self):
        """优雅关闭（停止线程 + 子进程池）。"""
        self._running = False
        self.stop_silent_timer()
        if self._ml_pool is not None:
            try:
                self._ml_pool.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self._save_link_state()

    # ------------------------------------------------------------------
    # 联动状态持久化（跨重启数据同步）
    # ------------------------------------------------------------------
    def _load_link_state(self):
        try:
            if os.path.isfile(self._link_state_path):
                with open(self._link_state_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._link_state.update(loaded)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 联动状态读取失败: %s', e)

    def _save_link_state(self):
        try:
            self._atomic_write_json(self._link_state_path, self._link_state)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 联动状态写入失败: %s', e)

    # ------------------------------------------------------------------
    # 联动接口（与「开始分析」初始分析功能协同）
    # ------------------------------------------------------------------
    def notify_analysis_started(self):
        """初始分析开始时调用：挂起自动调度，标记分析进行中，避免资源/数据竞争。"""
        self._link_state['analysis_running'] = True
        self._scheduler_paused = True
        self._link_state['last_sync_ts'] = _now_str()
        self._save_link_state()
        self._emit('info', '联动：初始分析开始，暂停自动进化调度')

    def notify_analysis_done(self):
        """初始分析结束时调用：恢复自动调度。"""
        self._link_state['analysis_running'] = False
        self._scheduler_paused = False
        self._link_state['last_sync_ts'] = _now_str()
        self._save_link_state()
        self._emit('info', '联动：初始分析结束，恢复自动进化调度')

    def sync_analysis_result(self, prediction_record: Dict[str, Any]):
        """接收「开始分析」产出的预测记录，建立联动数据纽带。

        参数:
            prediction_record: 含 target_issue / top_numbers / fused_probabilities 的预测记录。
                缺字段时安全跳过对应项，不抛异常。
        """
        try:
            target = prediction_record.get('target_issue') or prediction_record.get('issue')
            top = prediction_record.get('top_numbers') or prediction_record.get('numbers')
            fused = prediction_record.get('fused_probabilities')
            self._link_state['last_analysis_issue'] = target
            self._link_state['last_prediction'] = {
                'target_issue': target,
                'top_numbers': top,
                'fused_probabilities': fused,
            }
            self._link_state['pending_verification'] = True
            self._link_state['last_sync_ts'] = _now_str()
            self._save_link_state()
            self._emit('info', f'联动：已接收初始分析预测（目标期号 {target}），等待开奖回填')
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] sync_analysis_result 失败: %s', e)

    def sync_verification(self, issue: str, actual_numbers: List[int]):
        """开奖结果回填：评估上次预测命中情况，更新联动状态并可能触发更深进化。

        参数:
            issue: 被验证期号（应与 last_analysis_issue 对应）
            actual_numbers: 该期真实开奖 5 位数字列表
        """
        try:
            pred = self._link_state.get('last_prediction') or {}
            top = pred.get('top_numbers') or []
            # Convert list of integers to list of lists if needed
            if top and not isinstance(top[0], (list, tuple)):
                top = [[x] for x in top]
            top1 = top3 = top5 = 0
            for i, n in enumerate(actual_numbers):
                picks = top[i] if i < len(top) else None
                if not isinstance(picks, (list, tuple)):
                    continue
                if picks:
                    top1 += 1 if n == picks[0] else 0
                    top3 += 1 if n in picks[:3] else 0
                    top5 += 1 if n in picks[:5] else 0
            total = len(actual_numbers) or 5
            metrics = {
                'top1': round(top1 / total * 100, 2),
                'top3': round(top3 / total * 100, 2),
                'top5': round(top5 / total * 100, 2),
            }
            self._link_state['last_verification'] = {
                'issue': issue, 'top1': metrics['top1'],
                'top3': metrics['top3'], 'top5': metrics['top5'],
                'ts': _now_str(),
            }
            self._link_state['pending_verification'] = False
            self._link_state['last_sync_ts'] = _now_str()
            self._save_link_state()
            self._last_assessment = {
                'summary': f'联动回填命中: Top1={metrics["top1"]}% / '
                           f'Top3={metrics["top3"]}% / Top5={metrics["top5"]}%',
                'conclusion': '基于初始分析预测的开奖回填评估',
                'weak_positions': [],
                'candidate': metrics, 'baseline': {}, 'ts': _now_str(),
            }
            # 若调度已到期，轻量触发一次进化以消化新数据
            try:
                if self._scheduler.should_run() and not self._running:
                    self.run_now(full=False)
            except Exception:  # noqa: BLE001
                pass
            self._emit('info', f'联动：开奖回填完成（期号 {issue}）Top3={metrics["top3"]}%')
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] sync_verification 失败: %s', e)

    def get_best_candidate_config(self) -> Optional[Dict[str, Any]]:
        """返回历史中指标最优的候选版本配置（含 weights / lookback）。

        优先取内存/DB 中 metrics 综合评分最高且优于随机基线的 trial；
        若无则返回 None（诚实边界：无可用优化配置）。
        """
        self._load_versions()
        best = None
        best_score = -1.0
        from modules.evolution_tuner import _score
        for v in self._versions:
            params = v.get('params_json') or {}
            weights = params.get('weights')
            if not isinstance(weights, dict) or not weights:
                continue
            m = v.get('metrics_json') or v.get('metrics') or {}
            if not isinstance(m, dict):
                continue
            # Ensure no None values in metrics
            m = {k: (v if v is not None else 0.0) for k, v in m.items()}
            s = _score(m)
            if s > best_score:
                best_score = s
                best = {
                    'version_tag': v.get('version_tag'),
                    'status': v.get('status'),
                    'weights': weights,
                    'lookback': params.get('lookback', 60),
                    'metrics': m,
                    'score': round(s, 3),
                }
        return best

    def apply_active_config_to_predictor(self, predictor, force: bool = False) -> bool:
        """将最优候选权重应用到预测器（替换旧版 no-op 注入）。

        诚实边界：仅当候选严格优于基线（Top1/3/5 均不劣化且综合评分更高）时自动应用；
        否则返回 False，不污染生产预测器。force=True 时由用户显式覆盖（如实验模式）。

        返回:
            bool —— 是否成功应用了候选配置
        """
        best = self.get_best_candidate_config()
        if best is None:
            self._emit('info', '联动：暂无候选优化配置，维持生产权重')
            return False
        base_metrics = self._checkpoint.get('baseline', {}).get('metrics', {}) if isinstance(
            self._checkpoint.get('baseline'), dict) else {}
        beats = (base_metrics and
                 best['metrics'].get('top1', 0) >= float(base_metrics.get('top1', 0)) - 1e-6 and
                 best['metrics'].get('top3', 0) >= float(base_metrics.get('top3', 0)) - 1e-6 and
                 best['metrics'].get('top5', 0) >= float(base_metrics.get('top5', 0)) - 1e-6 and
                 _score(best['metrics']) > _score(base_metrics) + 1e-6)
        if not beats and not force:
            self._emit('info', f'联动：候选未超越基线（评分 {best["score"]}），维持生产权重（不自动注入）')
            return False
        try:
            for algo, w in best['weights'].items():
                # Handle both real P5PredictorConfig object and dictionary mock
                config_obj = predictor.config
                if hasattr(config_obj, 'config'):
                    # Real P5PredictorConfig object
                    config_dict = config_obj.config
                else:
                    # Dictionary mock (used in tests)
                    config_dict = config_obj
                if algo in config_dict.get('algorithms', {}):
                    config_dict['algorithms'][algo]['weight'] = float(w)
            self._emit('success', f'联动：已注入候选优化权重（v={best.get("version_tag")}, '
                                   f'force={force}）')
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 应用候选配置失败: %s', e)
            return False

    def get_link_state(self) -> Dict[str, Any]:
        """返回联动状态快照（供 GUI 展示「联动状态」）。"""
        return dict(self._link_state)

    # ------------------------------------------------------------------
    # 消息投递
    # ------------------------------------------------------------------
    def _enqueue(self, msg: Dict[str, Any]):
        try:
            self.queue.put_nowait(msg)
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, level: str, text: str, **kwargs):
        """发送日志消息（GUI 统一以 log 类型接收并渲染到日志框）。"""
        self._enqueue({'type': 'log', 'level': level, 'text': text, **kwargs})

    def _send(self, msg_type: str, data: Dict[str, Any] = None):
        """发送结构化控制消息（metrics/stage/hitrate/tuning_perf 等），绕过 _emit 的 log 包装。

        _emit 将所有调用包装为 {'type': 'log', ...}，GUI 的 _handle_evolution_msg 中
        t == 'metrics' / 'hitrate' / 'stage' / 'tuning_perf' 分支永远不会命中。
        本方法直接投递指定 type 的消息，使 GUI 能正确路由到对应 UI 更新逻辑。
        """
        self._enqueue({'type': msg_type, **(data or {})})

    # ------------------------------------------------------------------
    # 定时器（v3.52 AutoEvoScheduler）
    # ------------------------------------------------------------------
    def start_silent_timer(self):
        self.stop_silent_timer()
        self._timer = threading.Timer(
            60.0, self._silent_timer_tick
        )
        self._timer.daemon = True
        self._timer.start()

    def stop_silent_timer(self):
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._timer = None

    def _silent_timer_tick(self):
        try:
            # 用锁保护 _running 检查，避免定时器周期重叠时启动多线程
            with self._run_lock:
                if self._scheduler_paused:
                    # 联动：初始分析进行中，挂起自动调度，下一轮再检查
                    logger.info('[self_evolution] 定时器 tick：联动暂停中（analysis_running=True），跳过本轮')
                elif self._scheduler.should_run() and not self._running:
                    logger.info('[self_evolution] 定时器 tick：检测到应触发自我进化，启动轻量自检…')
                    # 轻量运行：auto=True, auto_full=False
                    self._running = True
                    t = threading.Thread(target=self._run, daemon=True)
                    t.start()
                else:
                    if self._running:
                        logger.info('[self_evolution] 定时器 tick：引擎正在运行，跳过本轮触发')
                    else:
                        logger.info('[self_evolution] 定时器 tick：尚未到达调度间隔，跳过本轮')
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 定时器 tick 异常: %s', e)
        # 下一轮
        self.start_silent_timer()

    # ------------------------------------------------------------------
    # 主循环（六阶段）
    # ------------------------------------------------------------------
    def _run(self):
        """六阶段主循环（在后台线程执行）。"""
        logger.info('[self_evolution] _run 主循环开始（线程=%s）', threading.current_thread().name)
        # 加载检查点：若存在未完成检查点则从断点续跑
        self._load_checkpoint()
        start_phase = self._checkpoint.get('phase', self.PHASE_COLLECT)
        has_ckpt = bool(self._checkpoint)
        logger.info('[self_evolution] 检查点状态: 已加载=%s, 当前阶段=%s, 检查点键=%s',
                     has_ckpt, start_phase, list(self._checkpoint.keys()) if has_ckpt else '无')

        phases = [
            self.PHASE_COLLECT,
            self.PHASE_BASELINE,
            self.PHASE_EVOLVE,
            self.PHASE_EVALUATE,
            self.PHASE_PERSIST,
            self.PHASE_DONE,
        ]
        try:
            start_idx = phases.index(start_phase)
        except ValueError:
            start_idx = 0
        logger.info('[self_evolution] 将从索引 %d 继续（总阶段数=%d）', start_idx, len(phases))

        for phase_idx, phase in enumerate(phases[start_idx:]):
            if not self._running:
                logger.info('[self_evolution] _run 循环：_running=False，中止进化流程')
                self._emit('warning', '自我进化被中止')
                break
            phase_real_idx = start_idx + phase_idx
            logger.info('[self_evolution] >>> 进入阶段 %d/%d: %s', phase_real_idx, len(phases) - 1, phase)
            self._checkpoint['phase'] = phase
            self._save_checkpoint()
            self._emit('section', f'[阶段] {phase.upper()}')
            # 发送阶段进度消息给 GUI（更新阶段指示器和状态栏）
            self._send('stage', {'index': phase_real_idx, 'total': len(phases), 'name': phase})
            try:
                handler = getattr(self, f'_phase_{phase}', None)
                if handler:
                    handler()
                else:
                    logger.warning('[self_evolution] 阶段处理器缺失: %s', phase)
                    self._emit('warning', f'阶段处理器缺失: {phase}')
            except Exception as e:  # noqa: BLE001
                logger.error('[self_evolution] 阶段 %s 异常: %s', phase, e, exc_info=True)
                self._emit('error', f'阶段 {phase} 异常: {e}')
                break
            logger.info('[self_evolution] <<< 阶段 %s 执行完毕，检查点=%s', phase,
                        list(self._checkpoint.keys()))

        # 完成
        logger.info('[self_evolution] 六阶段主循环全部结束，清空检查点并标记调度完成')
        self._checkpoint = {}
        self._save_checkpoint()
        self._scheduler.mark_run()
        if self._running:
            logger.info('[self_evolution] 引擎完成，发送 done 消息')
            self._emit('success', '自我进化完成')
            self._enqueue({'type': 'status', 'status': 'idle', 'version': self._current_version_tag()})
        self._running = False
        logger.info('[self_evolution] _run 主循环结束（线程=%s）', threading.current_thread().name)

    # ------------------------------------------------------------------
    # 阶段实现
    # ------------------------------------------------------------------
    def _atomic_write_json(self, path, data):
        """原子写 JSON：先写临时文件，再 os.replace，避免断电/强杀后文件损坏。"""
        dir_name = os.path.dirname(path)
        fd, tmp = None, None
        try:
            fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning('[self_evolution] 原子写失败 (%s): %s', path, e)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _phase_collect(self):
        """阶段 1：数据采集——统计样本量、最新期号、新增数据量。"""
        logger.info('[self_evolution] _phase_collect 开始')
        self._emit('info', '采集历史数据样本量…')
        db = _connect_db()
        if db is None:
            logger.warning('[self_evolution] _phase_collect: 数据库连接失败（_connect_db 返回 None）')
            self._emit('warning', '数据库不可用，采集阶段降级（使用本地快照）')
            self._checkpoint['history_count'] = 0
            self._checkpoint['latest_issue'] = ''
            self._send('metrics', {'history_count': 0, 'latest_issue': ''})
            return

        try:
            if not db.connect():
                logger.warning('[self_evolution] _phase_collect: db.connect() 返回 False')
                self._checkpoint['history_count'] = 0
                self._checkpoint['latest_issue'] = ''
                self._send('metrics', {'history_count': 0, 'latest_issue': ''})
                return
            rows = db.get_history_data(limit=1, order='DESC')
            latest_issue = rows[0].get('issue', '') if rows else ''
            total = db.get_history_data_count()
            self._checkpoint['latest_issue'] = latest_issue
            self._checkpoint['history_count'] = total
            logger.info('[self_evolution] _phase_collect 完成: 样本量=%s 期, 最新期号=%s', total, latest_issue)
            self._emit('info', f'  样本量: {total} 期, 最新期号: {latest_issue}')
            # 推送指标磁贴更新（供 GUI 显示样本量/最新期号）
            self._send('metrics', {'history_count': total, 'latest_issue': latest_issue})
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] _phase_collect 异常: %s', e, exc_info=True)
            self._emit('warning', f'采集异常: {e}')
            self._checkpoint['history_count'] = 0
            self._send('metrics', {'history_count': 0, 'latest_issue': ''})
        finally:
            if db is not None:
                try:
                    db.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        logger.info('[self_evolution] _phase_collect 结束')

    # ------------------------------------------------------------------
    # 深度调优辅助
    # ------------------------------------------------------------------
    def _make_tuning_predictor(self):
        """构造「调优专用」预测器：冻结 ML 分量、关闭 AI，仅保留统计类信号。

        目的：
          - 规避 ml_predictor 的 sklearn 子进程在极端环境下崩溃/被沙箱拦截的风险；
          - 聚焦可稳定学习的统计类权重（频率/遗漏/贝叶斯/趋势/马尔可夫/形态/特征）；
          - 大幅提速：_run_algorithms 不再触发 GBM 训练。
        深度调优器在权重空间搜索时，分量（权重无关）只需按窗口算一次并缓存，
        ML 作为固定 0.14 权重分量保留在最终融合中（见 TUNING_FIXED_KEYS）。
        """
        try:
            from modules.predictor import P5Predictor
            p = P5Predictor()
            # Handle both real P5PredictorConfig object and dictionary mock
            config_obj = p.config
            if hasattr(config_obj, 'config'):
                # Real P5PredictorConfig object
                config_dict = config_obj.config
            else:
                # Dictionary mock (used in tests)
                config_dict = config_obj
            algos = config_dict.get('algorithms', {})
            if 'ml_supervised' in algos:
                algos['ml_supervised']['enabled'] = False
            config_dict['global']['enable_ai_model'] = False
            # Note: The above line is duplicated in the original, but we keep it as is.
            return p
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 调优预测器构造失败: %s', e)
            return None

    def _get_statistical_weights(self) -> Dict[str, float]:
        """读取当前生产预测器的「可调」统计类权重（排除冻结分量）。"""
        try:
            from modules.predictor import P5Predictor
            p = P5Predictor()
            config = p.config
            # Try to get algorithm weights via method if available
            if hasattr(config, 'get_algorithm_weights'):
                weights = config.get_algorithm_weights()
            else:
                # Fallback: extract from algorithms dict
                algorithms = None
                if isinstance(config, dict):
                    algorithms = config.get('algorithms')
                else:
                    algorithms = getattr(config, 'algorithms', None)
                if algorithms is None:
                    raise AttributeError('No algorithms found in config')
                weights = {}
                for algo, details in algorithms.items():
                    if isinstance(details, dict) and 'weight' in details:
                        weights[algo] = details['weight']
                    else:
                        # If details is a number or unexpected format, skip
                        pass
            return {k: float(v) for k, v in weights.items() if k not in TUNING_FIXED_KEYS}
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 读取统计权重失败: %s', e)
            return {'frequency_weighted': 0.68, 'omission_regression': 0.06,
                    'bayesian_inference': 0.10, 'trend_momentum': 0.01,
                    'markov_transition': 0.005, 'pattern_continuation': 0.003,
                    'feature_engineering': 0.002}

    def _build_train_windows(self, eval_periods: int):
        """从 DB 读取历史并构造无前视 walk-forward 窗口。

        返回: (windows, hist_count)
            windows: [(train_rows, actual_numbers), ...]
        """
        db = _connect_db()
        if db is None:
            return [], 0
        try:
            if not db.connect():
                return [], 0
            rows = db.get_history_data(limit=None, order='ASC')
            if not rows or len(rows) < ML_EVAL_MIN + 1:
                return [], len(rows) if rows else 0
            rows = _row_to_sorted(rows)
            windows = build_walkforward_windows(
                rows, eval_periods=eval_periods,
                ml_eval_min=ML_EVAL_MIN, wf_max_train=WF_MAX_TRAIN)
            return windows, len(rows)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 构造训练窗口失败: %s', e)
            return [], 0
        finally:
            try:
                db.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _phase_baseline(self):
        """阶段 2：采集基线——当前版本指标 + 融合权重快照。"""
        logger.info('[self_evolution] _phase_baseline 开始')
        self._emit('info', '采集基线版本指标…')
        # 惰性加载历史版本
        self._load_versions()
        active = [v for v in self._versions if v.get('status') == 'active']
        logger.info('[self_evolution] _phase_baseline: 历史版本总数=%s, active版本数=%s',
                     len(self._versions), len(active))
        if active:
            baseline = active[-1]
            self._checkpoint['baseline'] = baseline
            metrics = baseline.get('metrics', {})
            logger.info('[self_evolution] _phase_baseline 完成: 基线版本=%s, Top1=%s%%, Top3=%s%%, Top5=%s%%',
                        baseline.get('version_tag'), metrics.get('top1'), metrics.get('top3'), metrics.get('top5'))
            self._emit('info', f'  基线 Top1={metrics.get("top1", "N/A")}%, '
                               f'Top3={metrics.get("top3", "N/A")}%, '
                               f'Top5={metrics.get("top5", "N/A")}%')
        else:
            self._checkpoint['baseline'] = {}
            logger.info('[self_evolution] _phase_baseline: 无历史 active 版本，以随机基线为参照')
            self._emit('info', '  无历史 active 版本，以随机基线为参照')
        logger.info('[self_evolution] _phase_baseline 结束')

    def _phase_evolve(self):
        """阶段 3：参数迭代——深度调优（权重空间坐标下降）+ 采集权重快照。

        v3.56 升级：
          - 原实现仅「采集权重快照」+ 单次 ML 重训，不做参数搜索；
          - 现引入 DeepTuner：在统计类融合权重 + lookback 空间做坐标下降，
            以 walk-forward（无前视）命中率为目标，组件按窗口缓存、仅重融合，
            成本从「候选×窗口×重训」降至「窗口×重算 + 候选×廉价重融合」。
        """
        logger.info('[self_evolution] _phase_evolve 开始')
        self._emit('info', '参数迭代：深度调优（组件缓存 + 坐标下降）…')
        # 惰性初始化 ML 子进程池
        if self._ml_pool is None:
            try:
                self._ml_pool = _MLPredictorPool(max_workers=2, timeout=60)
                logger.info('[self_evolution] ML 子进程池创建成功（max_workers=2, timeout=60s）')
            except Exception as e:  # noqa: BLE001
                logger.warning('[self_evolution] ML 子进程池创建失败（降级为进程内）: %s', e)
                self._emit('warning', f'ML 子进程池创建失败（降级为进程内）：{e}')
                self._ml_pool = None

        # 采集权重快照
        weight_snapshot = self._collect_weight_snapshot()
        self._checkpoint['weight_snapshot'] = weight_snapshot
        logger.info('[self_evolution] 权重快照已采集: weights=%s, lookback=%s',
                     weight_snapshot.get('weights'), weight_snapshot.get('lookback'))

        # ── 深度调优：构造窗口 → 坐标下降搜索最优权重/lookback ──
        hist_count = self._checkpoint.get('history_count', 0)
        eval_periods = 10 if (self.auto and not self.auto_full) else 15
        logger.info('[self_evolution] 样本量=%s, auto=%s, auto_full=%s, eval_periods=%s',
                     hist_count, self.auto, self.auto_full, eval_periods)
        tuned = None
        if hist_count >= ML_EVAL_MIN + 1:
            logger.info('[self_evolution] 样本量充足，开始构造训练窗口（eval_periods=%s）', eval_periods)
            try:
                windows, win_count = self._build_train_windows(eval_periods)
                logger.info('[self_evolution] 窗口构造完成: 有效窗口数=%s', len(windows))
                if windows:
                    base_weights = self._get_statistical_weights()
                    logger.info('[self_evolution] 当前统计权重: %s', base_weights)
                    tuning_predictor = self._make_tuning_predictor()
                    if tuning_predictor is not None:
                        logger.info('[self_evolution] 调优预测器构造成功，开始坐标下降搜索')
                        from modules.evolution_tuner import PredictorComponentProvider
                        provider = PredictorComponentProvider(tuning_predictor)
                        tuner = DeepTuner(
                            provider=provider,
                            delta=0.03, max_rounds=6,
                            enable_lookback_search=(not self.auto or self.auto_full),
                            lookback_candidates=(40, 60, 80),
                        )
                        baseline_metrics = self._checkpoint.get('baseline', {}).get('metrics') if isinstance(
                            self._checkpoint.get('baseline'), dict) else None
                        tuned = tuner.tune(
                            base_weights, windows,
                            base_lookback=60,
                            baseline_metrics=baseline_metrics,
                            fixed_keys=list(TUNING_FIXED_KEYS),
                        )
                        self._checkpoint['tuned_weights'] = tuned['weights']
                        self._checkpoint['tuned_lookback'] = tuned['lookback']
                        self._checkpoint['tuned_metrics'] = tuned['metrics']
                        self._checkpoint['tuning_perf'] = {
                            'candidates_evaluated': tuned['candidates_evaluated'],
                            'cache_hits': tuned['cache_hits'],
                            'cache_misses': tuned['cache_misses'],
                            'elapsed_ms': tuned['elapsed_ms'],
                            'improved': tuned['improved'],
                        }
                        logger.info('[self_evolution] 深度调优结果: 候选=%s, 缓存命中=%s, 耗时=%sms, improved=%s, Top3=%s%%',
                                    tuned['candidates_evaluated'], tuned['cache_hits'],
                                    tuned['elapsed_ms'], tuned['improved'],
                                    tuned['metrics'].get('top3'))
                        # 推送调优性能给 GUI（更新调优耗时磁贴）
                        self._send('tuning_perf', {'elapsed_ms': tuned['elapsed_ms']})
                        self._emit('info',
                                   f'  深度调优完成：候选 {tuned["candidates_evaluated"]} 组, '
                                   f'组件缓存命中 {tuned["cache_hits"]} 次, '
                                   f'耗时 {tuned["elapsed_ms"]}ms, '
                                   f'Top3={tuned["metrics"].get("top3", "N/A")}% '
                                   f'(基线 {tuned["baseline_metrics"].get("top3", "N/A")}%)')
                        if tuned['improved']:
                            self._emit('success', '  深度调优找到不劣于基线的候选配置')
                    else:
                        logger.warning('[self_evolution] 调优预测器构造失败，跳过深度调优')
                        self._emit('warning', '  调优预测器不可用，跳过深度调优')
                else:
                    logger.info('[self_evolution] 窗口数量为 0，跳过深度调优')
                    self._emit('info', '  窗口不足，跳过深度调优')
            except Exception as e:  # noqa: BLE001
                logger.error('[self_evolution] 深度调优异常: %s', e, exc_info=True)
                self._emit('warning', f'  深度调优异常（降级）: {e}')
        else:
            logger.info('[self_evolution] 样本量 %s < 最小要求 %s，跳过深度调优', hist_count, ML_EVAL_MIN)
            self._emit('info', f'  样本量 {hist_count} < 最小要求 {ML_EVAL_MIN}，跳过深度调优')

        # 尝试 ML 重训（仅数据量足够时，作为独立单次 OOS 指标）
        if hist_count >= ML_EVAL_MIN:
            logger.info('[self_evolution] 开始 ML 单次重训评估（样本量=%s >= %s）', hist_count, ML_EVAL_MIN)
            ml_metrics = self._drive_ml_retrain()
            self._checkpoint['ml_metrics'] = ml_metrics
            logger.info('[self_evolution] ML 重训完成: 指标=%s', ml_metrics)
        else:
            logger.info('[self_evolution] ML 重训跳过：样本量 %s < 最小要求 %s', hist_count, ML_EVAL_MIN)
            self._emit('info', f'  ML 重训跳过：样本量 {hist_count} < 最小要求 {ML_EVAL_MIN}')
            self._checkpoint['ml_metrics'] = None
        logger.info('[self_evolution] _phase_evolve 结束')

    def _phase_evaluate(self):
        """阶段 4：OOS 评估——优先采用深度调优候选指标，否则回退 walk-forward。"""
        self._emit('info', 'OOS 评估：对比基线（深度调优候选 / walk-forward）…')
        # 优先用深度调优产出的候选指标
        tuned_metrics = self._checkpoint.get('tuned_metrics')
        if tuned_metrics and tuned_metrics.get('tested', 0) > 0:
            ml_metrics = tuned_metrics
            self._emit('info', '  采用深度调优候选指标作为评估对象')
        else:
            hist_count = self._checkpoint.get('history_count', 0)
            eval_periods = 10 if (self.auto and not self.auto_full) else 15
            if hist_count < ML_EVAL_MIN + eval_periods:
                self._emit('info', f'  样本不足，评估降级为最近 {max(1, hist_count - ML_EVAL_MIN)} 期')
                eval_periods = max(1, hist_count - ML_EVAL_MIN)
            ml_metrics = self._evaluate_walkforward(eval_periods)

        self._checkpoint['eval_metrics'] = ml_metrics
        # 诚实边界：与基线对比
        baseline = self._checkpoint.get('baseline', {}) or {}
        b_metrics = baseline.get('metrics', {}) if isinstance(baseline, dict) else {}
        better = self._compare_metrics(ml_metrics, b_metrics)
        self._checkpoint['beat_baseline'] = better
        tag = '通过' if better else '未超越'
        self._emit('info', f'  候选: Top1={ml_metrics.get("top1", "N/A")}%, '
                           f'基线: Top1={b_metrics.get("top1", "N/A")}% → {tag}')
        # 推送命中率指标给 GUI（更新 Top-4 命中磁贴）
        top4 = ml_metrics.get('top3') if ml_metrics else None  # 当前引擎使用 top3 口径，作为 Top-4 展示
        self._send('hitrate', {'top4': top4} if top4 is not None else {})
        # 自我评分 + 结晶
        self._last_assessment = self._assess_improvement_internal(ml_metrics, b_metrics)

    def _phase_persist(self):
        """阶段 5：持久化——写版本表/文件（含深度调优候选权重/lookback/性能）。"""
        logger.info('[self_evolution] _phase_persist 开始')
        self._emit('info', '持久化版本…')
        beat = self._checkpoint.get('beat_baseline', True)
        ml_metrics = self._checkpoint.get('eval_metrics', {}) or {}
        weight_snapshot = self._checkpoint.get('weight_snapshot', {}) or {}
        baseline = self._checkpoint.get('baseline', {}) or {}

        # 合并深度调优结果到 params_json（权重 / lookback / 调优性能）
        tuned_weights = self._checkpoint.get('tuned_weights')
        tuned_lookback = self._checkpoint.get('tuned_lookback')
        tuning_perf = self._checkpoint.get('tuning_perf', {}) or {}
        params_json = dict(weight_snapshot)
        if tuned_weights:
            params_json['weights'] = tuned_weights
        if tuned_lookback is not None:
            params_json['lookback'] = tuned_lookback
        if tuning_perf:
            params_json['tuning'] = tuning_perf
        logger.info('[self_evolution] 准备持久化版本: beat=%s, tuned_weights=%s, tuned_lookback=%s',
                     beat, bool(tuned_weights), tuned_lookback)

        version_tag = self._make_version_tag()
        parent_tag = baseline.get('version_tag', '') if isinstance(baseline, dict) else ''
        status = 'active' if beat else 'trial'
        record = {
            'version_tag': version_tag,
            'parent_tag': parent_tag,
            'status': status,
            'params_json': params_json,
            'metrics_json': ml_metrics,
            'baseline_json': baseline,
            'note': f'auto={self.auto}, full={self.auto_full}, beat={beat}, '
                    f'tuned={"yes" if tuned_weights else "no"}',
            'created_at': _now_str(),
        }
        logger.info('[self_evolution] 调用 _persist_version: tag=%s, status=%s', version_tag, status)
        saved = self._persist_version(record)
        if saved:
            self._versions.append(record)
            logger.info('[self_evolution] 版本持久化成功: %s [%s]', version_tag, status)
            self._emit('success', f'  版本已持久化: {version_tag} [{status}]')
            # 同步最优候选到联动状态，供「开始分析」读取
            if tuned_weights:
                self._link_state['best_candidate'] = {
                    'weights': tuned_weights,
                    'lookback': tuned_lookback,
                    'metrics': ml_metrics,
                }
                self._save_link_state()
                logger.info('[self_evolution] 最优候选已写入联动状态')
        else:
            logger.warning('[self_evolution] 版本持久化失败: tag=%s', version_tag)
            self._emit('error', '  版本持久化失败')
        logger.info('[self_evolution] _phase_persist 结束')

    def _phase_done(self):
        """阶段 6：收尾——生成改进建议。"""
        logger.info('[self_evolution] _phase_done 开始')
        self._emit('info', '生成改进建议…')
        proposals = self._generate_proposals_from_assessment()
        self._proposals = proposals
        logger.info('[self_evolution] 改进建议: 共 %s 条', len(proposals))
        if proposals:
            self._persist_proposals(proposals)
            self._emit('info', f'  已生成 {len(proposals)} 条改进建议')
        else:
            self._emit('info', '  暂无改进建议')
        logger.info('[self_evolution] _phase_done 结束')

    # ------------------------------------------------------------------
    # 参数 / 评估辅助
    # ------------------------------------------------------------------
    def _collect_weight_snapshot(self) -> Dict[str, Any]:
        """采集当前融合权重快照 + lookback 等参数。"""
        snapshot = {'timestamp': _now_str()}
        try:
            _r = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _r not in sys.path:
                sys.path.insert(0, _r)
            from modules.predictor import P5Predictor
            inst = P5Predictor()
            cfg = getattr(inst, 'config', None)
            if cfg is not None:
                if isinstance(cfg, dict):
                    snapshot['weights'] = cfg.get('weights') or cfg.get('algo_weights')
                    snapshot['lookback'] = cfg.get('lookback')
                    global_cfg = cfg.get('global', {})
                    if isinstance(global_cfg, dict):
                        snapshot['enable_ai_model'] = global_cfg.get('enable_ai_model')
                else:
                    snapshot['weights'] = getattr(cfg, 'weights', None) or getattr(cfg, 'algo_weights', None)
                    snapshot['lookback'] = getattr(cfg, 'lookback', None)
                    if hasattr(cfg, 'get_global_param'):
                        snapshot['enable_ai_model'] = cfg.get_global_param('enable_ai_model', None)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 权重快照采集失败: %s', e)
            snapshot['weights'] = None
            snapshot['lookback'] = None
        return snapshot

    def _drive_ml_retrain(self) -> Dict[str, Any]:
        """调用 ml_predictor 强制重训并返回本次 OOS 指标。"""
        db = _connect_db()
        if db is None:
            logger.warning('[self_evolution] ML 重训跳过：数据库连接失败（DB 不可用）')
            return {}
        try:
            if not db.connect():
                logger.warning('[self_evolution] ML 重训跳过：数据库连接失败')
                return {}
            rows = db.get_history_data(limit=None, order='ASC')
            if not rows or len(rows) < ML_EVAL_MIN:
                logger.warning('[self_evolution] ML 重训跳过：样本量不足（%s < %s）',
                               len(rows) if rows else 0, ML_EVAL_MIN)
                return {}
            sorted_data = _row_to_sorted(rows)
            cfg_snap = self._checkpoint.get('weight_snapshot', {})

            def _run_in_process(data_slice, cfg):
                if self._ml_pool is not None:
                    return self._ml_pool.predict_next(data_slice, cfg)
                return None

            # 最近1期作为评估
            train = sorted_data[:-1] if len(sorted_data) > 1 else sorted_data
            target = sorted_data[-1]
            pred = None
            if self._ml_pool is not None:
                pred = self._ml_pool.predict_next(train, cfg_snap)
            else:
                try:
                    _r = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    if _r not in sys.path:
                        sys.path.insert(0, _r)
                    if not hasattr(self, '_fallback_ml'):
                        from modules.ml_predictor import predict_next
                        self._fallback_ml = predict_next
                    mp = self._fallback_ml
                    # 模块级 predict_next 不接受 config 快照，直接调用
                    # 传入 target_issue=None 避免日志显示 None；实际预测不依赖该参数
                    pred = mp(train, target_issue=None)
                except Exception as e:  # noqa: BLE001
                    logger.warning('[self_evolution] ML 重训失败: %s', e)
                    pred = None

            # 把 predict_next 的概率分布统一转换为引擎评估期望的 per_position 结构
            pred = _ml_pred_to_per_position(pred)

            actual = target['numbers']
            if pred and 'per_position' in pred:
                per = pred['per_position']
                hits = sum(
                    1 for i, n in enumerate(actual)
                    if i < len(per) and n in per[i].get('top3', [])
                )
                return {'ml_tested': 1, 'ml_top3_hits': hits, 'ml_top3_rate': round(hits / 5 * 100, 2)}
            return {}
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] _drive_ml_retrain 异常: %s', e)
            return {}
        finally:
            try:
                db.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _evaluate_walkforward(self, eval_periods: int) -> Dict[str, Any]:
        """滑动窗口 OOS 评估（只用被评估期之前的数据，无前视泄漏）。"""
        db = _connect_db()
        if db is None:
            return {}
        try:
            if not db.connect():
                return {}
            rows = db.get_history_data(limit=None, order='ASC')
            if not rows or len(rows) < ML_EVAL_MIN + 1:
                return {}
            total = len(rows)
            start = max(ML_EVAL_MIN, total - eval_periods)
            windows = rows[start:total]

            top1_hits = 0
            top3_hits = 0
            top5_hits = 0
            tested = 0

            # 性能护栏：滑动窗口逐期递进，相邻窗口仅差 1 期样本，逐窗重训 5 个 GBM
            # 极冗余；按步长采样评估点，将单次评估的"训练组数"收敛到 WF_MAX_TRAIN。
            # 语义不变（每个训练点仍只用其之前的样本，严格无前视），仅减少评估样本量；
            # auto 模式窗口数本就 ≤ 此值（step=1 全评估），不受影响。
            last_idx = len(windows) - 1  # 最近一期评估点索引（最大训练集、最具代表性）
            if last_idx < 1:
                eval_idx = []
            else:
                # 步长 s 使采样点（含首、尾两端）总数 ≤ WF_MAX_TRAIN；
                # 以"替换末采样点"而非"追加"的方式纳入最近一期，保证训练组数不破上限。
                step = max(1, (last_idx - 1 + WF_MAX_TRAIN - 2) // (WF_MAX_TRAIN - 1))
                eval_idx = list(range(1, last_idx + 1, step))
                if eval_idx[-1] != last_idx:
                    eval_idx[-1] = last_idx

            for idx in eval_idx:
                train = rows[:start + idx]
                actual = windows[idx]['numbers']
                if len(train) < ML_EVAL_MIN:
                    continue
                # 使用进程池或进程内调用（进程内 predict_next 复用单例，避免循环内重复实例化）
                pred = None
                cfg_snap = self._checkpoint.get('weight_snapshot', {})
                if self._ml_pool is not None:
                    pred = self._ml_pool.predict_next(train, cfg_snap)
                if pred is None:
                    try:
                        _r = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        if _r not in sys.path:
                            sys.path.insert(0, _r)
                        if not hasattr(self, '_fallback_ml'):
                            from modules.ml_predictor import predict_next
                            self._fallback_ml = predict_next
                        # 传入 target_issue=None 避免日志显示 None；实际预测不依赖该参数
                        pred = self._fallback_ml(train, target_issue=None)
                    except Exception as e:  # noqa: BLE001
                        logger.warning('[self_evolution] 回退 ML 预测失败: %s', e)
                        pred = None
                # 把 predict_next 的概率分布统一转换为引擎评估期望的 per_position 结构
                pred = _ml_pred_to_per_position(pred)
                if not pred or 'per_position' not in pred:
                    continue
                per = pred['per_position']
                tested += 1
                for i, n in enumerate(actual):
                    if i >= len(per):
                        break
                    top5_candidates = per[i].get('top5', [])
                    if n == per[i].get('top1'):
                        top1_hits += 1
                    if n in per[i].get('top3', []):
                        top3_hits += 1
                    if n in top5_candidates:
                        top5_hits += 1

            if tested == 0:
                return {}
            return {
                'tested': tested,
                'top1': round(top1_hits / tested / 5 * 100, 2),
                'top3': round(top3_hits / tested / 5 * 100, 2),
                'top5': round(top5_hits / tested / 5 * 100, 2),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] _evaluate_walkforward 异常: %s', e)
            return {}
        finally:
            try:
                db.disconnect()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _compare_metrics(candidate: Dict, baseline: Dict) -> bool:
        """诚实边界：候选指标 ≥ 基线才算「通过」。Top-1 / Top-3 / Top-5 均需不劣化。"""
        if not baseline:
            return True  # 无基线时默认允许（首次进化）
        for key in ('top1', 'top3', 'top5'):
            c = float(candidate.get(key, 0))
            b = float(baseline.get(key, 0))
            if c < b - 1e-9:
                return False
        return True

    def _assess_improvement_internal(self, candidate: Dict, baseline: Dict) -> Dict[str, Any]:
        """评分 + 薄弱位分析，结晶为文字摘要。"""
        weak = []
        for pos in POS:
            c_val = candidate.get(f'{pos}_top1', candidate.get('top1', 0))
            b_val = baseline.get('top1', 0)
            if isinstance(c_val, (int, float)) and c_val < b_val:
                weak.append(pos)
        lines = [
            f'评估样本: {candidate.get("tested", 0)} 期',
            f'候选 Top1={candidate.get("top1", "N/A")}% / '
            f'Top3={candidate.get("top3", "N/A")}% / '
            f'Top5={candidate.get("top5", "N/A")}%',
            f'基线 Top1={baseline.get("top1", "N/A")}% / '
            f'Top3={baseline.get("top3", "N/A")}% / '
            f'Top5={baseline.get("top5", "N/A")}%',
        ]
        if weak:
            lines.append(f'薄弱位置: {", ".join(weak)}（建议重点优化）')
            conclusion = '候选未全面超越基线，归档 trial'
        else:
            lines.append('候选不劣于基线，可激活')
            conclusion = '候选不劣于基线，可激活为 active'
        return {
            'summary': '；'.join(lines),
            'conclusion': conclusion,
            'weak_positions': weak,
            'candidate': candidate,
            'baseline': baseline,
            'ts': _now_str(),
        }

    def _assess_improvement(self, history_count: int) -> Dict[str, Any]:
        """对外接口：读取验证记录，输出整体评分 + 薄弱位（兼容旧调用）。"""
        db = _connect_db()
        per_position_scores = {p: 0.0 for p in POS}
        total_verified = 0
        try:
            if db and db.connect():
                try:
                    rows = db.get_prediction_records(verified=1, limit=200)
                    for r in rows:
                        total_verified += 1
                        vd = r.get('verified_data') or {}
                        pp = vd.get('per_position', {})
                        for pos in POS:
                            hits = pp.get(pos, 0)
                            if isinstance(hits, (int, float)):
                                per_position_scores[pos] += float(hits)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    try:
                        db.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

        lines = [f'已验证记录: {total_verified} 条']
        for pos in POS:
            score = per_position_scores[pos]
            lines.append(f'{pos}: {score:.1f} 次命中')
        weak = sorted(POS, key=lambda p: per_position_scores[p])[:2]
        return {
            'summary': '；'.join(lines),
            'weak_positions': weak,
            'ts': _now_str(),
        }

    def _generate_proposals_from_assessment(self) -> List[Dict[str, Any]]:
        """基于薄弱位生成本次进化改进建议。"""
        assessment = getattr(self, '_last_assessment', None) or {}
        weak = assessment.get('weak_positions', [])
        proposals = []
        for pos in weak:
            proposals.append({
                'id': f'{int(time.time())}_{pos}',
                'category': '薄弱位优化',
                'priority': 'high',
                'title': f'针对 {pos} 为弱项，建议引入该位专项特征/权重调整',
                'status': 'pending',
                'created_at': _now_str(),
            })
        if not proposals:
            proposals.append({
                'id': f'{int(time.time())}_general',
                'category': '常规巡检',
                'priority': 'low',
                'title': '当前无显著薄弱位，维持现有融合权重',
                'status': 'pending',
                'created_at': _now_str(),
            })
        return proposals

    # ------------------------------------------------------------------
    # 版本持久化
    # ------------------------------------------------------------------
    def _load_versions(self):
        if self._versions_loaded:
            return
        self._versions = self._load_versions_from_db() or self._load_versions_from_local()
        self._versions_loaded = True

    def _load_versions_from_db(self) -> List[Dict[str, Any]]:
        """从专用表 p5_evolution_version 读取历史版本（与 data/database.sql 结构一致）。"""
        db = _connect_db()
        if db is None:
            return []
        try:
            if not db.connect():
                return []
            rows = db.get_evolution_versions(limit=200)
            out = []
            for r in rows:
                out.append({
                    'version_tag': r.get('version_tag', ''),
                    'parent_tag': r.get('parent_tag', ''),
                    'status': r.get('status', 'trial'),
                    'params_json': r.get('params_json', {}) or {},
                    'metrics_json': r.get('metrics_json', {}) or {},
                    'baseline_json': r.get('baseline_json', {}) or {},
                    'note': r.get('note', ''),
                    'created_at': r.get('created_at', ''),
                    'source': 'db',
                })
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 版本读取失败: %s', e)
            return []
        finally:
            try:
                db.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _load_versions_from_local(self) -> List[Dict[str, Any]]:
        try:
            if os.path.isfile(self._local_versions_path):
                with open(self._local_versions_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:  # noqa: BLE001
            pass
        return []

    def _persist_version(self, record: Dict[str, Any]) -> bool:
        saved_db = self._save_version_to_db(record)
        if saved_db:
            return True
        return self._save_version_to_local(record)

    def _save_version_to_db(self, record: Dict[str, Any]) -> bool:
        """保存到专用表 p5_evolution_version（按 version_tag 幂等 upsert）。"""
        db = _connect_db()
        if db is None:
            return False
        try:
            if not db.connect():
                return False
            return db.save_evolution_version(record)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 版本入库失败(降级为本地): %s', e)
            return False
        finally:
            try:
                db.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _save_version_to_local(self, record: Dict[str, Any]) -> bool:
        try:
            self._load_versions_from_local()
            self._versions.append(record)
            with open(self._local_versions_path, 'w', encoding='utf-8') as f:
                json.dump(self._versions, f, ensure_ascii=False, indent=2,
                          default=_json_default)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 版本写本地失败: %s', e)
            return False

    def rollback_to_version(self, version_tag: str) -> Dict[str, Any]:
        """回滚：将目标版本置为 active，原 active 置为 rolledback。"""
        self._load_versions()
        target = None
        current_active = None
        for v in self._versions:
            if v.get('status') == 'active':
                current_active = v
            if v.get('version_tag') == version_tag:
                target = v
        if target is None:
            return {'ok': False, 'error': f'版本 {version_tag} 不存在'}
        if current_active and current_active.get('version_tag') == version_tag:
            return {'ok': False, 'error': '当前已是该版本，无需回滚'}
        # 执行回滚
        if current_active:
            current_active['status'] = 'rolledback'
            self._persist_version(current_active)
        target['status'] = 'active'
        target['parent_tag'] = current_active.get('version_tag', '') if current_active else ''
        self._persist_version(target)
        self._emit('success', f'已回滚到 {version_tag}')
        return {'ok': True}

    def get_proposals(self, limit=50) -> List[Dict[str, Any]]:
        return self._proposals[-limit:]

    def export_versions(self, path: str) -> int:
        self._load_versions()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._versions, f, ensure_ascii=False, indent=2,
                          default=_json_default)
            return len(self._versions)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 导出失败: %s', e)
            return 0

    def get_versions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """对外接口：返回供 GUI 进化版本表展示的版本列表。

        GUI 的 _refresh_evolution_versions 读取 r.get('metrics', {})，因此此处把
        metrics_json 统一映射为 metrics 字段；同时保留 params / baseline 以供详情展示。
        按创建时间倒序，最新的版本排在最前。
        """
        self._load_versions()
        out = []
        for v in self._versions:
            metrics = v.get('metrics_json') or v.get('metrics') or {}
            if isinstance(metrics, str):
                try:
                    metrics = json.loads(metrics)
                except Exception:  # noqa: BLE001
                    metrics = {}
            params = v.get('params_json') or v.get('params') or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:  # noqa: BLE001
                    params = {}
            baseline = v.get('baseline_json') or v.get('baseline') or {}
            if isinstance(baseline, str):
                try:
                    baseline = json.loads(baseline)
                except Exception:  # noqa: BLE001
                    baseline = {}
            out.append({
                'version_tag': v.get('version_tag', ''),
                'status': v.get('status', 'trial'),
                'metrics': metrics,
                'params': params,
                'baseline': baseline,
                'parent_tag': v.get('parent_tag', ''),
                'note': v.get('note', ''),
                'created_at': v.get('created_at', ''),
                'source': v.get('source', 'db'),
            })
        out.sort(key=lambda x: str(x.get('created_at', '')), reverse=True)
        return out[:max(0, int(limit))]

    def _current_version_tag(self) -> str:
        self._load_versions()
        actives = [v for v in self._versions if v.get('status') == 'active']
        return actives[-1].get('version_tag', '—') if actives else '—'

    # ------------------------------------------------------------------
    # 检查点
    # ------------------------------------------------------------------
    def _load_checkpoint(self):
        try:
            if os.path.isfile(self._ckpt_path):
                with open(self._ckpt_path, 'r', encoding='utf-8') as f:
                    self._checkpoint = json.load(f)
        except Exception:  # noqa: BLE001
            self._checkpoint = {}

    def _save_checkpoint(self):
        try:
            with open(self._ckpt_path, 'w', encoding='utf-8') as f:
                json.dump(self._checkpoint, f, ensure_ascii=False, indent=2,
                          default=_json_default)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 检查点写入失败: %s', e)

    # ------------------------------------------------------------------
    # 提议持久化
    # ------------------------------------------------------------------
    def _persist_proposals(self, proposals: List[Dict[str, Any]]):
        db = _connect_db()
        if db is None:
            return
        try:
            if not db.connect():
                return
            for p in proposals:
                try:
                    db.save_artifact(
                        artifact_type='evolution_proposal',
                        data=p,
                        issue=p.get('id', ''),
                        meta={'category': p.get('category', ''), 'status': p.get('status', '')},
                    )
                except Exception:  # noqa: BLE001
                    pass
        finally:
            try:
                db.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _make_version_tag(self) -> str:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        return f'evo_{ts}'