#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opt_monitor_daily.py — 排列5 v3.16 监控层每日常态化封装

把"数据刷新 → 滚动监控 → 状态机 → 退化回滚"串成一条可定时运行的流水线。

设计原则（诚实 + 稳健）:
- 数据刷新尽力而为: 失败/超时仅记警告, 不阻塞监控(现有DB数据仍可计算漂移窗口)
- 监控本身失败则整体致命失败(无状态可读, 无意义)
- 状态机 DEGRADED 时自动回滚 tuning_config.yaml 至冻结控制组(诚实基线)
- 退出码: 0 = HEALTHY/WATCH(正常/观察); 2 = DEGRADED(已自动回滚, 需关注); 1 = 致命错误

用法:
  python opt_monitor_daily.py [--skip-update] [--window 300] [--signals-window 100] [--skip-signals]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

# --- 路径锚定(B3修复): 向上搜索项目根(modules/+main.py), 注入 sys.path ---
def _find_project_root(_start):
    _cur = os.path.abspath(_start)
    while True:
        if os.path.isdir(os.path.join(_cur, 'modules')) and \
           os.path.isfile(os.path.join(_cur, 'main.py')):
            return _cur
        _p = os.path.dirname(_cur)
        if _p == _cur:
            return os.path.dirname(os.path.abspath(_start))
        _cur = _p
PROJECT_ROOT = _find_project_root(__file__)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

HERE = os.path.dirname(os.path.abspath(__file__))  # 本脚本所在目录
# 状态机/产物均在项目根目录(与 opt_monitor.py 的相对路径口径一致)
STATUS_PATH = os.path.join(PROJECT_ROOT, 'reports', 'monitor', 'latest_status.json')


def _run(cmd_args, timeout=900):
    """运行子命令, 返回 (returncode,)。stdout/stderr 实时透传。"""
    print(f'\n[step] $ {" ".join(cmd_args)}', flush=True)
    try:
        proc = subprocess.run(
            cmd_args, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        print(f'[timeout] 命令超过 {timeout}s 超时', flush=True)
        return 124
    if proc.stdout:
        print(proc.stdout, end='', flush=True)
    if proc.stderr:
        print('[stderr]\n' + proc.stderr, end='', flush=True)
    return proc.returncode


def refresh_data(skip):
    """尽力刷新历史开奖数据。失败不阻塞。

    注: main.py update 调用的 fetch_latest_data() 在当前代码不存在(坏命令),
    故直接调用 data_fetcher 真实增量入口 crawl_and_save_incremental() 拉最新开奖入库。
    """
    if skip:
        print('[refresh] 通过 --skip-update 跳过数据刷新', flush=True)
        return True
    refresh_cmd = (
        "from modules.data_fetcher import P5Spider; "
        "P5Spider().crawl_and_save_incremental(); "
        "print('REFRESH_OK')"
    )
    rc = _run([sys.executable, '-c', refresh_cmd], timeout=300)
    if rc == 0:
        print('[refresh] 数据刷新成功（增量爬取最新开奖并入库）', flush=True)
        return True
    print('[refresh] 数据刷新失败/超时 — 降级为使用现有DB数据继续监控（不阻塞）',
          flush=True)
    return False


def run_monitor(window, signals_window, skip_signals):
    monitor_py = os.path.join(HERE, 'opt_monitor.py')
    cmd = [sys.executable, monitor_py, 'all',
           '--window', str(window), '--signals-window', str(signals_window)]
    if skip_signals:
        cmd.append('--skip-signals')
    rc = _run(cmd, timeout=900)
    return rc == 0


def load_status():
    if not os.path.exists(STATUS_PATH):
        return None
    try:
        with open(STATUS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[warn] 读取状态机失败: {e}', flush=True)
        return None


def maybe_rollback(status):
    if status and status.get('status') == 'DEGRADED':
        _run([sys.executable, os.path.join(HERE, 'opt_monitor.py'),
              'rollback', '--force'], timeout=120)
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description='排列5 监控层每日常态化封装')
    ap.add_argument('--skip-update', action='store_true',
                    help='跳过数据刷新（DB 已最新时省时）')
    ap.add_argument('--window', type=int, default=300, help='漂移/性能gate窗口(期)')
    ap.add_argument('--signals-window', type=int, default=100,
                    help='信号源分析窗口(期)')
    ap.add_argument('--skip-signals', action='store_true',
                    help='跳过较慢的信号源独立命中率分析')
    args = ap.parse_args()

    print('=' * 78, flush=True)
    print(f'排列5 监控常态化 启动 @ {datetime.now():%Y-%m-%d %H:%M:%S}', flush=True)
    print('=' * 78, flush=True)

    refresh_data(args.skip_update)

    if not run_monitor(args.window, args.signals_window, args.skip_signals):
        print('\n[ERROR] 监控运行失败, 无法生成状态机。请检查数据库连接与日志。',
              flush=True)
        sys.exit(1)

    status = load_status()
    if not status:
        print('\n[ERROR] 未找到 latest_status.json, 监控输出异常。', flush=True)
        sys.exit(1)

    rolled = maybe_rollback(status)

    # 汇总（状态机为扁平结构）
    print('\n' + '=' * 78, flush=True)
    print('每日监控汇总', flush=True)
    print('=' * 78, flush=True)
    print(f'状态等级 : {status.get("status")}')
    print(f'评估时间 : {status.get("generated_at")}')
    print(f'建议动作 : {status.get("recommended_action")}')
    print(f'漂移等级 : {status.get("drift_level")} | '
          f'尾部近30期 Top5={status.get("top5_rate")}')
    print(f'质量Gate : {status.get("gate_level")} | '
          f'信号贡献最高={status.get("signals_top_contributor")} / '
          f'最低={status.get("signals_worst_contributor")}')
    if rolled:
        print('[ACTION] 检测到 DEGRADED, 已自动回滚 tuning_config.yaml 至冻结控制组。')
    print('=' * 78, flush=True)

    sys.exit(2 if status.get('status') == 'DEGRADED' else 0)


if __name__ == '__main__':
    main()
