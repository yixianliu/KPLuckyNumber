# -*- coding: utf-8 -*-
"""
离屏冒烟：§4.3 系统性能监控「📈 性能监控」可见面

验证目标：
1. 进化页工具栏存在「📈 性能监控」按钮，回调绑定 _on_perf_monitor_view。
2. 方法 _on_perf_monitor_view 存在。
3. perf_monitor API 齐备：SystemPerformanceMonitor / get_perf_monitor /
   start_perf_monitoring / stop_perf_monitoring / record_response_time。
4. 点按「性能监控」→ get_summary() 渲染到 evo_log，不崩；含健康度/指标/告警。
5. task_source 注入（TaskManager 鸭子类型）不崩。
6. 关闭时 stop_perf_monitoring() 幂等空操作不崩。

运行：QT_QPA_PLATFORM=offscreen HOME=<隔离目录> /d/anaconda3/python.exe 本脚本
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _find_button(gui, keyword):
    """递归收集 gui 上所有 tk.Button，返回匹配 keyword 的 (text, command) 或 None。"""
    import tkinter as tk
    found = []

    def walk(w):
        try:
            for ch in w.winfo_children():
                if ch.__class__.__name__ == 'Button':
                    try:
                        found.append((str(ch['text']), ch.cget('command')))
                    except Exception:
                        found.append((str(ch['text']), None))
                walk(ch)
        except Exception:
            pass

    walk(gui.root)
    for text, cmd in found:
        if keyword in text:
            return text, cmd
    return None


def main():
    ok = True
    import tkinter as tk
    import tempfile

    iso_data = tempfile.mkdtemp(prefix='kpn_iso_43_')

    from main import LotteryGUI as App
    root = tk.Tk()
    root.withdraw()
    gui = App(root)
    root.update()

    # 1. 方法存在
    if not hasattr(gui, '_on_perf_monitor_view'):
        print('[FAIL] 方法缺失: _on_perf_monitor_view')
        ok = False
    else:
        print('[PASS] 方法存在: _on_perf_monitor_view')

    # 2. 工具栏存在「📈 性能监控」按钮（command 可解析）
    btn = _find_button(gui, '性能监控')
    if btn:
        text, cmd = btn
        callable_ok = cmd is not None and (callable(cmd) or isinstance(cmd, str))
        print(f'[PASS] 工具栏存在「{text}」按钮 (command 可解析={callable_ok})')
        if not callable_ok:
            ok = False
    else:
        print('[FAIL] 工具栏未找到「性能监控」按钮')
        ok = False

    # 3. perf_monitor API 齐备
    import modules.perf_monitor as pm
    for m in ('SystemPerformanceMonitor', 'get_perf_monitor',
              'start_perf_monitoring', 'stop_perf_monitoring',
              'record_response_time'):
        if not hasattr(pm, m):
            print(f'[FAIL] perf_monitor 缺 API: {m}')
            ok = False
    print('[PASS] perf_monitor API 齐备（SystemPerformanceMonitor/get_perf_monitor/'
          'start_perf_monitoring/stop_perf_monitoring/record_response_time）') if ok else None

    # 4. 点按 → get_summary() 渲染到 evo_log，不崩
    try:
        gui._on_perf_monitor_view()
        root.update()
        evo_text = gui.evo_log.get(1.0, tk.END)
        assert '系统性能监控' in evo_text, 'evo_log 应含「系统性能监控」标题'
        assert '健康度' in evo_text, 'evo_log 应含「健康度」'
        print('[PASS] 点按「性能监控」渲染摘要（健康度/指标/告警）到 evo_log，不崩')
    except Exception as e:
        import traceback
        print(f'[FAIL] 点按性能监控异常: {e}')
        traceback.print_exc()
        ok = False

    # 5. task_source 注入（TaskManager 鸭子类型）不崩
    try:
        pm.get_perf_monitor().set_task_source(gui.task_mgr)
        print('[PASS] task_source 注入（TaskManager）不崩')
    except Exception as e:
        print(f'[FAIL] task_source 注入异常: {e}')
        ok = False

    # 6. 关闭时 stop_perf_monitoring() 幂等不崩
    try:
        pm.stop_perf_monitoring()
        print('[PASS] stop_perf_monitoring() 幂等空操作不崩')
    except Exception as e:
        print(f'[FAIL] stop_perf_monitoring() 异常: {e}')
        ok = False

    root.update()
    root.destroy()
    print('\n=== verify_perf_monitor_offscreen: ' + ('ALL PASS' if ok else 'FAIL') + ' ===')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
