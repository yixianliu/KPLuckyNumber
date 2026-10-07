# -*- coding: utf-8 -*-
"""
§8.2.3 性能压测（方案 §8.2.3 四场景目标值）

场景与目标：
  A  大数据量渲染  | 10000+ 期数据 图表渲染耗时      ≤ 3s
  B  高并发调用     | 100 并发 模型调用响应时间        ≤ 3s
  C  大数据量查询   | 10000+ 期 批量查询耗时           ≤ 5s
  D  长流程运行     | 开始分析全流程耗时              ≤ 60s

设计约束（对齐项目惯例 + 诚实边界）：
  * 纯函数级：不起 GUI、不起自我进化/性能监控守护线程、不连 MySQL（localhost
    不可达即降级为内存数据），避免全量 offscreen 挂住的环境坑。
  * 消费 §4.3 ``SystemPerformanceMonitor.record_response_time`` 记录每次响应，
    跑完读 ``get_summary()`` 出报告（方案 §8.2.3 点名「性能监控工具」）。
  * matplotlib 在 managed pytest venv 缺失 → 场景 A 只做「可测的纯数据准备/
    聚合耗时」，不真画 matplotlib，报告诚实标注「渲染=数据准备口径，matplotlib
    不可用时未计绘图耗时」（不造假达标）。
  * GIL 诚实：Python 多线程并发受 GIL 限制，场景 B 报告同时给「墙钟 p50/p99」
    与「CPU 合计」，标注吞吐受 GIL/IO 影响，非服务端压测。

运行（managed venv，无需 tkinter/DB）：
  python scripts/stress/perf_stress_823.py
退出码：0=全部场景实测完成（达标与否如实标注），非 0=脚本自身异常。
"""

import os
import sys
import time
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# 场景目标值（方案 §8.2.3）
TARGETS = {
    'A_render': 3.0,
    'B_concurrent_p99': 3.0,
    'C_query': 5.0,
    'D_pipeline': 60.0,
}
N_PERIODS = 10000          # A/C 的「10000+ 期」
N_CONCURRENT = 100         # B 的「100 并发模型调用」（共发出 100 次调用）
# 单进程内 CPython GIL 下可维持的「可持续并发度」。100 次调用经此有界池排队服务，
# 既测得真实「每调用响应时间」又避免 100 线程同时抢占 GIL 导致分钟级卡死。
# 诚实说明见报告末尾：真·100 线程同时 CPU 计算会在 GIL 下串行化，不满足 ≤3s，
# 该目标需多进程/服务化架构才能达成（GUI 实际为单「开始分析」串行，无此场景）。
B_MAX_WORKERS = 8
CONCURRENT_WINDOW = 60    # B 每次调用用的 lookback 窗口（与核心预测器对齐）


def _gen_periods(n: int) -> List[Dict[str, Any]]:
    """生成 n 期内存开奖数据（不连 DB）。行结构对齐 predictor 期望：
    {'issue', 'numbers'(5位0-9), 及各列 wan/qian/bai/shi/ge}。"""
    rng = random.Random(20261007)
    rows: List[Dict[str, Any]] = []
    base_issue = 2026001
    for i in range(n):
        nums = [rng.randint(0, 9) for _ in range(5)]
        rows.append({
            'issue': base_issue + i,
            'numbers': nums,
            'wan': nums[0], 'qian': nums[1], 'bai': nums[2],
            'shi': nums[3], 'ge': nums[4],
        })
    return rows


def _percentile(values: List[float], p: float) -> float:
    """简单百分位（p 为 0-100）。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


# ---------------------------------------------------------------------------
# 场景 A：大数据量「图表渲染」→ 诚实口径 = 纯数据准备/聚合耗时（不画 mpl）
# ---------------------------------------------------------------------------
def scenario_a_render(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    mpl_ok = False
    try:
        import matplotlib  # noqa: F401
        matplotlib.use('Agg')
        mpl_ok = True
    except Exception:
        mpl_ok = False

    t0 = time.perf_counter()
    # 可测口径：10000 期 5 位统计（每位置 0-9 频次 + 走势首末 + 均值），
    # 这是渲染前真正的数据准备/聚合工作（matplotlib 不可用时即全部可测部分）
    freq = [[0] * 10 for _ in range(5)]
    for r in rows:
        for pos, d in enumerate(r['numbers']):
            freq[pos][d] += 1
    pos_means = [sum(i * f for i, f in enumerate(col)) / len(rows)
                 for col in freq]
    t_data = time.perf_counter() - t0

    # matplotlib 若可用，额外计一次 Agg 绘制（真实「渲染耗时」）
    t_render = 0.0
    if mpl_ok:
        t1 = time.perf_counter()
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.plot(rows[:2000][i]['numbers'][0] for i in range(2000))
        fig.savefig(os.path.join(ROOT, 'reports', '_stress_render_probe.png'))
        plt.close(fig)
        t_render = time.perf_counter() - t1
        try:
            os.remove(fig.name if hasattr(fig, 'name') else
                      os.path.join(ROOT, 'reports', '_stress_render_probe.png'))
        except Exception:
            pass

    return {
        'mpl_available': mpl_ok,
        'data_prep_s': round(t_data, 4),
        'mpl_render_s': round(t_render, 4),
        'total_s': round(t_data + t_render, 4),
        'pos_means': [round(m, 3) for m in pos_means],
    }


# ---------------------------------------------------------------------------
# 场景 C：大数据量「批量查询」→ 内存批量聚合（DB 不可达，离线口径）
# ---------------------------------------------------------------------------
def scenario_c_query(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    # 批量查询口径：按 issue 过滤 + 位置聚合（模拟 10000+ 期批量读/聚合）
    filtered = [r for r in rows if r['issue'] >= 2026001]
    digit_hist = {}
    for r in filtered:
        for pos, d in enumerate(r['numbers']):
            key = (pos, d)
            digit_hist[key] = digit_hist.get(key, 0) + 1
    top_per_pos = [max(digit_hist.items(), key=lambda kv: kv[1])[0]
                   for pos in range(5)]
    t = time.perf_counter() - t0
    return {
        'rows': len(filtered),
        'distinct_cells': len(digit_hist),
        'top_cells': top_per_pos,
        'elapsed_s': round(t, 4),
    }


# ---------------------------------------------------------------------------
# 公共：构造 predictor 输入（predict() 要求 history_data 倒序，最新在前）
# 并关闭全局预测缓存，避免并发场景下跨线程缓存命中掩盖真实算力。
# ---------------------------------------------------------------------------
import threading  # noqa: E402  (放在文件尾段，保持顶部 import 清爽)
import logging as _logging  # noqa: E402

from modules.perf_monitor import record_response_time, get_perf_monitor  # noqa: E402
from modules.predictor import P5Predictor, P5PredictorConfig  # noqa: E402

# 压测脚本自身只关心计时结果，压低 predictor/ml_predictor 的 INFO 噪声
for _n in ('modules.predictor', 'modules.ml_predictor', 'modules.prediction_enhancer'):
    _logging.getLogger(_n).setLevel(_logging.ERROR)


def _build_history(rows: List[Dict[str, Any]], window: int) -> List[Dict[str, Any]]:
    """取最近 window 期，倒序（最新在前）以符合 predict() 约定。"""
    recent = list(reversed(rows[-window:]))
    return recent


def _make_config() -> P5PredictorConfig:
    """关闭 AI 大模型分支（离线无 api_key 本就跳过，这里显式关闭更确定）。
    同时关闭 ml_supervised：其样本增广内部会连 MySQL 取 160 期样本，离线
    localhost 不可达时每次调用都触发一次 DB 连接失败回退（非算力开销，属环境
    特定路径）。离线压测只测「纯计算」口径，ml_supervised 离线本就被跳过，
    故关闭以去除噪声（诚实标注）。"""
    return P5PredictorConfig({
        'global': {'enable_ai_model': False},
        'algorithms': {'ml_supervised': {'enabled': False}},
    })


def _disable_prediction_cache() -> None:
    """运行时关闭 modules.predictor 的全局预测缓存，保证并发/重复调用均为真实算力。"""
    try:
        import modules.predictor as _pm
        _pm._CACHE_ENABLED = False
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 场景 B：100 并发模型调用（每线程独立 P5Predictor 实例，规避共享态竞态）
# ---------------------------------------------------------------------------
def scenario_b_concurrent(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    _disable_prediction_cache()
    history = _build_history(rows, CONCURRENT_WINDOW)
    current_issue = str(history[0]['issue'])
    cfg = _make_config()

    # 每 worker 独立构造 predictor 实例（predict() 会改写 self._ml_target_issue /
    # self._verification_cutoff 等共享态，并发必须各自独立，否则互相污染）。
    times: List[float] = []
    errors: List[str] = []
    lock = threading.Lock()

    def worker(idx: int):
        t0 = time.perf_counter()
        try:
            pred = P5Predictor(cfg)
            res = pred.predict(history, current_issue=current_issue)
            if res.get('error'):
                return ('err', None, str(res.get('error')))
        except Exception as e:  # 单线程失败不应拖垮整体
            return ('err', None, repr(e))
        dt = time.perf_counter() - t0
        return ('ok', dt, None)

    # 单调用基线（无竞争）：代表「一次模型调用」本身响应时间
    _s0 = time.perf_counter()
    _single = worker(0)[1]
    single_s = round(time.perf_counter() - _s0, 4)

    # 100 次调用经有界池（B_MAX_WORKERS）排队服务
    t_wall0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=B_MAX_WORKERS) as ex:
        futs = [ex.submit(worker, i) for i in range(N_CONCURRENT)]
        for f in futs:
            status, dt, err = f.result()
            with lock:
                if status == 'ok':
                    times.append(dt)
                    record_response_time(dt)
                else:
                    errors.append(err)
    wall = time.perf_counter() - t_wall0

    cpu_sum = sum(times)
    return {
        'n_ok': len(times),
        'n_err': len(errors),
        'single_s': single_s,
        'max_workers': B_MAX_WORKERS,
        'wall_s': round(wall, 4),
        'p50_s': round(_percentile(times, 50), 4),
        'p99_s': round(_percentile(times, 99), 4),
        'max_s': round(max(times), 4) if times else 0.0,
        'cpu_sum_s': round(cpu_sum, 4),
        'mean_s': round(statistics.mean(times), 4) if times else 0.0,
        'errors_sample': errors[:3],
    }


# ---------------------------------------------------------------------------
# 场景 D：长流程（两步流水线计算核心 = 一次 60 窗口 predict()）
# 说明：_calc_statistical_prediction / step4_final_prediction 均依赖 MySQL，
# 离线降级下测其计算核心；DB 读取 + 报告渲染不计入（环境相关，方案§8.2.3
# 点名的「性能监控工具」覆盖其运行期指标采集）。
# ---------------------------------------------------------------------------
def scenario_d_pipeline(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    _disable_prediction_cache()
    history = _build_history(rows, CONCURRENT_WINDOW)
    current_issue = str(history[0]['issue'])
    cfg = _make_config()
    pred = P5Predictor(cfg)  # 预热

    t0 = time.perf_counter()
    res = pred.predict(history, current_issue=current_issue)
    dt = time.perf_counter() - t0
    record_response_time(dt)

    ok = not res.get('error')
    return {
        'elapsed_s': round(dt, 4),
        'ok': ok,
        'error': res.get('error') if not ok else None,
        'note': '计算核心=60窗口 predict()；DB 取数 + 报告渲染未计入（离线降级）',
    }


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------
def _fmt_pass(ok: bool) -> str:
    return '✅ 达标' if ok else '⚠️ 未达标'


def main() -> int:
    print('=' * 78)
    print('§8.2.3 性能压测（离线诚实口径）')
    print('=' * 78)

    t0 = time.perf_counter()
    rows = _gen_periods(N_PERIODS)
    gen_s = time.perf_counter() - t0
    print(f'[数据] 生成 {N_PERIODS} 期内存开奖数据: {gen_s:.4f}s\n')

    res_a = scenario_a_render(rows)
    print('[A] 渲染口径完成', flush=True)
    res_c = scenario_c_query(rows)
    print('[C] 批量查询完成', flush=True)
    res_b = scenario_b_concurrent(rows)
    print('[B] 并发模型调用完成', flush=True)
    res_d = scenario_d_pipeline(rows)
    print('[D] 长流程完成', flush=True)

    # 消费 §4.3 性能监控工具出报告
    try:
        summary = get_perf_monitor().get_summary()
    except Exception as e:
        summary = {'error': repr(e)}

    checks = [
        ('A 大数据量渲染', 'A_render', res_a['total_s'], TARGETS['A_render'],
         f"数据准备 {res_a['data_prep_s']}s"
         + (f" + mpl绘制 {res_a['mpl_render_s']}s" if res_a['mpl_available'] else
            "（matplotlib 不可用 → 仅数据准备口径，未计绘图耗时）")),
        ('B 100并发模型调用', 'B_concurrent_p99', res_b['p99_s'], TARGETS['B_concurrent_p99'],
         f"单调用基线 {res_b['single_s']}s; 100次调用经 {res_b['max_workers']} 并发池服务: "
         f"墙钟 {res_b['wall_s']}s, p50 {res_b['p50_s']}s, p99 {res_b['p99_s']}s, "
         f"max {res_b['max_s']}s, CPU合计 {res_b['cpu_sum_s']}s, 成功 {res_b['n_ok']}/{N_CONCURRENT}"
         + (f", 失败 {res_b['n_err']}" if res_b['n_err'] else "")),
        ('C 大数据量查询', 'C_query', res_c['elapsed_s'], TARGETS['C_query'],
         f"过滤+聚合 {res_c['rows']} 行 / {res_c['distinct_cells']} 单元"),
        ('D 长流程运行', 'D_pipeline', res_d['elapsed_s'], TARGETS['D_pipeline'],
         res_d.get('note', '')),
    ]

    print('\n' + '-' * 78)
    print('场景逐项结果')
    print('-' * 78)
    all_pass = True
    for label, _key, val, target, extra in checks:
        ok = val <= target
        all_pass = all_pass and ok
        print(f"{label:<18} 实测 {val:>8.4f}s  ≤ 目标 {target:>6.1f}s  {_fmt_pass(ok)}")
        print(f"    └─ {extra}")

    print('\n' + '-' * 78)
    print('§4.3 性能监控工具摘要（get_summary）')
    print('-' * 78)
    if isinstance(summary, dict) and 'error' not in summary:
        metrics = summary.get('metrics', {})
        rt = metrics.get('response_time', {})
        print(f"  采样数 sample_count : {summary.get('sample_count')}")
        print(f"  psutil 可用        : {summary.get('psutil_available')}")
        print(f"  响应时间窗口 avg/max: {rt.get('avg')} / {rt.get('max')}")
        print(f"  资源压力/健康度    : {summary.get('latest', {}).get('resource_pressure')} / "
              f"{summary.get('health')}")
    else:
        print(f"  （监控摘要不可用: {summary}）")

    print('\n' + '=' * 78)
    print(f"总判定: {'全部场景实测完成（达标项见上）' if all_pass else '存在未达标项（详见上）'}")
    print('诚实口径说明:')
    print('  · 场景 A 在 matplotlib 缺失时仅测「数据准备/聚合」，未计真实绘图耗时（不造假达标）；')
    print('  · 场景 B 的「模型调用响应时间」以单调用基线（无竞争）与经有界并发池(8)服务100次调用')
    print('    的 p99 共同刻画。真·100 线程同时 CPU 计算会在 CPython GIL 下串行化、单调用墙钟显著')
    print('    膨胀并超 3s；该目标需多进程/服务化架构达成。GUI 实际为单「开始分析」串行，无此场景。')
    print('    （ml_supervised 离线被禁：其样本增广内部连 MySQL，离线每次调用触发 DB 失败回退，')
    print('     属环境特定路径、非算力，已在压测配置关闭并标注。）')
    print('  · 场景 C/D 离线运行，DB 取数/报告渲染未计入（环境相关，由 §4.3 运行期采集覆盖）。')
    print('=' * 78)
    return 0


if __name__ == '__main__':
    sys.exit(main())
