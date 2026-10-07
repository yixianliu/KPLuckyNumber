# -*- coding: utf-8 -*-
"""
排列5 系统性能监控与资源管理（§4.3）

方案 §4.3.2 点名要建 ``SystemPerformanceMonitor``：
  * 性能监控体系：覆盖 CPU / 内存 / 响应时间 / 任务耗时，指标实时采集、动态更新、异常告警；
  * 资源管理优化：资源使用达阈值时自动优化（缓存清理 / 任务降级），资源配额管理；
  * 资源监控可视化：指标趋势留存，支持趋势分析。

设计约束（对齐项目既有惯例）：
  * **惰性导入 psutil + 优雅降级**：psutil 在 Anaconda 有、managed pytest venv 无，
    故 CPU/内存指标不可用时降级为 None（而非 0.0，避免污染滑窗均值误触发告警），
    任务/耗时指标（纯标准库）始终可用。
  * **≥10 类核心指标**（方案要求 ≥6）：CPU、进程内存、系统内存、响应时间、
    任务队列深度、运行中任务数、最长任务耗时、平均任务耗时、资源压力分、采样点计数。
  * **1Hz 守护线程 + deque 滑窗**：实时采集、趋势留存；``start_monitoring`` 幂等。
  * **沿边触发资源优化**（不抖）：资源压力超阈值才触发优化（默认清缓存），
    带冷却期，避免每 5s 重复清理；优化回调可注入（线程安全，异常隔离）。
  * **稳定性代理**：单任务异常（响应/耗时越限）时，资源压力分反映影响幅度，
    不直接阻断任务（方案「单任务异常整体性能影响 ≤10%」由守护线程隔离+降级兜底）。

无 DB 依赖，纯内存 + 本地 JSONL 趋势留存（可选落盘）。
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置（可被 config.PERF_MONITOR_CONFIG 覆盖）
# ---------------------------------------------------------------------------
PERF_MONITOR_CONFIG = {
    # 采集间隔（秒）——方案要求「指标更新频率 ≥ 1 次/秒」
    'sample_interval': 1.0,

    # 滑窗保留点数（趋势留存；deque 自动丢最旧）
    'window_size': 120,

    # 资源优化触发阈值（0-1，资源压力分）与冷却期（秒，沿边触发防抖）
    'resource_pressure_warn': 0.7,
    'resource_pressure_optimize': 0.85,
    'optimize_cooldown': 30.0,

    # 响应/耗时越限阈值（秒）——用于「单任务异常影响」评估
    'response_time_warn': 3.0,
    'response_time_danger': 5.0,
    'task_elapsed_warn': 30.0,
    'task_elapsed_danger': 120.0,

    # 内存阈值（字节；系统内存使用率 0-1 亦可，见 _collect_metrics）
    'system_mem_ratio_warn': 0.85,
    'system_mem_ratio_danger': 0.95,

    # 趋势落盘（可选；默认关，避免长跑刷盘）
    'trend_jsonl': os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'perf_trend.jsonl'),
}


def _now_iso() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


class SystemPerformanceMonitor:
    """系统性能监控（§4.3）：CPU/内存/响应/耗时 实时采集 + 阈值自动优化 + 趋势留存。

    与 TaskManager 解耦：通过 :meth:`set_task_source` 注入任务来源（鸭子类型，
    需暴露 ``get_task_count`` / ``is_running`` / ``get_running_tasks``），
    监控线程读它统计「任务队列深度 / 运行中任务数 / 任务耗时」。
    默认无任务来源时这些指标取 0（不污染，非 None——任务类指标恒可计算）。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 auto_optimize: bool = True):
        self.cfg = dict(PERF_MONITOR_CONFIG)
        if config:
            self.cfg.update(config)
        self.auto_optimize = auto_optimize

        # CPU/内存指标惰性导入 psutil（managed pytest venv 无 psutil → 降级 None）
        self._psutil = None
        self._psutil_failed = False
        self._proc = None

        # 任务来源（TaskManager，鸭子类型）
        self._task_source: Optional[Any] = None
        self._task_source_lock = threading.Lock()

        # 滑窗指标（deque 趋势留存，自动丢最旧）
        wsize = int(self.cfg['window_size'])
        self._windows: Dict[str, Deque[float]] = {
            'cpu_percent': deque(maxlen=wsize),
            'mem_rss_mb': deque(maxlen=wsize),
            'sys_mem_ratio': deque(maxlen=wsize),
            'response_time': deque(maxlen=wsize),
            'task_queue_depth': deque(maxlen=wsize),
            'task_running': deque(maxlen=wsize),
            'task_max_elapsed': deque(maxlen=wsize),
            'task_avg_elapsed': deque(maxlen=wsize),
            'resource_pressure': deque(maxlen=wsize),
        }
        self._timestamps: Deque[str] = deque(maxlen=wsize)
        self._metrics_lock = threading.RLock()  # 可重入：get_summary 持锁内调用 get_latest 需同锁重入

        # 资源优化沿边触发状态（冷却期防抖）
        self._last_optimize_ts = 0.0
        self._optimize_active = False
        self._alert_states = {
            'cpu': False, 'memory': False,
            'response_time': False, 'task_elapsed': False,
            'resource_pressure': False,
        }

        # 资源优化回调（可注入；默认 None → 走内置缓存清理）
        self._resource_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._callback_lock = threading.Lock()

        # 监控线程控制
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._sample_count = 0
        self._psutil_ok: Optional[bool] = None

    # ------------------------------------------------------------------
    # psutil 惰性导入（降级：无 psutil 时 CPU/内存取 None，不写 0.0）
    # ------------------------------------------------------------------
    def _ensure_psutil(self):
        if self._psutil is not None or self._psutil_failed:
            return
        try:
            import psutil
            self._psutil = psutil
            self._proc = psutil.Process(os.getpid())
            self._psutil_ok = True
            logger.info('[PerfMonitor] psutil 已就绪（CPU/内存指标启用）')
        except Exception as e:
            self._psutil_failed = True
            self._psutil_ok = False
            logger.info('[PerfMonitor] psutil 不可用，CPU/内存指标降级为 None: %s', e)

    # ------------------------------------------------------------------
    # 任务来源注入（与 TaskManager 解耦）
    # ------------------------------------------------------------------
    def set_task_source(self, source: Optional[Any]):
        """注入任务来源（TaskManager 或鸭子类型）。线程安全。"""
        with self._task_source_lock:
            self._task_source = source

    def set_resource_callback(self, cb: Optional[Callable[[Dict[str, Any]], None]]):
        """注入资源优化回调（替代内置缓存清理）。"""
        with self._callback_lock:
            self._resource_callback = cb

    # ------------------------------------------------------------------
    # 指标采集
    # ------------------------------------------------------------------
    def _collect_metrics(self) -> Dict[str, Any]:
        """采集一次性能指标。CPU/内存不可用时取 None（不写 0.0 污染滑窗）。

        任务类指标（队列深度/运行中数/耗时）纯标准库、恒可计算，取 0 语义正确。
        """
        m: Dict[str, Any] = {}
        self._ensure_psutil()

        # CPU 使用率（%）
        if self._psutil and self._proc is not None:
            try:
                m['cpu_percent'] = round(self._proc.cpu_percent(interval=None), 2)
            except Exception:
                m['cpu_percent'] = None
        else:
            m['cpu_percent'] = None

        # 进程 RSS（MB）
        if self._psutil and self._proc is not None:
            try:
                m['mem_rss_mb'] = round(self._proc.memory_info().rss / (1024 * 1024), 2)
            except Exception:
                m['mem_rss_mb'] = None
        else:
            m['mem_rss_mb'] = None

        # 系统内存使用率（0-1）
        if self._psutil:
            try:
                m['sys_mem_ratio'] = round(self._psutil.virtual_memory().percent / 100.0, 4)
            except Exception:
                m['sys_mem_ratio'] = None
        else:
            m['sys_mem_ratio'] = None

        # 任务类指标（纯标准库）
        with self._task_source_lock:
            src = self._task_source
        queue_depth = 0
        running = 0
        max_elapsed = 0.0
        avg_elapsed = 0.0
        if src is not None:
            try:
                queue_depth = int(src.get_task_count())
            except Exception:
                queue_depth = 0
            try:
                running = 1 if src.is_running() else 0
                for t in src.get_running_tasks():
                    running += 1
            except Exception:
                running = 0
            try:
                tasks = src.get_running_tasks()
                if tasks:
                    elapsed = [float(t.get('running_time', 0.0) or 0.0) for t in tasks]
                    max_elapsed = max(elapsed)
                    avg_elapsed = sum(elapsed) / len(elapsed)
            except Exception:
                pass
        m['task_queue_depth'] = queue_depth
        m['task_running'] = running
        m['task_max_elapsed'] = round(max_elapsed, 2)
        m['task_avg_elapsed'] = round(avg_elapsed, 2)

        # 资源压力分（0-1）：综合 CPU/内存/任务耗时 的压力
        m['resource_pressure'] = self._compute_resource_pressure(m)
        return m

    def _compute_resource_pressure(self, m: Dict[str, Any]) -> float:
        """综合资源压力分（0-1）。CPU/内存缺失时按缺失项跳过、权重归一，
        不因 None 而崩溃；至少一个可计算维度时返回有效值。"""
        components: List[float] = []
        # CPU（0-1）
        if m.get('cpu_percent') is not None:
            components.append(min(1.0, m['cpu_percent'] / 100.0))
        # 系统内存（0-1）
        if m.get('sys_mem_ratio') is not None:
            components.append(float(m['sys_mem_ratio']))
        # 任务耗时压力（0-1，超过 danger 阈值 → 1.0）
        if m.get('task_max_elapsed') and self.cfg['task_elapsed_danger'] > 0:
            components.append(
                min(1.0, m['task_max_elapsed'] / self.cfg['task_elapsed_danger']))
        # 队列深度压力（0-1，超过 50 个待办 → 1.0）
        if m.get('task_queue_depth'):
            components.append(min(1.0, m['task_queue_depth'] / 50.0))

        if not components:
            # 所有维度都不可知（psutil 缺失 + 无任务源）→ 压力未知，返回 0 表示
            # 「未观测到压力」而非「无压力」，由调用方结合 sample_count 判定。
            return 0.0
        return round(sum(components) / len(components), 4)

    # ------------------------------------------------------------------
    # 告警 + 沿边资源优化（守护线程每采样周期调用）
    # ------------------------------------------------------------------
    def _check_alerts_and_optimize(self, m: Dict[str, Any]) -> None:
        """更新告警状态；资源压力越限时沿边触发资源优化（冷却防抖）。"""
        # ---- 告警（沿边记录，去抖）----
        # CPU
        cpu = m.get('cpu_percent')
        if cpu is not None:
            self._alert_states['cpu'] = cpu >= 90.0
        # 内存（系统使用率）
        mem = m.get('sys_mem_ratio')
        if mem is not None:
            self._alert_states['memory'] = mem >= self.cfg['system_mem_ratio_warn']
        # 任务耗时
        elapsed = m.get('task_max_elapsed') or 0.0
        self._alert_states['task_elapsed'] = elapsed >= self.cfg['task_elapsed_danger']
        # 资源压力
        pressure = m.get('resource_pressure') or 0.0
        self._alert_states['resource_pressure'] = pressure >= self.cfg[
            'resource_pressure_optimize']

        # ---- 资源优化：沿边触发（超 optimize 阈值 + 冷却期才触发一次）----
        if not self.auto_optimize:
            return
        now = time.monotonic()
        if pressure >= self.cfg['resource_pressure_optimize'] and not self._optimize_active:
            if now - self._last_optimize_ts >= self.cfg['optimize_cooldown']:
                self._last_optimize_ts = now
                self._optimize_active = True
                self._run_resource_optimization(m)
                # 优化后复位（留一个采样周期观测效果；下次循环若仍高再触发）
                try:
                    time.sleep(0.0)
                except Exception:  # noqa: BLE001
                    pass
                self._optimize_active = False

    def _run_resource_optimization(self, m: Dict[str, Any]) -> None:
        """资源越限优化：优先注入回调；默认清理全局预测缓存（资源配额兜底）。

        线程安全 + 异常隔离：优化失败仅记日志，绝不阻断监控主循环。
        """
        context = {
            'resource_pressure': m.get('resource_pressure'),
            'sys_mem_ratio': m.get('sys_mem_ratio'),
            'cpu_percent': m.get('cpu_percent'),
            'action': 'cache_clear',
            'ts': _now_iso(),
        }
        with self._callback_lock:
            cb = self._resource_callback
        if cb is not None:
            try:
                cb(context)
                logger.info('[PerfMonitor] 资源优化回调已执行: %s', context['ts'])
            except Exception as e:
                logger.warning('[PerfMonitor] 资源优化回调失败(非致命): %s', e)
            return
        # 默认：清理全局预测缓存（惰性导入，失败静默）
        try:
            from modules.smart_cache import clear_cache
            clear_cache()
            logger.warning(
                '[PerfMonitor] 资源压力 %.0f%% 越限，已清理全局预测缓存（默认优化）',
                (m.get('resource_pressure') or 0.0) * 100)
        except Exception as e:
            logger.debug('[PerfMonitor] 默认缓存清理跳过(非致命): %s', e)

    # ------------------------------------------------------------------
    # 监控线程
    # ------------------------------------------------------------------
    def start_monitoring(self) -> bool:
        """启动 1Hz 守护采样线程（幂等；已运行则直接 True）。"""
        if self._running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True,
                                        name='PerfMonitor')
        self._thread.start()
        self._running = True
        logger.info('[PerfMonitor] 性能监控守护线程已启动（interval=%.1fs）',
                    self.cfg['sample_interval'])
        return True

    def stop_monitoring(self) -> None:
        """停止监控线程（幂等）。"""
        if not self._running:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._running = False
        logger.info('[PerfMonitor] 性能监控已停止（共采样 %d 点）', self._sample_count)

    def _monitor_loop(self) -> None:
        interval = max(0.1, float(self.cfg['sample_interval']))
        while not self._stop.is_set():
            try:
                m = self._collect_metrics()
                self._push_sample(m)
                self._check_alerts_and_optimize(m)
            except Exception as e:
                logger.warning('[PerfMonitor] 采样循环出错(非致命): %s', e)
            self._stop.wait(interval)

    def _push_sample(self, m: Dict[str, Any]) -> None:
        """把一次采样推入滑窗（None 也推入——滑窗保留「未知」，不回填 0.0）。"""
        with self._metrics_lock:
            for key, dq in self._windows.items():
                dq.append(m.get(key))
            self._timestamps.append(_now_iso())
            self._sample_count += 1

    # ------------------------------------------------------------------
    # 对外读取 API
    # ------------------------------------------------------------------
    def get_latest(self) -> Dict[str, Any]:
        """返回最近一次采样的全部指标（含 psutil 可用性 + 样本计数）。"""
        with self._metrics_lock:
            out = {k: (list(dq)[-1] if dq else None) for k, dq in self._windows.items()}
        out['sample_count'] = self._sample_count
        out['psutil_available'] = bool(self._psutil)
        out['ts'] = (list(self._timestamps)[-1]
                     if list(self._timestamps) else None)
        return out

    def get_trend(self, metric: str, limit: int = 60) -> List[Optional[float]]:
        """取某指标的最近 ``limit`` 个采样点（最旧→最新），缺失为 None。

        趋势留存（§4.3.2(3)）：deque 滑窗即内存趋势；``persist_trend`` 可落盘。
        """
        if metric not in self._windows:
            return []
        with self._metrics_lock:
            data = list(self._windows[metric])
        return data[-max(0, int(limit)):]

    def get_summary(self) -> Dict[str, Any]:
        """汇总：各指标最近窗口统计 + 告警状态 + 资源优化状态 + 健康度。

        健康度 = 资源压力分映射（0-100）；psutil 不可用时该维标注降级。
        """
        with self._metrics_lock:
            def _stats(key):
                dq = list(self._windows[key])
                vals = [v for v in dq if v is not None]
                if not vals:
                    return {'count': len(dq), 'avg': None, 'max': None,
                            'current': (dq[-1] if dq else None), 'window': len(dq)}
                return {
                    'count': len(vals),
                    'avg': round(sum(vals) / len(vals), 4),
                    'max': round(max(vals), 4),
                    'current': dq[-1] if dq else None,
                    'window': len(dq),
                }

            latest = self.get_latest()
            active_alerts = [k for k, on in self._alert_states.items() if on]
            pressure = latest.get('resource_pressure') or 0.0
            health = round(100.0 * (1.0 - min(1.0, pressure)))
            return {
                'ts': latest.get('ts'),
                'sample_count': latest.get('sample_count', 0),
                'psutil_available': latest.get('psutil_available', False),
                'metrics': {k: _stats(k) for k in self._windows},
                'latest': latest,
                'active_alerts': active_alerts,
                'optimize_active': self._optimize_active,
                'last_optimize_ts': self._last_optimize_ts,
                'health_score': health,
            }

    def persist_trend(self, limit: int = 0) -> int:
        """把各指标滑窗趋势追加到本地 JSONL（默认关，需显式调用；趋势留存可查）。

        Args:
            limit: 每指标取最近多少点（0=全窗）。

        Returns:
            写入的采样点数；落盘失败返回 0（不影响监控）。
        """
        try:
            path = self.cfg['trend_jsonl']
            os.makedirs(os.path.dirname(path), exist_ok=True)
            limit = int(limit)
            rows = []
            with self._metrics_lock:
                for key, dq in self._windows.items():
                    window = list(dq)[-limit:] if limit else list(dq)
                    rows.append({
                        'ts': _now_iso(),
                        'metric': key,
                        'values': [v for v in window],
                        'sample_count': self._sample_count,
                        'psutil_available': bool(self._psutil),
                    })
            with open(path, 'a', encoding='utf-8') as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            return len(rows)
        except Exception as e:
            logger.warning('[PerfMonitor] 趋势落盘失败(非致命): %s', e)
            return 0


# ---------------------------------------------------------------------------
# 全局单例 + 便捷函数
# ---------------------------------------------------------------------------
_perf_monitor: Optional[SystemPerformanceMonitor] = None
_perf_monitor_lock = threading.Lock()


def get_perf_monitor(auto_optimize: bool = True) -> SystemPerformanceMonitor:
    """获取全局性能监控单例（惰性创建）。"""
    global _perf_monitor
    with _perf_monitor_lock:
        if _perf_monitor is None:
            _perf_monitor = SystemPerformanceMonitor(auto_optimize=auto_optimize)
        return _perf_monitor


def start_perf_monitoring() -> bool:
    """启动全局性能监控。"""
    return get_perf_monitor().start_monitoring()


def stop_perf_monitoring() -> None:
    """停止全局性能监控。"""
    get_perf_monitor().stop_monitoring()


def record_response_time(seconds: float) -> None:
    """记录一次响应时间（如 AI 分析 / 数据抓取耗时），纳入滑窗。

    线程安全；负值视为无效跳过（不污染窗口）。
    """
    m = get_perf_monitor()
    if seconds is None or seconds < 0:
        return
    with m._metrics_lock:
        m._windows['response_time'].append(float(seconds))




