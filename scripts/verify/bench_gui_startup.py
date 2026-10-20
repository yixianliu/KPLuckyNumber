# -*- coding: utf-8 -*-
"""GUI 冷启动基准探针（PUP-20261019-013100 §0.2 B1 / DSP-20261019-013100 S7）。

测量口径（与方案 §0.2 实测方式一致，import + build = total）：
  - import  : 解释器启动后、import main 之前的纯模块导入耗时（含 pandas/numpy/
              matplotlib 等重依赖首次加载；首页字体缓存未命中也会计入，属真实冷启动）；
  - build   : tk.Tk() + withdraw + LotteryGUI(root) 控件树完整构建耗时；
  - visible : deiconify + 布局 update 到窗口可见的耗时（不含用户感知的主循环）；
  - total   : import + build + visible。

实现说明：import 一次性生效，单进程内多次循环会被 sys.modules 缓存污染，
因此每轮迭代以独立子进程冷启动（_bench_startup_child.py），父进程聚合统计。
同理 HOME/USERPROFILE 重定向到脚本目录下固定临时目录，使字体缓存等在多次
迭代间复用（稳态口径），同时不触碰真实用户配置。

用法：
  python scripts/verify/bench_gui_startup.py                      # 默认 5 轮
  python scripts/verify/bench_gui_startup.py --iterations 7
  python scripts/verify/bench_gui_startup.py --timeout 180 --outdir reports/prep

产出：<outdir>/bench_startup.json（S7 七个基准 JSON 之一，G0.4 校验可解析）。
退出码：0 = 采集成功（基线偏差仅作参考评估，不影响退出码）；1 = 采集失败。

说明：本脚本仅测量 GUI 启动工程指标，与排列五开奖预测能力无关。
【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，
本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。
请理性购彩，量力而行。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_CHILD = os.path.join(_HERE, '_bench_startup_child.py')

# 方案 §0.2 基线快照：GUI 冷启动总耗时 3.40 s（import+build 口径）
_BASELINE_TOTAL_S = 3.40
_BASELINE_TOLERANCE = 0.10  # B4（main.py 拆分）将以 ≤10% 作为严格门禁，此处仅参考

_PHASES = ('import', 'build', 'visible', 'total')


def _p95(values):
    """计算 P95（最近秩线性插值口径，与闪烁守护探针一致），空列表返回 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = 0.95 * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def _median(values):
    """计算中位数（偶数样本取中间两值均值），空列表返回 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _phase_stats(samples_ms):
    """汇总单阶段统计量（单位统一为 ms，保留 1 位小数）。

    参数:
        samples_ms: 该阶段全部迭代样本（毫秒）。

    返回:
        dict —— n/mean/median/p95/min/max（毫秒）。
    """
    return {
        'n': len(samples_ms),
        'mean': round(sum(samples_ms) / len(samples_ms), 1) if samples_ms else 0.0,
        'median': round(_median(samples_ms), 1),
        'p95': round(_p95(samples_ms), 1),
        'min': round(min(samples_ms), 1) if samples_ms else 0.0,
        'max': round(max(samples_ms), 1) if samples_ms else 0.0,
    }


def _run_one_iteration(timeout_s):
    """以独立子进程执行一次冷启动测量，返回 (result_dict, error_str)。

    参数:
        timeout_s: 子进程超时秒数（超时判定为失败，防挂死）。

    返回:
        (result, error) —— result 为子进程打印的 [BENCH] JSON 行解析结果；
        error 非空表示本轮失败（超时/崩溃/无标记行）。
    """
    try:
        proc = subprocess.run(
            [sys.executable, _CHILD],
            capture_output=True, text=True, timeout=timeout_s, cwd=_ROOT)
    except subprocess.TimeoutExpired:
        return None, 'timeout after {}s'.format(timeout_s)
    marker_lines = [ln for ln in proc.stdout.splitlines()
                    if ln.startswith('[BENCH] ')]
    if not marker_lines:
        tail = (proc.stderr or '').strip().splitlines()[-3:]
        return None, 'no marker line (rc={}); stderr_tail={}'.format(
            proc.returncode, tail)
    try:
        result = json.loads(marker_lines[-1][len('[BENCH] '):])
    except ValueError as exc:
        return None, 'bad json: {}'.format(exc)
    if not result.get('ok'):
        return result, result.get('error') or 'child reported failure'
    return result, None


def _aggregate(iter_results):
    """按阶段聚合多轮样本，返回 phases 统计与每轮原始样本。

    参数:
        iter_results: 每轮成功的子进程结果列表。

    返回:
        (phases, samples) —— phases 为四阶段统计；samples 为原始样本列表。
    """
    samples = [{
        'import_ms': round(r['import_s'] * 1000, 1),
        'build_ms': round(r['build_s'] * 1000, 1),
        'visible_ms': round(r['visible_s'] * 1000, 1),
        'total_ms': round(r['total_s'] * 1000, 1),
        'geometry': r.get('geometry'),
        'nb_tab': r.get('nb_tab'),
    } for r in iter_results]
    phases = {
        name: _phase_stats([s['{}_ms'.format(name)] for s in samples])
        for name in _PHASES
    }
    return phases, samples


def _evaluate_baseline(median_total_ms):
    """对照方案 §0.2 基线 3.40s ±10% 做参考性评估（不影响退出码）。

    参数:
        median_total_ms: total 阶段中位数（毫秒）。

    返回:
        dict —— 基线路径、偏差百分比与 within/above/below_band 结论。
    """
    baseline_ms = _BASELINE_TOTAL_S * 1000.0
    dev_pct = (median_total_ms - baseline_ms) / baseline_ms * 100.0
    if abs(dev_pct) <= _BASELINE_TOLERANCE * 100:
        evaluation = 'within_band'
    elif dev_pct > 0:
        evaluation = 'above_band'
    else:
        evaluation = 'below_band'
    return {
        'source': 'PUP-20261019-013100 §0.2（v5.1 基线快照）',
        'baseline_total_ms': round(baseline_ms, 1),
        'tolerance_pct': _BASELINE_TOLERANCE * 100,
        'median_total_ms': round(median_total_ms, 1),
        'deviation_pct': round(dev_pct, 1),
        'evaluation': evaluation,
        'note': 'B1 阶段为参考性评估；B4（main.py 拆分）验收以 ≤10% 为硬门禁',
    }


def main():
    """主流程：解析参数 → 多轮子进程冷启动 → 聚合统计 → 落盘 JSON。"""
    parser = argparse.ArgumentParser(description='GUI 冷启动基准探针')
    parser.add_argument('--iterations', type=int, default=5,
                        help='冷启动迭代轮数（默认 5）')
    parser.add_argument('--timeout', type=float, default=180.0,
                        help='单轮子进程超时秒数（默认 180）')
    parser.add_argument('--outdir', default='reports/prep',
                        help='输出目录（默认 reports/prep，相对项目根）')
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error('--iterations 必须 >= 1')
    if not os.path.exists(_CHILD):
        print('[FATAL] 缺少子进程探针: {}'.format(_CHILD))
        return 1

    out_dir = args.outdir if os.path.isabs(args.outdir) \
        else os.path.join(_ROOT, args.outdir)
    os.makedirs(out_dir, exist_ok=True)

    print('[BENCH] 开始冷启动基准: {} 轮 x (import+build+visible), '
          '基线 {}s ±{}%'.format(
              args.iterations, _BASELINE_TOTAL_S,
              _BASELINE_TOLERANCE * 100))
    ok_results = []
    errors = []
    for i in range(1, args.iterations + 1):
        result, error = _run_one_iteration(args.timeout)
        if error:
            errors.append({'iteration': i, 'error': error})
            print('[BENCH] 第 {}/{} 轮失败: {}'.format(
                i, args.iterations, error))
            continue
        ok_results.append(result)
        print('[BENCH] 第 {}/{} 轮 import={:.1f}ms build={:.1f}ms '
              'visible={:.1f}ms total={:.1f}ms geom={}'.format(
                  i, args.iterations, result['import_s'] * 1000,
                  result['build_s'] * 1000, result['visible_s'] * 1000,
                  result['total_s'] * 1000, result.get('geometry')))

    if not ok_results:
        print('[FATAL] 全部 {} 轮采集失败，无数据落盘'.format(args.iterations))
        return 1

    phases, samples = _aggregate(ok_results)
    evaluation = _evaluate_baseline(phases['total']['median'])

    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'mode': 'collect',
        'environment': {
            'python': sys.version.split()[0],
            'platform': sys.platform,
            'iterations_requested': args.iterations,
            'iterations_ok': len(ok_results),
            'timeout_s': args.timeout,
        },
        'phases': phases,
        'samples': samples,
        'baseline_evaluation': evaluation,
        'errors': errors,
    }

    out_file = os.path.join(out_dir, 'bench_startup.json')
    with open(out_file, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    # 结论摘要（控制台）
    print('[BENCH] 统计(n={}): import median={:.1f}ms / build median={:.1f}ms '
          '/ visible median={:.1f}ms / total median={:.1f}ms p95={:.1f}ms'.format(
              phases['total']['n'], phases['import']['median'],
              phases['build']['median'], phases['visible']['median'],
              phases['total']['median'], phases['total']['p95']))
    print('[BENCH] 基线对照: {} (偏差 {:+.1f}%)'.format(
        evaluation['evaluation'], evaluation['deviation_pct']))
    print('[BENCH] 已落盘: {}'.format(out_file))
    if errors:
        print('[BENCH] 警告: {} 轮失败（详见 JSON errors 字段）'.format(
            len(errors)))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
