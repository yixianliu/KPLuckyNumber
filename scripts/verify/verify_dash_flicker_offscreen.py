# -*- coding: utf-8 -*-
"""离屏探针：右侧仪表盘闪烁复现与门禁（PUP-20261019-013100 §1.2 / §2.2 GUI-01/02/09）。

三个场景：
  1. idle   —— 空闲 idle_seconds（默认 30s），仅驱动 Tk 事件循环，不产生业务事件；
  2. resize —— 20 次连续 root.geometry 变更（宽度 1px 级微抖动 + 数次真实缩放，
               模拟用户拖拽窗口），每轮排空事件队列；
  3. render —— 注入最小预测结果数据，经「统一渲染入口」连续渲染两次 + 触发一次
               概览磁贴后台刷新（等回投落地），聚焦控件销毁与帧耗时。

采集指标（方案点名顶层字段：configure_counts / destroyed_widgets / frame_p95_ms）：
  - container_configure / inner_configure: dash_container / dash_inner 的
    <Configure> 事件计数（分场景累计）；
  - itemconfig_calls: dash_container.itemconfig(width=...) 调用计数（对应验收 A3）；
  - destroyed_widgets: tk.Widget.destroy 调用计数（全量口径，分场景）；
  - nested_scrollregion: _update_result_scrollregion 在 _on_dash_configure
    调用栈内被同步调用的次数（对应验收 A4「事件循环无嵌套触发」）；
  - frame_p95_ms: 每轮 root.update() 帧耗时 P95 值。

用法：
  python scripts/verify/verify_dash_flicker_offscreen.py                # 采集（B0 基线，G0.3）
  python scripts/verify/verify_dash_flicker_offscreen.py --gate         # 门禁（A1-A4 验收）
  python scripts/verify/verify_dash_flicker_offscreen.py --scenario 2   # 只跑指定场景
  python scripts/verify/verify_dash_flicker_offscreen.py --idle-seconds 10
  python scripts/verify/verify_dash_flicker_offscreen.py --outdir reports/prep

退出码：采集模式恒 0（仅落盘指标，G0.3 以「场景 2 configure > 100 复现成立」人工/CI 判定）；
        --gate 模式 0 = A1-A4 全部达标，1 = 存在未达标项。

说明：本脚本仅测量 GUI 事件循环工程指标，与排列五开奖预测能力无关。
"""
import inspect
import json
import os
import shutil
import sys
import time
from datetime import datetime

import tkinter as tk

# ---------------------------------------------------------------------------
# 离屏环境准备（与 verify_gui_v5_offscreen.py 保持同一范式）
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_HERE = os.path.dirname(os.path.abspath(__file__))
_HOME = os.path.join(_HERE, '_flicker_test_home')
os.environ['HOME'] = _HOME
os.environ['USERPROFILE'] = _HOME
os.makedirs(_HOME, exist_ok=True)

# 主题偏好落在 PROJECT_ROOT/data 下；本脚本不切换主题，仍按惯例备份/还原。
_PREF_FILE = os.path.join(_ROOT, 'data', 'theme_preference.json')
_PREF_BACKUP = _PREF_FILE + '.flicker_verify_bak'


def _backup_theme_pref():
    """备份主题偏好文件（存在则复制，不存在则跳过）。"""
    try:
        if os.path.exists(_PREF_FILE) and not os.path.exists(_PREF_BACKUP):
            shutil.copy2(_PREF_FILE, _PREF_BACKUP)
    except Exception:
        pass


def _restore_theme_pref():
    """还原主题偏好文件并清理备份副本。"""
    try:
        if os.path.exists(_PREF_BACKUP):
            shutil.copy2(_PREF_BACKUP, _PREF_FILE)
            os.remove(_PREF_BACKUP)
    except Exception:
        pass


import main as m  # noqa: E402

# ---------------------------------------------------------------------------
# 场景 2 的 geometry 序列（全值 >= 1024x720，规避 main.py L403 root.minsize
# 钳制导致的尺寸静默回落）：以 1px 级微抖动为主，穿插 6 次显著宽度跳变
# （相邻差 >= 2px 恰好 6 次，对齐修复后 A3=itemconfig <= 6 的阈值设计）；
# 微抖动仍会派发 <Configure> 事件，是反馈循环探测的有效负载。
# ---------------------------------------------------------------------------
_RESIZE_WIDTHS = [1024, 1023, 1022, 1100, 1099, 1098, 1200, 1199, 1198,
                  1024, 1023, 1022, 1024, 1023, 1024, 1100, 1099, 1100,
                  1024, 1023]
_RESIZE_HEIGHTS = [720, 719, 718, 760, 758, 756, 800, 798, 796,
                   760, 758, 756, 760, 758, 720, 770, 768, 770,
                   720, 718]

# 门禁阈值（方案 §2.2 GUI-01 验收 A1-A4）
# 注意路径必须以 scenarios 开头（report 顶层为汇总字段，分场景指标在 scenarios 内）
_GATE = {
    'A1': ('scenarios.idle.container_configure', 2),
    'A2': ('scenarios.resize.container_configure', 60),
    'A3': ('scenarios.resize.itemconfig_calls', 6),
    'A4': ('nested_scrollregion', 0),
}

_POS_KEYS = ['wan', 'qian', 'bai', 'shi', 'ge']


def _make_minimal_pf(digits_map, issue='2026129'):
    """构造最小 pipeline_final，使聚合链路产出有效数据并走真实渲染路径。

    参数:
        digits_map: {pos_key: [5 位候选数字]}，每位 5 个候选。
        issue: 目标期号字符串。

    返回:
        dict —— _extract_source_data 可解析的 pf 结构。
    """
    return {
        'trend_prediction': {
            pk: {'numbers': list(digits_map[pk])} for pk in _POS_KEYS
        },
        'recommended_combinations': [
            {'combination': ''.join(str(digits_map[pk][0]) for pk in _POS_KEYS)}
        ],
        'next_issue': issue,
    }


def _p95(values):
    """计算 P95（线性插值最近秩口径），空列表返回 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round(0.95 * (len(ordered) - 1)))
    return ordered[idx]


class Probe:
    """探针：包装 GUI 关键方法/事件绑定，累计各类指标。"""

    def __init__(self, gui):
        """初始化探针并完成全部 monkeypatch 与事件绑定。

        参数:
            gui: m.LotteryGUI 实例（已完成构建）。
        """
        self.gui = gui
        # 指标累计
        self.container_configure = 0
        self.inner_configure = 0
        self.itemconfig_calls = 0
        self.destroyed_widgets = 0
        self.nested_scrollregion = 0
        self.inner_stack_max = 0
        self.frames = []
        # 待还原的原始引用
        self._orig_itemconfig = gui.dash_container.itemconfig
        self._orig_destroy = tk.Widget.destroy
        self._orig_scrollregion = gui._update_result_scrollregion
        self._installed = False

    def install(self):
        """安装探针：实例方法包装 + 类方法包装 + Configure 附加绑定。"""
        if self._installed:
            return
        gui = self.gui
        probe = self

        # 1) canvas itemconfig 计数（仅统计含 width= 的调用，对应 A3）
        def _counting_itemconfig(*args, **kwargs):
            if 'width' in kwargs:
                probe.itemconfig_calls += 1
            return self._orig_itemconfig(*args, **kwargs)

        gui.dash_container.itemconfig = _counting_itemconfig

        # 2) 全量控件 destroy 计数（tk.Widget 覆盖 tk/ttk 两套控件体系）
        # 诊断增强：若根窗口被销毁，打印调用栈，定位业务销毁路径（仅诊断用）
        def _counting_destroy(widget_self, *args, **kwargs):
            probe.destroyed_widgets += 1
            if widget_self is probe.gui.root:
                sys.stderr.write('[PROBE-DIAG] root.destroy() 被调用，栈帧:\n')
                for frame in inspect.stack()[1:12]:
                    sys.stderr.write('  {}:{} in {}\n'.format(
                        frame.filename, frame.lineno, frame.function))
                sys.stderr.flush()
            return self._orig_destroy(widget_self, *args, **kwargs)

        tk.Widget.destroy = _counting_destroy

        # 3) scrollregion 嵌套检测：被 _on_dash_configure 同步调用即记为嵌套（A4）
        def _counting_scrollregion():
            names = [frame.function for frame in inspect.stack()]
            if '_on_dash_configure' in names:
                probe.nested_scrollregion += 1
            return self._orig_scrollregion()

        gui._update_result_scrollregion = _counting_scrollregion

        # 4) Configure 计数器（add='+' 保留业务回调，仅追加计数）
        def _on_container_configure(event=None):
            probe.container_configure += 1

        def _on_inner_configure(event=None):
            probe.inner_configure += 1
            depth = len(inspect.stack())
            probe.inner_stack_max = max(probe.inner_stack_max, depth)

        gui.dash_container.bind('<Configure>', _on_container_configure, add='+')
        gui.dash_inner.bind('<Configure>', _on_inner_configure, add='+')
        self._installed = True

    def uninstall(self):
        """还原全部 monkeypatch（事件绑定随控件销毁一并失效，无需解绑）。"""
        if not self._installed:
            return
        self.gui.dash_container.itemconfig = self._orig_itemconfig
        tk.Widget.destroy = self._orig_destroy
        self.gui._update_result_scrollregion = self._orig_scrollregion
        self._installed = False

    def _tick(self, settle=0.02):
        """推进一轮 Tk 事件循环并记录帧耗时。

        参数:
            settle: update 后 Sleep 秒数，给系统事件队列排空时间。
        """
        t0 = time.perf_counter()
        self.gui.root.update()
        time.sleep(settle)
        self.gui.root.update()
        self.frames.append((time.perf_counter() - t0) * 1000.0)

    def snapshot(self):
        """输出当前累计指标快照（dict）。"""
        return {
            'container_configure': self.container_configure,
            'inner_configure': self.inner_configure,
            'itemconfig_calls': self.itemconfig_calls,
            'destroyed_widgets': self.destroyed_widgets,
            'nested_scrollregion': self.nested_scrollregion,
            'inner_stack_max': self.inner_stack_max,
            'frames': len(self.frames),
            'frame_p95_ms': round(_p95(self.frames), 3),
        }

    def reset_scene(self):
        """清空帧耗时序列（场景间隔离 P95 口径；累计计数器不清零，按场景差值汇报）。"""
        self.frames = []


def run_scenario_idle(probe, seconds):
    """场景 1：空闲事件循环（验收 A1）。

    参数:
        probe: Probe 实例。
        seconds: 空闲秒数。

    返回:
        dict 场景指标。
    """
    probe.reset_scene()
    base = probe.snapshot()
    deadline = time.time() + seconds
    while time.time() < deadline:
        probe._tick(settle=0.005)
    end = probe.snapshot()
    return {
        'seconds': seconds,
        'container_configure': end['container_configure'],
        'inner_configure': end['inner_configure'],
        'itemconfig_calls': 0,
        'destroyed_widgets': end['destroyed_widgets'] - base['destroyed_widgets'],
        'nested_scrollregion': end['nested_scrollregion'],
        'inner_stack_max': end['inner_stack_max'],
        'frames': end['frames'] - base['frames'],
        'frame_p95_ms': end['frame_p95_ms'],
    }


def run_scenario_resize(probe):
    """场景 2：20 次连续 geometry 变更（验收 A2/A3/A4，G0.3 复现核心）。

    返回:
        dict 场景指标。
    """
    probe.reset_scene()
    base = probe.snapshot()
    root = probe.gui.root
    for step, (width, height) in enumerate(zip(_RESIZE_WIDTHS, _RESIZE_HEIGHTS), 1):
        if not root.winfo_exists():
            sys.stderr.write('[PROBE-DIAG] 场景2 第 {} 步前 root 已销毁\n'.format(step))
            sys.stderr.flush()
            break
        root.geometry('{}x{}'.format(width, height))
        probe._tick(settle=0.02)
    end = probe.snapshot()
    return {
        'steps': len(_RESIZE_WIDTHS),
        'container_configure': end['container_configure'] - base['container_configure'],
        'inner_configure': end['inner_configure'] - base['inner_configure'],
        'itemconfig_calls': end['itemconfig_calls'] - base['itemconfig_calls'],
        'destroyed_widgets': end['destroyed_widgets'] - base['destroyed_widgets'],
        'nested_scrollregion': end['nested_scrollregion'] - base['nested_scrollregion'],
        'inner_stack_max': end['inner_stack_max'],
        'frames': end['frames'] - base['frames'],
        'frame_p95_ms': end['frame_p95_ms'],
    }


def run_scenario_render(probe):
    """场景 3：渲染注入（GUI-03 基线：全量 destroy 计数 + 帧耗时）。

    经 _render_unified_dashboard 连续渲染两次（第二次更换数据触发 key 变化
    与控件树重建），再触发一次 _refresh_result_overview 并等待回投落地。

    返回:
        dict 场景指标。
    """
    probe.reset_scene()
    base = probe.snapshot()
    gui = probe.gui
    root = gui.root

    # 还原标准尺寸，保证 canvas 容器几何稳定
    root.geometry('1024x720')
    root.update()
    time.sleep(0.05)

    # 第一次渲染：最小 pf 数据
    pf1 = _make_minimal_pf({pk: [1, 2, 3, 4, 5] for pk in _POS_KEYS})
    gui._last_pipeline_final = pf1
    gui._last_trend_result = None
    gui._last_quick_final = None
    gui._render_unified_dashboard(task_mgr=None)
    for _ in range(6):
        probe._tick(settle=0.03)

    # 第二次渲染：更换数据 → dashboard key 变化 → 触发控件树重建（全量 destroy）
    pf2 = _make_minimal_pf({pk: [9, 8, 7, 6, 0] for pk in _POS_KEYS},
                           issue='2026130')
    gui._last_pipeline_final = pf2
    gui._render_unified_dashboard(task_mgr=None)
    for _ in range(6):
        probe._tick(settle=0.03)

    # 概览磁贴刷新：后台线程查库 + root.after(0) 回投，等待落地
    gui._refresh_result_overview()
    for _ in range(50):
        probe._tick(settle=0.05)

    end = probe.snapshot()
    return {
        'renders': 2,
        'container_configure': end['container_configure'] - base['container_configure'],
        'inner_configure': end['inner_configure'] - base['inner_configure'],
        'itemconfig_calls': end['itemconfig_calls'] - base['itemconfig_calls'],
        'destroyed_widgets': end['destroyed_widgets'] - base['destroyed_widgets'],
        'nested_scrollregion': end['nested_scrollregion'] - base['nested_scrollregion'],
        'inner_stack_max': end['inner_stack_max'],
        'frames': end['frames'] - base['frames'],
        'frame_p95_ms': end['frame_p95_ms'],
    }


def evaluate_gates(report):
    """按 _GATE 阈值判定 A1-A4，返回判定明细列表。

    参数:
        report: 完整采集报告（dict）。

    返回:
        list[dict] —— 每项含 gate/desc/actual/threshold/passed。
    """
    results = []

    def _get(path):
        cur = report
        for part in path.split('.'):
            cur = cur.get(part, {})
        return cur if isinstance(cur, int) else 0

    for gate in sorted(_GATE):
        path, threshold = _GATE[gate]
        actual = _get(path)
        results.append({
            'gate': gate,
            'path': path,
            'actual': actual,
            'threshold': threshold,
            'passed': actual <= threshold,
        })
    return results


def write_markdown(report, out_path):
    """生成人读复现报告 flicker_repro.md。

    参数:
        report: 完整采集报告（dict）。
        out_path: markdown 输出路径。
    """
    scenarios = report['scenarios']
    lines = [
        '# 右侧仪表盘闪烁复现报告',
        '',
        '- 编号: PUP-20261019-013100 §1.2（G0.3）',
        '- 生成时间: {}'.format(report['generated_at']),
        '- 模式: {}'.format('门禁验收(--gate)' if report['mode'] == 'gate' else '基线采集'),
        '- 高门槛复现判定（G0.3）: 场景2 `dash_container` Configure 计数 > 100',
        '',
        '## 场景指标',
        '',
        '| 指标 | 场景1 空闲 | 场景2 连续resize | 场景3 渲染注入 |',
        '| --- | --- | --- | --- |',
    ]
    for field in ('container_configure', 'inner_configure', 'itemconfig_calls',
                  'destroyed_widgets', 'nested_scrollregion', 'frames',
                  'frame_p95_ms'):
        lines.append('| {} | {} | {} | {} |'.format(
            field,
            scenarios.get('idle', {}).get(field, '—'),
            scenarios.get('resize', {}).get(field, '—'),
            scenarios.get('render', {}).get(field, '—'),
        ))
    lines += [
        '',
        '## 口径说明',
        '',
        '- `container_configure`/`inner_configure`: 对 dash_container/dash_inner 的'
        ' `<Configure>` 事件以 add="+" 附加计数回调，不干扰业务回调；',
        '- `itemconfig_calls`: 仅统计 dash_container.itemconfig 调用中携带 width='
        ' 参数的次数（GUI-01 阈值守卫的直接作用对象）；',
        '- `destroyed_widgets`: tk.Widget.destroy 全量计数（含 render 场景控件树重建）；',
        '- `nested_scrollregion`: _update_result_scrollregion 出现在'
        ' _on_dash_configure 调用栈内的同步调用次数（A4 嵌套触发指标）；',
        '- `frame_p95_ms`: 每轮 root.update() 帧耗时 P95（ms）。',
        '',
    ]
    if report.get('gates') is not None:
        lines += [
            '## 门禁判定（A1-A4）',
            '',
            '| 门禁 | 指标路径 | 实测值 | 阈值 | 判定 |',
            '| --- | --- | --- | --- | --- |',
        ]
        for item in report['gates']:
            lines.append('| {} | {} | {} | <= {} | {} |'.format(
                item['gate'], item['path'], item['actual'], item['threshold'],
                'PASS' if item['passed'] else 'FAIL'))
        lines.append('')
    if report.get('baseline_note'):
        lines += ['## 备注', '', report['baseline_note'], '']
    lines += [
        '本报告仅记录 GUI 事件循环工程指标，与排列五开奖预测能力无关。',
        '',
        '> 【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，'
        '本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。请理性购彩，量力而行。',
        '',
    ]
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def main():
    """主流程：解析参数 → 构造离屏 GUI → 跑场景 → 落盘 JSON/MD → 判定/退出。"""
    args = sys.argv[1:]
    gate_mode = '--gate' in args
    idle_seconds = 30
    out_dir = os.path.join(_ROOT, 'reports', 'prep')
    only_scenario = None
    # 简易参数解析（避免引入 argparse 之外的复杂度，行为与 argparse 等价）
    for i, arg in enumerate(args):
        if arg == '--idle-seconds' and i + 1 < len(args):
            idle_seconds = int(args[i + 1])
        elif arg == '--scenario' and i + 1 < len(args):
            only_scenario = int(args[i + 1])
        elif arg == '--outdir' and i + 1 < len(args):
            out_dir = os.path.join(_ROOT, args[i + 1]) \
                if not os.path.isabs(args[i + 1]) else args[i + 1]

    os.makedirs(out_dir, exist_ok=True)
    _backup_theme_pref()

    root = tk.Tk()
    root.withdraw()
    gui = m.LotteryGUI(root)
    # 离屏探针专用：解除 main.py L403 root.minsize(1024, 720) 的尺寸钳制，
    # 否则 < 1024x720 的 geometry 会被 WM 静默钳回基准尺寸，Configure 事件
    # 不再派发，场景 2 的反馈循环探测将失效（不修改生产代码）
    try:
        root.minsize(1, 1)
    except Exception:
        pass
    # withdraw 态下 winfo_width=1（伪几何），deiconify 后才能测真实布局
    try:
        root.deiconify()
    except Exception:
        pass
    root.geometry('1024x720')
    root.update()
    time.sleep(0.1)
    root.update()

    # 环境诊断：确认离屏窗口真实映射与几何状态（排查 Configure 不派发类问题）
    print('[DIAG] root.state={} viewable={} winfo_width={} winfo_height={} '
          'dash_width={} nb_tab={}'.format(
              root.state(), bool(root.winfo_viewable()),
              root.winfo_width(), root.winfo_height(),
              gui.dash_container.winfo_width(),
              gui.output_nb.select() if hasattr(gui, 'output_nb') else 'N/A'))

    probe = Probe(gui)
    probe.install()

    scenarios = {}
    try:
        if only_scenario in (None, 1):
            print('[场景1] 空闲 {}s ...'.format(idle_seconds))
            scenarios['idle'] = run_scenario_idle(probe, idle_seconds)
        if only_scenario in (None, 2):
            print('[场景2] 20 次连续 geometry 变更 ...')
            scenarios['resize'] = run_scenario_resize(probe)
        if only_scenario in (None, 3):
            print('[场景3] 渲染注入（统一入口 x2 + 概览刷新） ...')
            scenarios['render'] = run_scenario_render(probe)
    finally:
        probe.uninstall()
        try:
            root.destroy()
        except Exception:
            pass
        _restore_theme_pref()

    # 顶层汇总字段（方案点名）：configure_counts / destroyed_widgets / frame_p95_ms
    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'mode': 'gate' if gate_mode else 'collect',
        'environment': {
            'python': sys.version.split()[0],
            'platform': sys.platform,
            'tk_version': root.tk.call('info', 'patchlevel') if False else None,
        },
        'idle_seconds': idle_seconds,
        'scenarios': scenarios,
        'configure_counts': {
            name: {
                'container': data.get('container_configure', 0),
                'inner': data.get('inner_configure', 0),
            } for name, data in scenarios.items()
        },
        'destroyed_widgets': {
            name: data.get('destroyed_widgets', 0) for name, data in scenarios.items()
        },
        'frame_p95_ms': {
            name: data.get('frame_p95_ms', 0.0) for name, data in scenarios.items()
        },
    }
    # 嵌套指标汇总（A4 跨场景口径）
    report['nested_scrollregion'] = sum(
        data.get('nested_scrollregion', 0) for data in scenarios.values())

    json_path = os.path.join(out_dir, 'flicker_baseline.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # G0.3 复现判定标注（采集模式）：场景 2 container Configure > 100 = 反馈循环复现成立
    if 'resize' in scenarios:
        resize_cfg = scenarios['resize']['container_configure']
        report['baseline_note'] = (
            '场景2 dash_container Configure = {}；{}（方案 G0.3 阈值 > 100，'
            '修复后目标 <= 60，见 §2.2 A2）'.format(
                resize_cfg,
                '反馈循环复现成立' if resize_cfg > 100
                else '未达 G0.3 复现阈值，反馈循环显著度低于方案预期，以实测记录为准'))

    md_path = os.path.join(out_dir, 'flicker_repro.md')
    write_markdown(report, md_path)

    # 控制台摘要
    print('\n=== 指标摘要 ===')
    for name, data in scenarios.items():
        print('  [{}] container_configure={} inner_configure={} itemconfig={} '
              'destroyed={} nested={} frame_p95_ms={}'.format(
                  name,
                  data.get('container_configure', 0),
                  data.get('inner_configure', 0),
                  data.get('itemconfig_calls', 0),
                  data.get('destroyed_widgets', 0),
                  data.get('nested_scrollregion', 0),
                  data.get('frame_p95_ms', 0.0)))
    print('  nested_scrollregion(合计) = {}'.format(report['nested_scrollregion']))
    print('  JSON: {}'.format(json_path))
    print('  MD  : {}'.format(md_path))

    if not gate_mode:
        print('\n采集模式完成（未做门禁判定）。修复后请以 --gate 复跑验证 A1-A4。')
        sys.stdout.flush()
        os._exit(0)

    gates = evaluate_gates(report)
    print('\n=== 门禁判定（A1-A4） ===')
    failed = 0
    for item in gates:
        mark = 'PASS' if item['passed'] else 'FAIL'
        if not item['passed']:
            failed += 1
        print('  [{}] {} actual={} threshold<={}'.format(
            mark, item['path'], item['actual'], item['threshold']))
    if failed:
        print('\n门禁未通过：{}/{} 项超标。'.format(failed, len(gates)))
        sys.stdout.flush()
        os._exit(1)
    print('\n门禁全部通过（A1-A4）。')
    sys.stdout.flush()
    os._exit(0)


if __name__ == '__main__':
    main()
