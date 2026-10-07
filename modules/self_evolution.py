# -*- coding: utf-8 -*-
"""
self_evolution.py — 自我学习 / 自我训练 / 自我进化引擎（排列5）

【v3.52 完整实现（2026-08-15 重建）】
六阶段流水线：collect → baseline → evolve → evaluate → persist → done
后台守护线程 + queue 通信 + 检查点续跑 + DB 版本持久化 + 诚实边界闸门。

【v3.62 增强】
- 数据质量校验：过滤异常行（位数不符、数字越界），提升输入数据质量
- 自适应学习：动态采样步长加速坐标下降迭代
- 功能使用日志：完整的阶段开始/完成事件，便于问题排查和性能分析
- 进化过程可视化：向 GUI 推送阶段进度事件
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
from typing import Optional, List, Dict, Any, Tuple

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

def _safe_subprocess_run(args, timeout=2, **kwargs):
    """
    安全执行 subprocess 命令，统一参数校验与超时控制。
    参数:
        args: list of str, 命令及其参数
        timeout: 超时秒数，默认 2 秒
        **kwargs: 其他传递给 subprocess.run 的参数
    返回:
        subprocess.CompletedProcess 对象的 stdout（已 strip）
    异常:
        将异常捕获并返回空字符串，以免影响主流程。
    """
    import subprocess
    # 参数校验：确保 args 是列表且所有元素为字符串
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        logging.warning(f"_safe_subprocess_run: 参数非法 {args}")
        return ""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
        return result.stdout.strip()
    except Exception as e:
        logging.warning(f"_safe_subprocess_run: 命令执行失败 {args}: {e}")
        return ""

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


# =====================================================================
# Wilson 95% 置信区间 + CI 重叠闸门（诚实边界核心）
# =====================================================================
import math

def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for binomial proportion.
    
    Returns (lower, upper) bounds clamped to [0, 1].
    """
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def ci_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Check if two CIs overlap."""
    return not (a[1] < b[0] or b[1] < a[0])


def _gate_pass(cand: Dict, base: Dict) -> bool:
    """Wilson CI 严格闸门：候选 CI 整体高于基线 CI 且不重叠才通过。
    
    返回 True = 候选显著优于基线（可激活为 active）
    返回 False = CI 重叠或不优（归档为 trial）
    """
    # 需要 hits 和 n（测试期数 * 5 位置）
    c_top1_hits = int(cand.get('top1_hits', 0))
    c_top3_hits = int(cand.get('top3_hits', 0))
    c_top5_hits = int(cand.get('top5_hits', 0))
    c_n = int(cand.get('tested', 0)) * 5
    
    b_top1_hits = int(base.get('top1_hits', 0))
    b_top3_hits = int(base.get('top3_hits', 0))
    b_top5_hits = int(base.get('top5_hits', 0))
    b_n = int(base.get('tested', 0)) * 5
    
    # 如果没有 hits 信息，回退到点估计比较（兼容旧版本记录）
    if c_n == 0 or b_n == 0:
        c_top1 = float(cand.get('top1', 0))
        b_top1 = float(base.get('top1', 0))
        return c_top1 > b_top1 + 1e-9
    
    c_ci = wilson_ci(c_top1_hits, c_n)
    b_ci = wilson_ci(b_top1_hits, b_n)
    
    # 严格条件：候选 CI 下界 > 基线 CI 上界（不重叠且整体更优）
    return (not ci_overlap(c_ci, b_ci)) and (c_ci[0] > b_ci[1])


# =====================================================================
# 防泄漏断言（运行时自检）
# =====================================================================
def _assert_no_leak(train_rows: list, eval_issue: Any) -> None:
    """运行时断言：确保训练集不包含评估期或未来数据。
    
    Raises:
        AssertionError: 如果检测到前视泄漏
    """
    eval_issue_int = int(eval_issue)
    train_issues = {int(r['issue']) for r in train_rows if r.get('issue') is not None}
    if eval_issue_int in train_issues:
        raise AssertionError(f'LEAK: eval issue {eval_issue_int} in train set')
    if train_issues and max(train_issues) >= eval_issue_int:
        raise AssertionError(f'LEAK: future issue {max(train_issues)} >= eval {eval_issue_int} in train')


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
    """将原始 DB 行转为 ml_predictor.predict_next 期望的格式。

    v3.62 增强：增加数据质量校验，过滤异常行（如位数不符、数字越界），
    提升输入数据质量，减少下游计算异常。
    """
    out = []
    valid_count = 0
    invalid_count = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        nums = [r.get('wan'), r.get('qian'), r.get('bai'),
                r.get('shi'), r.get('ge')]
        if any(x is None for x in nums):
            invalid_count += 1
            continue
        # 类型安全转换：过滤非整数或越界值
        int_nums = []
        valid = True
        for x in nums:
            try:
                v = int(x)
                if v < 0 or v > 9:
                    valid = False
                    break
                int_nums.append(v)
            except (ValueError, TypeError):
                valid = False
                break
        if not valid:
            invalid_count += 1
            continue
        out.append({'issue': str(r.get('issue', '')),
                    'numbers': int_nums})
        valid_count += 1
    if invalid_count > 0:
        logger.info('[self_evolution] 数据质量校验: 有效行=%s, 无效行=%s', valid_count, invalid_count)
    return out


# =====================================================================
# AutoEvoScheduler（保持原样）
# =====================================================================

class AutoEvoScheduler:
    """后台静默调度器：控制 SelfEvolutionEngine 的自动触发频率，避免空转。

    §3.6 自愈：内置「连续 N 次 DB 不可达 → 暂停并告警」计数。
    自我进化六阶段强依赖数据库（历史开奖 / 进化版本表），若 DB 持续不可达，
    引擎反复触发只会降级空转并刷屏告警。本计数在 DB 连续失败达到阈值后
    自动暂停自动调度并产出告警，待 DB 恢复且用户显式 resume 后再触发，
    防止「静默空转 + 无效进化」的资源浪费。
    """

    DEFAULT_INTERVAL_HOURS = 24
    # DB 连续不可达多少次后暂停自动调度（24h 间隔下 5 次 ≈ 5 天，给足恢复窗口）
    DB_FAIL_THRESHOLD = 5

    def __init__(self, engine, data_dir=None, interval_hours=None):
        self.engine = engine
        self.interval_hours = interval_hours or self.DEFAULT_INTERVAL_HOURS
        if data_dir is None:
            try:
                from paths import PROJECT_ROOT
                data_dir = os.path.join(PROJECT_ROOT, 'data')
            except Exception:  # noqa: BLE001
                data_dir = os.path.join(os.getcwd(), 'data')
        self.data_dir = data_dir
        self.schedule_path = os.path.join(data_dir, 'self_evolution_schedule.json')
        self._timer = None
        # §3.6：DB 连续不可达计数（持久化到 schedule_path 同目录，跨进程重启保留）
        self.db_fail_state_path = os.path.join(data_dir, 'self_evolution_db_fail.json')
        self._consecutive_db_fail = 0
        self._db_paused = False
        self._load_db_fail_state()

    # ------------------------------------------------------------------
    # §3.6 DB 不可达自愈计数
    # ------------------------------------------------------------------
    def _load_db_fail_state(self):
        """进程重启后恢复上次的 DB 不可达计数与暂停标志。"""
        try:
            if os.path.isfile(self.db_fail_state_path):
                with open(self.db_fail_state_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._consecutive_db_fail = int(data.get('consecutive_db_fail', 0))
                self._db_paused = bool(data.get('db_paused', False))
        except Exception:  # noqa: BLE001
            self._consecutive_db_fail = 0
            self._db_paused = False

    def _save_db_fail_state(self):
        try:
            with open(self.db_fail_state_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'consecutive_db_fail': self._consecutive_db_fail,
                    'db_paused': self._db_paused,
                    'updated_at': _now_str(),
                }, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    def report_db_failure(self) -> int:
        """引擎在 DB 连接失败时调用：累加计数，达到阈值则暂停并告警。"""
        self._consecutive_db_fail += 1
        if self._consecutive_db_fail >= self.DB_FAIL_THRESHOLD and not self._db_paused:
            self._db_paused = True
            self._save_db_fail_state()
            logger.warning(
                '[AutoEvoScheduler] DB 连续不可达 %d 次（阈值 %d），已暂停自动调度',
                self._consecutive_db_fail, self.DB_FAIL_THRESHOLD)
            self._emit_db_pause_alert()
        else:
            self._save_db_fail_state()
        return self._consecutive_db_fail

    def report_db_success(self):
        """引擎在 DB 连接成功时调用：清零计数并自动恢复调度。"""
        if self._consecutive_db_fail != 0 or self._db_paused:
            logger.info(
                '[AutoEvoScheduler] DB 已恢复（此前连续失败 %d 次），清零计数并恢复调度',
                self._consecutive_db_fail)
        self._consecutive_db_fail = 0
        self._db_paused = False
        self._save_db_fail_state()

    def is_db_paused(self) -> bool:
        """当前是否因 DB 连续不可达而处于自动调度暂停。"""
        return self._db_paused

    def resume_db(self):
        """用户显式恢复：DB 恢复后由 GUI/人工调用本方法重新启用自动调度。"""
        self._consecutive_db_fail = 0
        self._db_paused = False
        self._save_db_fail_state()
        logger.info('[AutoEvoScheduler] 用户显式恢复 DB 自动调度')

    def _emit_db_pause_alert(self):
        """暂停时向 GUI 投递一条告警消息（若引擎队列可用）。"""
        try:
            eng = self.engine
            if eng is not None and hasattr(eng, '_emit'):
                eng._emit('warning',
                          f'自动进化调度已暂停：数据库连续不可达 '
                          f'{self._consecutive_db_fail} 次（阈值 {self.DB_FAIL_THRESHOLD}）')
        except Exception:  # noqa: BLE001
            pass

    def should_run(self) -> bool:
        # §3.6：DB 连续不可达已暂停时，不再自动触发，避免静默空转
        if self._db_paused:
            return False
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
    """隔离 ml_predictor 原生崩溃：优先 spawn 子进程，失败时降级为进程内直接调用。"""

    def __init__(self, max_workers=2, timeout=60):
        self._timeout = timeout
        self._use_subprocess = False
        # v3.67: Windows 上禁用 multiprocessing.Pool ——
        # spawn 启动方式会为每个 worker 弹出独立的控制台窗口（子进程反复打印命令），
        # 且 Windows 无法对 GUI 进程的子进程隐藏控制台；同时该项目里 sklearn 缺失降级为
        # numpy-only（ml_predictor 已不依赖 sklearn），无需子进程隔离，直接进程内执行即可。
        if sys.platform == 'win32':
            self._pool = None
            logger.info('[MLPredictorPool] Windows 平台，跳过 multiprocessing.Pool 创建，直接进程内执行')
            return
        try:
            self._pool = multiprocessing.Pool(
                processes=max_workers, maxtasksperchild=50
            )
            self._use_subprocess = True
        except Exception:  # noqa: BLE001
            # 无法创建子进程，降级为进程内
            self._pool = None

    def predict_next(self, sorted_data, cfg_snapshot):
        """在子进程（或进程内）调用 ml_predictor.predict_next，超时/崩溃均返回 None。"""
        if self._use_subprocess and self._pool is not None:
            try:
                result = self._pool.apply_async(
                    _ml_predictor_worker_entry,
                    (sorted_data, cfg_snapshot),
                )
                return result.get(timeout=self._timeout)
            except Exception as e:  # noqa: BLE001
                logger.warning('[MLPredictorPool] 子进程调用失败(%s)，降级为进程内: %s', e, type(e).__name__)
                self._use_subprocess = False
                self._pool = None

        # 降级：进程内直接调用（ml_predictor 已无 sklearn 依赖，无需隔离）
        try:
            return _ml_predictor_worker_entry(sorted_data, cfg_snapshot)
        except Exception as e:  # noqa: BLE001
            logger.warning('[MLPredictorPool] 进程内调用也失败: %s', e)
            return None

    def shutdown(self):
        if self._pool is not None:
            try:
                # v3.65: 先 close() 让正在执行的任务完成，再 terminate() 强制终止
                # 直接 terminate() 会导致子进程持有多重锁时无法释放，引发白屏卡顿
                try:
                    self._pool.close()       # 停止接受新任务
                    self._pool.join(timeout=3)  # 等待现有任务完成
                except Exception:
                    pass
                self._pool.terminate()       # 强制终止所有 worker
                self._pool.join(timeout=3)   # 等待 worker 退出
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._pool = None
                self._use_subprocess = False
            # v3.65: Windows 上 terminate() 后仍有残留子进程，强制 cleanup
            self._kill_orphan_workers()

    def _kill_orphan_workers(self):
        """v3.65: Windows 上强制清理 terminate() 后残留的 SpawnPoolWorker 子进程。

        Windows 上 multiprocessing.Pool.terminate() 只能发送 CTRL_BREAK_EVENT，
        但子进程若在获取 multiprocessing.Lock 时被中断，会卡在锁释放前，
        导致主进程 join() 超时，进而阻塞 tkinter 窗口退出（白屏）。
        此处通过 os.kill 发送 SIGTERM 强制终止残留进程。
        """
        import os as _os
        # 先清理 multiprocessing 管理的活跃子进程
        for proc in multiprocessing.active_children():
            if proc.is_alive():
                try:
                    proc.terminate()
                    proc.join(timeout=1)
                except Exception:
                    pass
        # Windows 兜底：通过 pid 精确 kill 本进程的子进程（避免误杀其他 python）
        if sys.platform == 'win32':
            try:
                import subprocess
                # 获取当前进程的 pid，再 kill 其所有子进程
                parent_pid = os.getpid()
                # v3.67: creationflags 防止 tasklist 弹出黑色控制台窗口
                stdout = _safe_subprocess_run(
                    ['tasklist', '/FO', 'CSV', '/NH', '/FI',
                     f'PARENTPROCESSID eq {parent_pid}'],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in stdout.splitlines():
                    try:
                        # CSV 格式: "python.exe","1234",...
                        fields = line.split('","')
                        pid_str = fields[1].strip('"')
                        if pid_str.isdigit():
                            os.kill(int(pid_str), 9)
                    except Exception:
                        pass
            except Exception:
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
# 遗传算法搜索器（小空间 GA，与坐标下降互补）
# =====================================================================
import random
import subprocess


class GeneticAlgorithmTuner:
    """小空间遗传算法搜索器（种群≤12/代≤20/变异率0.15）。

    与 DeepTuner（坐标下降）互补：
    - 坐标下降：局部精细搜索，适合凸/近凸空间
    - GA：全局探索，能跳出局部最优，适合非凸/离散空间
    
    搜索空间（可通过 config.GA_CONFIG 覆盖）：
    - 权重：frequency_weighted, ml_supervised, bayesian_inference, markov_transition
    - lookback: 40-80
    - feature_subset: auto（固定）
    """
    
    def __init__(
        self,
        population_size: int = 12,
        generations: int = 20,
        mutation_rate: float = 0.15,
        crossover: str = 'blend',
        search_space: Optional[Dict] = None,
    ):
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover = crossover
        self.search_space = search_space or {
            'weights': ['frequency_weighted', 'ml_supervised', 'bayesian_inference', 'markov_transition'],
            'lookback_range': [40, 80],
            'feature_subset': 'auto',
        }
        self._eval_cache: Dict[str, Dict] = {}
    
    def _random_individual(self, base_weights: Dict[str, float], base_lookback: int) -> Dict[str, Any]:
        """生成随机个体（权重在基线附近扰动，lookback 在范围内随机）"""
        import copy
        weights = copy.deepcopy(base_weights)
        # 权重扰动：每个权重 ±20% 均匀分布，然后重归一化
        for k in weights:
            if k in self.search_space['weights']:
                weights[k] *= random.uniform(0.8, 1.2)
        total = sum(max(0.0, v) for v in weights.values())
        if total > 0:
            weights = {k: max(0.0, v) / total for k, v in weights.items()}
        
        lookback = random.randint(
            self.search_space['lookback_range'][0],
            self.search_space['lookback_range'][1]
        )
        return {'weights': weights, 'lookback': lookback}
    
    def _crossover(self, parent1: Dict, parent2: Dict) -> Dict[str, Any]:
        """混合交叉"""
        child_weights = {}
        for k in parent1['weights']:
            if self.crossover == 'blend':
                alpha = random.random()
                child_weights[k] = alpha * parent1['weights'][k] + (1 - alpha) * parent2['weights'][k]
            else:  # uniform
                child_weights[k] = random.choice([parent1['weights'][k], parent2['weights'][k]])
        
        # 归一化
        total = sum(max(0.0, v) for v in child_weights.values())
        if total > 0:
            child_weights = {k: max(0.0, v) / total for k, v in child_weights.items()}
        
        # lookback 取父代之一
        child_lookback = random.choice([parent1['lookback'], parent2['lookback']])
        return {'weights': child_weights, 'lookback': child_lookback}
    
    def _mutate(self, individual: Dict, base_weights: Dict) -> Dict[str, Any]:
        """变异：权重高斯扰动，lookback 随机跳变"""
        import copy
        weights = copy.deepcopy(individual['weights'])
        for k in weights:
            if k in self.search_space['weights'] and random.random() < self.mutation_rate:
                weights[k] *= random.gauss(1.0, 0.1)
                weights[k] = max(0.0, weights[k])
        total = sum(max(0.0, v) for v in weights.values())
        if total > 0:
            weights = {k: max(0.0, v) / total for k, v in weights.items()}
        
        if random.random() < self.mutation_rate:
            lookback = random.randint(
                self.search_space['lookback_range'][0],
                self.search_space['lookback_range'][1]
            )
        else:
            lookback = individual['lookback']
        return {'weights': weights, 'lookback': lookback}
    
    def _make_key(self, weights: Dict, lookback: int) -> str:
        """生成缓存键"""
        w_str = ','.join(f'{k}:{v:.4f}' for k, v in sorted(weights.items()))
        return f'lb{lookback}|{w_str}'
    
    def tune(
        self,
        base_weights: Dict[str, float],
        windows: List[Tuple[List[Dict], List[int]]],
        base_lookback: int = 60,
        baseline_metrics: Optional[Dict] = None,
        fixed_keys: tuple = (),
        provider=None,  # PredictorComponentProvider
    ) -> Dict[str, Any]:
        """运行 GA 搜索，返回最优个体"""
        logger.info('[GA] 开始遗传算法搜索: pop=%d, gen=%d, mut=%.2f', 
                    self.population_size, self.generations, self.mutation_rate)
        
        # 初始化种群：基线个体 + 随机个体
        population = [{'weights': base_weights, 'lookback': base_lookback}]
        while len(population) < self.population_size:
            population.append(self._random_individual(base_weights, base_lookback))
        
        best_individual = None
        best_metrics = None
        candidates_evaluated = 0
        
        for gen in range(self.generations):
            # 评估种群
            scored_pop = []
            for ind in population:
                key = self._make_key(ind['weights'], ind['lookback'])
                if key in self._eval_cache:
                    metrics = self._eval_cache[key]
                else:
                    # 评估个体（使用 provider 做廉价融合评估，类似 DeepTuner）
                    if provider is not None:
                        from modules.evolution_tuner import _evaluate_candidate
                        metrics = _evaluate_candidate(
                            provider, windows, ind['weights'], ind['lookback'],
                            fixed_keys=fixed_keys, baseline_metrics=baseline_metrics
                        )
                    else:
                        metrics = {'tested': 0, 'top1': 0, 'top3': 0, 'top5': 0}
                    self._eval_cache[key] = metrics
                    candidates_evaluated += 1
                
                scored_pop.append((ind, metrics))
            
            # 选择最优
            scored_pop.sort(key=lambda x: x[1].get('top1', 0), reverse=True)
            if best_individual is None or scored_pop[0][1].get('top1', 0) > best_metrics.get('top1', 0):
                best_individual = scored_pop[0][0]
                best_metrics = scored_pop[0][1]
            
            # 精英保留 + 生成下一代
            elite_count = max(1, self.population_size // 4)
            next_gen = [ind for ind, _ in scored_pop[:elite_count]]
            
            while len(next_gen) < self.population_size:
                # 锦标赛选择
                p1 = random.choice(scored_pop[:self.population_size // 2])[0]
                p2 = random.choice(scored_pop[:self.population_size // 2])[0]
                child = self._crossover(p1, p2)
                child = self._mutate(child, base_weights)
                next_gen.append(child)
            
            population = next_gen
            logger.debug('[GA] Gen %d: best_top1=%.2f%%', gen + 1, best_metrics.get('top1', 0))
        
        # 最终评估最佳个体（如果是缓存命中则直接用）
        best_key = self._make_key(best_individual['weights'], best_individual['lookback'])
        if best_key in self._eval_cache:
            best_metrics = self._eval_cache[best_key]
        
        logger.info('[GA] 搜索完成: 候选=%d, 最优 Top1=%.2f%%, lookback=%d',
                    candidates_evaluated, best_metrics.get('top1', 0), best_individual['lookback'])
        
        return {
            'weights': best_individual['weights'],
            'lookback': best_individual['lookback'],
            'metrics': best_metrics,
            'candidates_evaluated': candidates_evaluated,
            'cache_hits': sum(1 for _ in self._eval_cache.values()),
            'cache_misses': 0,
            'elapsed_ms': 0,  # 由调用方计时
            'improved': best_metrics.get('top1', 0) > (baseline_metrics or {}).get('top1', 0) if baseline_metrics else False,
            'baseline_metrics': baseline_metrics or {},
        }


# =====================================================================
# 实验追踪器（写超参+数据指纹+git哈希到 DB + 本地 JSONL）
# =====================================================================
class ExperimentTracker:
    """实验追踪：每次 persist 时记录完整实验元数据。
    
    存储：
    - DB: p5_evolution_version.metrics_json 扩展字段
    - 本地: evolution_runs.jsonl（追加，便于离线分析）
    
    字段：
    - version_tag, parent_tag, status
    - hyperparams: weights, lookback, search_backend, search_params
    - data_fingerprint: sample_count, latest_issue, data_hash
    - git_hash: 当前提交短哈希
    - timestamp
    """
    
    def __init__(self, data_dir: str, db=None):
        self._data_dir = data_dir
        self._db = db
        self._local_log = os.path.join(data_dir, 'evolution_runs.jsonl')
        os.makedirs(data_dir, exist_ok=True)
    
    def _get_git_hash(self) -> str:
        """获取当前 git 短哈希"""
        try:
            # v3.67: Windows 上加 CREATE_NO_WINDOW 防止 git 弹出黑色控制台窗口
            _extra = {}
            if sys.platform == 'win32':
                _extra['creationflags'] = subprocess.CREATE_NO_WINDOW
            stdout = _safe_subprocess_run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)),
                timeout=3, **_extra
            )
            if stdout:
                return stdout
        except Exception:  # noqa: BLE001
            pass
        return 'unknown'
    
    def _compute_data_fingerprint(self, hist_count: int, latest_issue: str) -> Dict:
        """计算数据指纹"""
        import hashlib
        data_str = f'{hist_count}|{latest_issue}|{len(windows) if "windows" in locals() else 0}'
        # 简化版：仅用样本量+最新期号
        return {
            'sample_count': hist_count,
            'latest_issue': latest_issue,
            'hash': hashlib.md5(data_str.encode()).hexdigest()[:12],
        }
    
    def record(
        self,
        version_tag: str,
        parent_tag: str,
        status: str,
        hyperparams: Dict,
        data_fingerprint: Dict,
        metrics: Dict,
        search_backend: str = 'coord',
    ) -> None:
        """记录实验（DB + 本地 JSONL）"""
        import datetime
        
        git_hash = self._get_git_hash()
        timestamp = datetime.datetime.now().isoformat()
        
        # 扩展的 metrics_json（存入 p5_evolution_version）
        extended_metrics = dict(metrics)
        extended_metrics.update({
            'hyperparams': hyperparams,
            'data_fingerprint': data_fingerprint,
            'git_hash': git_hash,
            'search_backend': search_backend,
            'tracked_at': timestamp,
        })
        
        # 1. 本地 JSONL 追加（永久保留，不受 DB 限制）
        record = {
            'version_tag': version_tag,
            'parent_tag': parent_tag,
            'status': status,
            'metrics': extended_metrics,
            'timestamp': timestamp,
        }
        try:
            with open(self._local_log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, default=_json_default) + '\n')
        except Exception as e:  # noqa: BLE001
            logger.warning('[ExperimentTracker] 本地日志写入失败: %s', e)
        
        # 2. DB 写入由 save_evolution_version 处理（此处仅准备 extended_metrics）
        self._last_extended_metrics = extended_metrics
        logger.info('[ExperimentTracker] 实验已记录: %s (%s), git=%s', version_tag, status, git_hash)


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

        # §4.2 更新流程可追溯台账（本地 JSONL，DB 不可达时仍可追溯/导出/回溯）
        # 每次更新（进化产版 + 手动回滚 + 自动回滚）都追加一条含
        # trigger_reason / execution_result / validation / auto_rolled_back 的记录。
        self._update_ledger_path = os.path.join(data_dir, 'update_ledger.jsonl')
        self._update_history: List[Dict[str, Any]] = []
        # 记录「上一个可用 active 版本 tag」，供更新异常时自动回退（§4.2.2(3)）
        self._last_usable_version_tag: Optional[str] = None

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
        # v3.57：启动时主动清理上次进程异常退出残留的卡死状态，
        # 避免 evolution_link_state.json 中 analysis_running=True 永久存在。
        if self._link_state.get('analysis_running'):
            if self._is_link_state_stale():
                logger.warning(
                    '[self_evolution] 启动时检测到联动状态残留卡死'
                    '（analysis_running=True 且 last_sync_ts 超时），已自动重置')
                self._force_reset_link_state()

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
    def start_silent_timer(self, delay: Optional[float] = None):
        self.stop_silent_timer()
        if delay is None:
            delay = 60.0
        # 限制极端值，避免过长或过短
        delay = max(30.0, min(delay, 3600.0))
        self._timer = threading.Timer(delay, self._silent_timer_tick)
        self._timer.daemon = True
        self._timer.start()

    def stop_silent_timer(self):
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._timer = None

    # ------------------------------------------------------------------
    # 联动状态自愈（v3.57）
    # ------------------------------------------------------------------
    # 联动暂停超过该时长视为「残留卡死」，自动重置。30 分钟足够覆盖任何正常
    # 分析任务的最长耗时（即便 AI 解读阶段超时也不会超过 10 分钟），又能保证
    # 真正的并发分析场景不会被误重置。
    _LINK_STALE_TIMEOUT = 30 * 60  # 秒

    def _is_link_state_stale(self) -> bool:
        """判断当前联动暂停状态是否已残留过久（进程异常退出未清理）。"""
        if not self._link_state.get('analysis_running'):
            return False
        ts_str = self._link_state.get('last_sync_ts')
        if not ts_str:
            return True  # 没有时间戳必然是异常状态
        try:
            ts = datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
            age = (datetime.datetime.now() - ts).total_seconds()
            return age > self._LINK_STALE_TIMEOUT
        except Exception:
            return True  # 时间戳解析失败，保守视为卡死

    def _force_reset_link_state(self):
        """强制重置联动状态为安全值，并持久化。

        用途：进程异常退出导致 analysis_running=True 卡死时，下一次引擎 tick
        会自动调用本方法恢复，后续定时器调度不再被永久挂起。
        """
        try:
            self._link_state['analysis_running'] = False
            self._link_state['last_sync_ts'] = _now_str()
            self._scheduler_paused = False
            self._save_link_state()
            self._emit('info', '联动状态超时已自动重置，引擎调度已恢复')
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 联动状态强制重置失败: %s', e)

    def _silent_timer_tick(self):
        """定时器回调：检查是否需要触发自我进化

        注意：此处不使用锁，因为 start() 方法在调用本方法前会先检查 _running 状态。
        若 start() 持有锁时定时器触发，可能会产生竞态；但通过以下方式避免：
        - _running 为线程安全标志（虽无锁保护，但布尔读取本身是原子的）
        - 若检测到 _running=True，则跳过启动

        v3.57 自愈：
            若联动暂停超过 ``_LINK_STALE_TIMEOUT`` 分钟仍未恢复（说明上一次分析
            进程异常退出，未走 notify_analysis_done 兜底），自动重置联动状态，
            防止引擎被永久卡在「联动暂停」分支、用户后续每次点击「开始分析」都
            要看这条无意义日志。

        v3.62 优化：
            - 跳过类日志降级为 DEBUG，减少日志噪音
            - 联动暂停时延长检测间隔至 5 分钟
            - 未到调度间隔时动态计算下次唤醒时间，最长 1 小时
        """
        next_delay = 60.0
        try:
            if self._scheduler_paused:
                if self._is_link_state_stale():
                    logger.warning(
                        '[self_evolution] 联动暂停超时（> %d 分钟），自动重置联动状态',
                        self._LINK_STALE_TIMEOUT // 60)
                    self._force_reset_link_state()
                else:
                    logger.debug('[self_evolution] 定时器 tick：联动暂停中，跳过本轮')
                next_delay = 300.0  # 暂停期间降低唤醒频率
            # §3.6：DB 连续不可达暂停时，进一步降低唤醒频率并记录日志，避免静默空转。
            # should_run() 在 DB 暂停时已恒返回 False，此处仅作观测与节流。
            elif self._scheduler.is_db_paused():
                logger.debug(
                    '[self_evolution] 定时器 tick：DB 连续不可达已暂停自动调度'
                    '（连续失败 %d 次），本轮跳过',
                    self._scheduler._consecutive_db_fail)
                next_delay = 3600.0  # DB 暂停期间每 1 小时检测一次恢复情况
            elif self._scheduler.should_run() and not self._running:
                logger.info('[self_evolution] 定时器 tick：检测到应触发自我进化，启动轻量自检…')
                self._running = True
                t = threading.Thread(target=self._run, daemon=True)
                t.start()
                next_delay = 60.0
            else:
                if self._running:
                    logger.debug('[self_evolution] 定时器 tick：引擎正在运行，跳过本轮触发')
                    next_delay = 60.0
                else:
                    logger.debug('[self_evolution] 定时器 tick：尚未到达调度间隔，跳过本轮')
                    # 动态计算下次检查时间，减少空转唤醒
                    try:
                        if os.path.isfile(self._scheduler.schedule_path):
                            with open(self._scheduler.schedule_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            last = data.get('last_run_ts')
                            if last is not None:
                                interval_sec = self._scheduler.interval_hours * 3600
                                remaining = interval_sec - (time.time() - float(last))
                                if remaining > 0:
                                    next_delay = max(60.0, min(remaining, 3600.0))
                                else:
                                    next_delay = 60.0
                    except Exception:
                        next_delay = 300.0
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 定时器 tick 异常: %s', e)
            next_delay = 300.0
        finally:
            # 确保定时器始终被重建，即使异常也继续调度
            self.start_silent_timer(delay=next_delay)

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
            # v3.62 新增：发送阶段开始事件，便于 GUI 展示进化过程可视化
            self._emit('info', f'▶ 阶段 {phase_real_idx + 1}/{len(phases)}: {phase} 开始执行')
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
            # v3.62 新增：发送阶段完成事件
            self._emit('info', f'◀ 阶段 {phase_real_idx + 1}/{len(phases)}: {phase} 执行完毕')

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
            # §3.6：DB 不可达计数——连续达到阈值后调度器暂停并告警
            try:
                self._scheduler.report_db_failure()
            except Exception:  # noqa: BLE001
                pass
            self._checkpoint['history_count'] = 0
            self._checkpoint['latest_issue'] = ''
            self._send('metrics', {'history_count': 0, 'latest_issue': ''})
            return

        try:
            if not db.connect():
                logger.warning('[self_evolution] _phase_collect: db.connect() 返回 False')
                # §3.6：连接失败同样计入 DB 不可达
                try:
                    self._scheduler.report_db_failure()
                except Exception:  # noqa: BLE001
                    pass
                self._checkpoint['history_count'] = 0
                self._checkpoint['latest_issue'] = ''
                self._send('metrics', {'history_count': 0, 'latest_issue': ''})
                return
            # §3.6：连接成功，清零 DB 不可达计数并（若之前暂停）自动恢复调度
            try:
                self._scheduler.report_db_success()
            except Exception:  # noqa: BLE001
                pass
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
        """读取当前生产预测器的「可调」统计类权重（排除冻结分量）。

        失败时不再静默回退硬编码（会掩盖配置错误），而是：
        1. 记录 ERROR 级日志
        2. 沿用上次成功快照（self._last_good_weights）
        3. 无快照时抛出 ConfigError（由调用方决定降级或中止）
        """
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
            result = {k: float(v) for k, v in weights.items() if k not in TUNING_FIXED_KEYS}
            # 缓存成功快照供后续失败时兜底
            self._last_good_weights = result
            return result
        except Exception as e:  # noqa: BLE001
            logger.error('[self_evolution] 读取统计权重失败（不再回退硬编码）: %s', e)
            if hasattr(self, '_last_good_weights') and self._last_good_weights:
                logger.info('[self_evolution] 沿用上次成功权重快照: %s', self._last_good_weights)
                return self._last_good_weights
            # 无历史快照时抛出，强制上层处理
            raise RuntimeError('ConfigError: 无法读取预测器权重且无历史快照可用') from e

    def _build_train_windows(self, eval_periods: int):
        """从 DB 读取历史并构造无前视 walk-forward 窗口。

        v3.62 增强：增加数据质量校验，过滤不完整期号；返回详细的窗口统计信息。

        返回: (windows, hist_count, quality_stats)
            windows: [(train_rows, actual_numbers), ...]
            quality_stats: {'total_rows', 'valid_rows', 'invalid_rows'}
        """
        db = _connect_db()
        if db is None:
            return [], 0, {'total_rows': 0, 'valid_rows': 0, 'invalid_rows': 0}
        try:
            if not db.connect():
                return [], 0, {'total_rows': 0, 'valid_rows': 0, 'invalid_rows': 0}
            rows = db.get_history_data(limit=None, order='ASC')
            if not rows:
                return [], 0, {'total_rows': 0, 'valid_rows': 0, 'invalid_rows': 0}

            # 数据质量统计
            total_rows = len(rows)
            valid_rows = sum(1 for r in rows if isinstance(r, dict) and all(r.get(p) is not None for p in POS))
            invalid_rows = total_rows - valid_rows

            rows = _row_to_sorted(rows)
            if len(rows) < ML_EVAL_MIN + 1:
                logger.warning('[self_evolution] 有效样本不足: %s < %s', len(rows), ML_EVAL_MIN)
                return [], len(rows), {'total_rows': total_rows, 'valid_rows': valid_rows, 'invalid_rows': invalid_rows}

            windows = build_walkforward_windows(
                rows, eval_periods=eval_periods,
                ml_eval_min=ML_EVAL_MIN, wf_max_train=WF_MAX_TRAIN)
            logger.info('[self_evolution] 窗口构造完成: 有效窗口数=%s, 总样本=%s, 有效样本=%s',
                        len(windows), total_rows, valid_rows)
            return windows, len(rows), {'total_rows': total_rows, 'valid_rows': valid_rows, 'invalid_rows': invalid_rows}
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 构造训练窗口失败: %s', e)
            return [], 0, {'total_rows': 0, 'valid_rows': 0, 'invalid_rows': 0}
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
        """阶段 3：参数迭代——深度调优（可插拔搜索后端：坐标下降/GA/两者）+ 采集权重快照。

        v4.0 升级：
          - 搜索后端由 config.SEARCH_BACKEND 控制："coord" | "ga" | "both"
          - 坐标下降（DeepTuner）：局部精细搜索，组件缓存加速
          - 遗传算法：全局探索，跳出局部最优，小空间（pop≤12, gen≤20, mut=0.15）
          - 两者并行时取更优候选
          - 所有候选经 Wilson CI 严格闸门（_gate_pass）评估，不优于基线归档 trial
        """
        import time
        start_ms = time.time()
        logger.info('[self_evolution] _phase_evolve 开始')
        self._emit('info', '参数迭代：深度调优（可插拔搜索后端）…')
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

        # 读取搜索后端配置
        from config import SEARCH_BACKEND, GA_CONFIG
        search_backend = SEARCH_BACKEND.lower() if isinstance(SEARCH_BACKEND, str) else 'both'
        logger.info('[self_evolution] 搜索后端: %s', search_backend)

        # ── 深度调优：构造窗口 → 搜索最优权重/lookback ──
        hist_count = self._checkpoint.get('history_count', 0)
        eval_periods = 10 if (self.auto and not self.auto_full) else 15
        logger.info('[self_evolution] 样本量=%s, auto=%s, auto_full=%s, eval_periods=%s',
                     hist_count, self.auto, self.auto_full, eval_periods)
        
        best_tuned = None
        quality_stats = {'total_rows': 0, 'valid_rows': 0, 'invalid_rows': 0}
        
        if hist_count >= ML_EVAL_MIN + 1:
            logger.info('[self_evolution] 样本量充足，开始构造训练窗口（eval_periods=%s）', eval_periods)
            try:
                windows, win_count, quality_stats = self._build_train_windows(eval_periods)
                logger.info('[self_evolution] 窗口构造完成: 有效窗口数=%s, 数据质量=%s', len(windows), quality_stats)
                if windows:
                    base_weights = self._get_statistical_weights()
                    logger.info('[self_evolution] 当前统计权重: %s', base_weights)
                    tuning_predictor = self._make_tuning_predictor()
                    if tuning_predictor is not None:
                        from modules.evolution_tuner import PredictorComponentProvider
                        provider = PredictorComponentProvider(tuning_predictor)
                        baseline_metrics = self._checkpoint.get('baseline', {}).get('metrics') if isinstance(
                            self._checkpoint.get('baseline'), dict) else None
                        
                        # 运行选定的搜索后端
                        candidates = []
                        
                        if search_backend in ('coord', 'both'):
                            logger.info('[self_evolution] 启动坐标下降搜索')
                            tuner = DeepTuner(
                                provider=provider,
                                delta=0.03, max_rounds=6,
                                enable_lookback_search=(not self.auto or self.auto_full),
                                lookback_candidates=(40, 60, 80),
                            )
                            tuned_coord = tuner.tune(
                                base_weights, windows,
                                base_lookback=60,
                                baseline_metrics=baseline_metrics,
                                fixed_keys=list(TUNING_FIXED_KEYS),
                            )
                            candidates.append(('coord', tuned_coord))
                        
                        if search_backend in ('ga', 'both'):
                            logger.info('[self_evolution] 启动遗传算法搜索')
                            ga_tuner = GeneticAlgorithmTuner(
                                population_size=GA_CONFIG.get('population_size', 12),
                                generations=GA_CONFIG.get('generations', 20),
                                mutation_rate=GA_CONFIG.get('mutation_rate', 0.15),
                                crossover=GA_CONFIG.get('crossover', 'blend'),
                                search_space=GA_CONFIG.get('search_space', {}),
                            )
                            tuned_ga = ga_tuner.tune(
                                base_weights, windows,
                                base_lookback=60,
                                baseline_metrics=baseline_metrics,
                                fixed_keys=list(TUNING_FIXED_KEYS),
                                provider=provider,
                            )
                            candidates.append(('ga', tuned_ga))
                        
                        # 选择最优候选（按 Top1 点估计，最终仍由 Wilson CI 闸门决定是否激活）
                        if candidates:
                            candidates.sort(key=lambda x: x[1]['metrics'].get('top1', 0), reverse=True)
                            best_name, best_tuned = candidates[0]
                            logger.info('[self_evolution] 最优候选来自 %s: Top1=%.2f%%', best_name, best_tuned['metrics'].get('top1', 0))
                            
                            # 记录所有候选供实验追踪
                            self._checkpoint['all_candidates'] = {name: t for name, t in candidates}
                        
                        if best_tuned:
                            self._checkpoint['tuned_weights'] = best_tuned['weights']
                            self._checkpoint['tuned_lookback'] = best_tuned['lookback']
                            self._checkpoint['tuned_metrics'] = best_tuned['metrics']
                            self._checkpoint['tuning_perf'] = {
                                'candidates_evaluated': sum(c[1]['candidates_evaluated'] for c in candidates),
                                'cache_hits': sum(c[1].get('cache_hits', 0) for c in candidates),
                                'cache_misses': sum(c[1].get('cache_misses', 0) for c in candidates),
                                'elapsed_ms': int((time.time() - start_ms) * 1000),
                                'improved': best_tuned['improved'],
                                'quality_stats': quality_stats,
                                'best_backend': best_name,
                            }
                            logger.info('[self_evolution] 深度调优结果: 候选=%s, 耗时=%sms, improved=%s, Top3=%s%%',
                                        self._checkpoint['tuning_perf']['candidates_evaluated'],
                                        self._checkpoint['tuning_perf']['elapsed_ms'],
                                        best_tuned['improved'],
                                        best_tuned['metrics'].get('top3'))
                            # 推送调优性能给 GUI
                            self._send('tuning_perf', {'elapsed_ms': self._checkpoint['tuning_perf']['elapsed_ms']})
                            self._emit('info',
                                       f'  深度调优完成（{best_name}）：候选 {self._checkpoint["tuning_perf"]["candidates_evaluated"]} 组, '
                                       f'耗时 {self._checkpoint["tuning_perf"]["elapsed_ms"]}ms, '
                                       f'Top3={best_tuned["metrics"].get("top3", "N/A")}% '
                                       f'(基线 {best_tuned["baseline_metrics"].get("top3", "N/A")}%)')
                            if best_tuned['improved']:
                                self._emit('success', f'  {best_name} 找到不劣于基线的候选配置')
                        else:
                            logger.warning('[self_evolution] 所有搜索后端均未产出候选')
                            self._emit('warning', '  所有搜索后端均未产出候选')
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
        """阶段 5：持久化——写版本表/文件（含深度调优候选权重/lookback/性能 + 实验追踪）。"""
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
        
        # 实验追踪：收集超参、数据指纹、git哈希
        from config import EXPERIMENT_TRACKING
        all_candidates = self._checkpoint.get('all_candidates', {})
        best_backend = tuning_perf.get('best_backend', 'coord') if tuning_perf else 'coord'
        hist_count = self._checkpoint.get('history_count', 0)
        latest_issue = self._checkpoint.get('latest_issue', '')
        
        hyperparams = {
            'search_backend': best_backend,
            'tuned_weights': tuned_weights,
            'tuned_lookback': tuned_lookback,
            'tuning_perf': tuning_perf,
            'all_candidates': {k: v['metrics'] for k, v in all_candidates.items()} if all_candidates else {},
            'baseline_metrics': baseline.get('metrics', {}) if isinstance(baseline, dict) else {},
        }
        
        data_fingerprint = {
            'sample_count': hist_count,
            'latest_issue': latest_issue,
        }
        
        version_tag = self._make_version_tag()
        parent_tag = baseline.get('version_tag', '') if isinstance(baseline, dict) else ''
        status = 'active' if beat else 'trial'
        # §4.2 可追溯：trigger_reason 记录「为什么触发本次更新」（方案明列必含字段）
        trigger_reason = self._checkpoint.get(
            'trigger_reason',
            ('自动进化调度(auto=%s, full=%s), beat_baseline=%s'
             % (self.auto, self.auto_full, beat)))
        record = {
            'version_tag': version_tag,
            'parent_tag': parent_tag,
            'status': status,
            'params_json': params_json,
            'metrics_json': ml_metrics,
            'baseline_json': baseline,
            'note': f'auto={self.auto}, full={self.auto_full}, beat={beat}, '
                    f'tuned={"yes" if tuned_weights else "no"}, backend={best_backend}',
            'created_at': _now_str(),
            'trigger_reason': trigger_reason,   # §4.2.2(2) 触发原因（可追溯）
        }
        logger.info('[self_evolution] 准备持久化版本: tag=%s, status=%s', version_tag, status)

        # §4.2.2(4) 更新前安全校验：版本兼容/数据完整/算法有效（本地、不依赖 DB）
        validation = self._pre_update_validation(record)
        record['validation'] = validation
        if not validation['ok']:
            logger.warning(
                '[self_evolution] 更新前校验未通过(fatal): tag=%s details=%s',
                version_tag, validation['details'])

        # 实验追踪器（DB + 本地 JSONL）
        tracker = ExperimentTracker(self._data_dir, db=None)  # DB 写入由 _persist_version 处理
        tracker.record(
            version_tag=version_tag,
            parent_tag=parent_tag,
            status=status,
            hyperparams=hyperparams,
            data_fingerprint=data_fingerprint,
            metrics=ml_metrics,
            search_backend=best_backend,
        )
        # 将扩展指标合并到记录的 metrics_json（存入 p5_evolution_version）
        if hasattr(tracker, '_last_extended_metrics'):
            record['metrics_json'] = tracker._last_extended_metrics

        saved = self._persist_version(record)
        if saved:
            self._versions.append(record)
            record['execution_result'] = 'success'
            self._last_usable_version_tag = version_tag
            # §4.2.2(2) 可追溯台账：进化产版成功也记录（含校验 + 执行结果）
            self._record_update(
                action='evolution_persist', version_tag=version_tag,
                trigger_reason=trigger_reason,
                execution_result='success',
                validation=validation,
                extra={'status': status, 'parent_tag': parent_tag,
                       'beat_baseline': beat})
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
            # §4.2.2(3) 更新异常自动回滚：持久化失败 → 回退上一可用版本 + 可追溯记录
            record['execution_result'] = 'failed'
            logger.warning('[self_evolution] 版本持久化失败: tag=%s，触发自动回滚', version_tag)
            rollback_result = self.auto_rollback_on_update_failure(
                version_tag, reason='持久化失败(DB/本地均不可达)')
            self._emit('warning', '  持久化失败，已自动回滚: ' + rollback_result['message'])
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
            # 防泄漏断言：训练集严格早于评估期
            _assert_no_leak(train, target['issue'])
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
                # 防泄漏断言：训练集严格早于评估期
                _assert_no_leak(train, windows[idx]['issue'])
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
        """Wilson 95% CI 严格闸门：候选 CI 整体高于基线 CI 且不重叠才通过。

        诚实边界 v4.0：替代点估计比较，杜绝随机波动误判。
        - 要求候选的 Top1 Wilson CI 下界 > 基线 Top1 Wilson CI 上界
        - 自动兼容旧版本记录（无 hits/n 时回退点估计）
        """
        if not baseline:
            return True
        
        # 使用模块级的 _gate_pass 函数（Wilson CI 闸门）
        return _gate_pass(candidate, baseline)

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
            self._record_update(
                action='manual_rollback', version_tag=version_tag,
                trigger_reason='GUI 手动回滚',
                execution_result='failed',
                extra={'error': f'版本 {version_tag} 不存在'})
            return {'ok': False, 'error': f'版本 {version_tag} 不存在'}
        if current_active and current_active.get('version_tag') == version_tag:
            self._record_update(
                action='manual_rollback', version_tag=version_tag,
                trigger_reason='GUI 手动回滚',
                execution_result='noop',
                extra={'error': '当前已是该版本，无需回滚'})
            return {'ok': False, 'error': '当前已是该版本，无需回滚'}
        # 执行回滚
        if current_active:
            current_active['status'] = 'rolledback'
            self._persist_version(current_active)
        target['status'] = 'active'
        target['parent_tag'] = current_active.get('version_tag', '') if current_active else ''
        self._persist_version(target)
        self._last_usable_version_tag = version_tag
        # §4.2 可追溯：手动回滚也写入更新台账（含触发原因 + 执行结果）
        self._record_update(
            action='manual_rollback', version_tag=version_tag,
            trigger_reason='GUI 手动回滚（用户显式操作）',
            execution_result='success',
            extra={'from_tag': current_active.get('version_tag', '') if current_active else ''})
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

    # ------------------------------------------------------------------
    # §4.2 更新流程：可追溯 / 可回滚 / 安全校验
    # ------------------------------------------------------------------

    def _pre_update_validation(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """更新前安全校验（§4.2.2(4)：版本兼容性 + 数据完整性 + 算法有效性）。

        纯本地校验，不依赖 DB，可在 DB 不可达时独立完成「更新前后校验」。

        Args:
            record: 待持久化/应用的版本记录（至少含 version_tag / params_json /
                metrics_json / status / parent_tag）。

        Returns:
            {'ok': bool, 'checks': {name: 'pass'/'fail'/'warn'},
             'fatal': bool, 'details': [str, ...]}
            ok=False 且 fatal=True 时调用方应终止执行并记录（不静默吞异常）。
        """
        checks: Dict[str, str] = {}
        details: List[str] = []
        fatal = False

        # (1) 版本兼容性：version_tag 非空且在本版本列表中唯一
        tag = record.get('version_tag')
        self._load_versions()
        existing_tags = {v.get('version_tag') for v in self._versions}
        if not tag:
            checks['version_tag'] = 'fail'
            details.append('version_tag 缺失（无法兼容/回滚定位）')
            fatal = True
        elif tag in existing_tags and record.get('_overwrite') is not True:
            checks['version_tag_unique'] = 'warn'
            details.append(f'版本 {tag} 已存在（幂等 upsert 将覆盖旧记录）')
        else:
            checks['version_tag'] = 'pass'

        # (2) 数据完整性：params_json / metrics_json 必须存在且为映射
        params = record.get('params_json') or {}
        metrics = record.get('metrics_json') or record.get('metrics') or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:  # noqa: BLE001
                params = {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:  # noqa: BLE001
                metrics = {}
        if isinstance(params, dict) and params:
            checks['params_completeness'] = 'pass'
        else:
            checks['params_completeness'] = 'fail'
            details.append('params_json 缺失或为空（权重/lookback 不完整）')
            fatal = True
        if isinstance(metrics, dict) and metrics:
            checks['metrics_completeness'] = 'pass'
        else:
            checks['metrics_completeness'] = 'warn'
            details.append('metrics_json 缺失（无法评估算法有效性，允许 trial 归档）')

        # (3) 算法有效性：权重须为数值映射（冻结权重契约，非 dict-of-str）
        weights = params.get('weights') if isinstance(params, dict) else None
        if isinstance(weights, dict) and weights:
            bad = [k for k, v in weights.items() if not isinstance(v, (int, float))]
            if bad:
                checks['algorithm_validity'] = 'warn'
                details.append(f'权重含非数值项 {bad}（可能污染融合）')
            else:
                checks['algorithm_validity'] = 'pass'
        else:
            checks['algorithm_validity'] = 'warn'
            details.append('未见 weights 数值映射（纯 trial 归档可接受）')

        # (4) 溯源一致性：parent_tag 若给定，应能在版本列表中定位
        parent = record.get('parent_tag')
        if parent:
            checks['lineage'] = ('pass' if parent in existing_tags
                                  else 'warn')
            if checks['lineage'] == 'warn':
                details.append(f'parent_tag {parent} 未在版本列表中找到（历史缺失）')
        else:
            checks['lineage'] = 'pass'

        ok = not fatal
        return {'ok': ok, 'checks': checks, 'fatal': fatal, 'details': details}

    def _record_update(self, action: str, version_tag: str,
                       trigger_reason: str, execution_result: str,
                       validation: Optional[Dict[str, Any]] = None,
                       auto_rolled_back: bool = False,
                       extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """§4.2 可追溯台账：追加一条更新记录（本地 JSONL，DB 不可达亦可用）。

        记录含：更新内容(action/version_tag)、时间、触发原因(trigger_reason)、
        执行结果(execution_result)、校验(validation)、是否自动回滚(auto_rolled_back)。

        Args:
            action: 'evolution_persist' / 'manual_rollback' / 'auto_rollback'。
            version_tag: 关联版本标签。
            trigger_reason: 触发原因（方案 §4.2.2(1)/(2) 明列必含字段）。
            execution_result: 'success' / 'failed' / 'rolledback' 等。
            validation: _pre_update_validation 的返回值（可空）。
            auto_rolled_back: 是否触发了自动回滚。
            extra: 其它可追溯信息（如 beat/baseline/parent）。

        Returns:
            已记录条目（含 timestamp）。
        """
        entry: Dict[str, Any] = {
            'timestamp': _now_str(),
            'action': action,
            'version_tag': version_tag,
            'trigger_reason': trigger_reason,
            'execution_result': execution_result,
            'validation': validation or {},
            'auto_rolled_back': auto_rolled_back,
        }
        if extra:
            entry.update(extra)
        # 内存 + 本地 JSONL 双写（JSONL 跨进程可查询/导出/回溯）
        self._update_history.append(entry)
        try:
            with open(self._update_ledger_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False,
                                   default=_json_default) + '\n')
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 更新台账写入失败(非致命): %s', e)
        return entry

    def _load_update_history(self) -> List[Dict[str, Any]]:
        """从本地 JSONL 补读更新台账（仅当内存为空时——跨进程重启补读）。

        同进程内 ``_record_update`` 已把条目写入内存 + JSONL，故此处只在
        内存为空（进程重启、GUI 读取引擎历史）时补读一次，避免重复追加。
        """
        if self._update_history:
            return list(self._update_history)
        if not os.path.isfile(self._update_ledger_path):
            return []
        try:
            with open(self._update_ledger_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._update_history.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 更新台账读取失败: %s', e)
        return list(self._update_history)

    def get_update_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """对外接口：查询更新记录（倒序，最新在前）。支持历史追溯（§4.2.2(2)）。

        内存非空（同进程）直接用；内存为空（跨进程重启）先从本地 JSONL 补读一次。
        """
        self._load_update_history()
        return list(reversed(self._update_history))[:max(0, int(limit))]

    def export_update_history(self, path: str) -> int:
        """导出更新台账到独立 JSON 文件（§4.2.2(2)：可导出、支持问题回溯）。"""
        self._load_update_history()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(list(reversed(self._update_history)), f,
                          ensure_ascii=False, indent=2, default=_json_default)
            return len(self._update_history)
        except Exception as e:  # noqa: BLE001
            logger.warning('[self_evolution] 导出更新台账失败: %s', e)
            return 0

    def auto_rollback_on_update_failure(self, failed_version_tag: str,
                                        reason: str) -> Dict[str, Any]:
        """§4.2.2(3) 更新异常自动回滚：把失败版本标记 failed，并回退到上一可用版本。

        设计约束（诚实边界 + 平滑回滚）：
          * 仅切换版本表 status 字段（失败版→'failed'，上一 active→'active'），
            **不改**冻结权重、不写预测器配置（注入本就是诚实 no-op）；
          * 「上一可用版本」取失败版本之前最近一个 active/trial（若存在），
            找不到则保持现状并如实记录「无可用回退目标」；
          * 全过程写可追溯台账（auto_rolled_back=True），不抛异常阻断主流程。

        Args:
            failed_version_tag: 更新失败/被拒的版本标签。
            reason: 失败原因（写入台账 trigger_reason）。

        Returns:
            {'ok': bool, 'restored_tag': str|None, 'message': str}
        """
        self._load_versions()
        # 定位上一可用版本（按 created_at 倒序中，除失败版外最近的一个 active/trial）
        candidates = [v for v in self._versions
                      if v.get('version_tag') != failed_version_tag
                      and v.get('status') in ('active', 'trial')]
        candidates.sort(key=lambda v: str(v.get('created_at', '')), reverse=True)
        restored = candidates[0] if candidates else None

        # 标记失败版本
        failed = next((v for v in self._versions
                       if v.get('version_tag') == failed_version_tag), None)
        if failed is not None:
            failed['status'] = 'failed'
            failed['note'] = f'[auto-rollback] 更新失败({reason})，已自动回退'
            self._persist_version(failed)

        # 恢复上一可用版本为 active（若存在）
        restored_tag = None
        if restored is not None:
            restored['status'] = 'active'
            self._persist_version(restored)
            restored_tag = restored.get('version_tag')
            self._last_usable_version_tag = restored_tag
            self._emit('warning',
                       f'自动回滚: {failed_version_tag} → {restored_tag}')

        entry = self._record_update(
            action='auto_rollback',
            version_tag=failed_version_tag,
            trigger_reason=reason,
            execution_result='rolledback' if restored_tag else 'no_restore_target',
            auto_rolled_back=True,
            extra={'restored_tag': restored_tag},
        )
        return {
            'ok': bool(restored_tag),
            'restored_tag': restored_tag,
            'entry': entry,
            'message': (f'已自动回滚到 {restored_tag}'
                        if restored_tag
                        else '无可用回退目标（无更早 active/trial 版本）'),
        }

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