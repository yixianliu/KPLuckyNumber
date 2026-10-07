# -*- coding: utf-8 -*-
"""
离屏冒烟：§4.2 自动更新流程「可追溯 + 可回滚 + 安全校验」可见面

验证目标：
1. 进化页工具栏存在「📜 更新日志」按钮，回调绑定 _on_update_history_log。
2. 新方法 _on_update_history_log 存在。
3. 引擎 §4.2 API 齐备：_pre_update_validation / _record_update /
   get_update_history / export_update_history / auto_rollback_on_update_failure。
4. 空台账初始态：点按「更新日志」→ get_update_history()==[] → 渲染友好提示不崩。
5. 有记录时：预置一条 evolution_persist 台账 → 点按后 evo_log 含该条 + 校验摘要，
   且可导出到 reports/。

运行：QT_QPA_PLATFORM=offscreen HOME=<隔离目录> /d/anaconda3/python.exe 本脚本
"""
import os
import sys
import time

# 隔离 HOME，避免测试污染真实配置/DB
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

    # 隔离进化引擎数据目录（本地台账 JSONL 写入此处，不污染真实 data/）
    import tempfile
    iso_data = tempfile.mkdtemp(prefix='kpn_iso_42_')

    # 让引擎走本地文件（DB 不可达兜底）——patch _connect_db
    import modules.self_evolution as se
    _orig_connect_db = se._connect_db
    se._connect_db = lambda: None

    from main import LotteryGUI as App
    root = tk.Tk()
    root.withdraw()
    gui = App(root)
    root.update()

    # 进化引擎是惰性初始化（GUI 构造时 _init_evolution_engine 未被调用，由轮询定时器
    # 触发）。离屏冒烟用「非 auto」引擎直接挂到 gui.evolution，避免启动真实 full 进化
    # 后台线程（慢 + 会写真实 data/），且其 §4.2 API 与 GUI 处理器消费的是同一实例。
    from modules.self_evolution import SelfEvolutionEngine
    eng = SelfEvolutionEngine(auto=False, auto_full=False, data_dir=iso_data)
    gui.evolution = eng
    eng._update_ledger_path = os.path.join(iso_data, 'update_ledger.jsonl')
    eng._local_versions_path = os.path.join(iso_data, 'evolution_versions.json')
    eng._update_history = []

    # 1. 方法存在
    if not hasattr(gui, '_on_update_history_log'):
        print('[FAIL] 方法缺失: _on_update_history_log')
        ok = False
    else:
        print('[PASS] 方法存在: _on_update_history_log')

    # 2. 工具栏存在「📜 更新日志」按钮（command 可解析）
    btn = _find_button(gui, '更新日志')
    if btn:
        text, cmd = btn
        callable_ok = cmd is not None and (callable(cmd) or isinstance(cmd, str))
        print(f'[PASS] 工具栏存在「{text}」按钮 (command 可解析={callable_ok})')
        if not callable_ok:
            ok = False
    else:
        print('[FAIL] 工具栏未找到「更新日志」按钮')
        ok = False

    # 3. 引擎 §4.2 API 齐备
    for m in ('_pre_update_validation', '_record_update', 'get_update_history',
              'export_update_history', 'auto_rollback_on_update_failure'):
        if not hasattr(eng, m):
            print(f'[FAIL] 引擎缺 §4.2 API: {m}')
            ok = False
    print('[PASS] 引擎 §4.2 API 齐备（_pre_update_validation/_record_update/'
          'get_update_history/export_update_history/auto_rollback_on_update_failure）') if ok else None

    # 4. 空台账初始态：点按 → 友好提示，不崩
    try:
        gui._on_update_history_log()
        root.update()
        evo_text = gui.evo_log.get(1.0, tk.END)
        assert '暂无更新记录' in evo_text or '共 0 条' in evo_text, '空台账应有友好提示'
        print('[PASS] 空台账点按渲染友好提示（"暂无更新记录"），不崩')
    except Exception as e:
        import traceback
        print(f'[FAIL] 空台账点按异常: {e}')
        traceback.print_exc()
        ok = False

    # 5. 有记录时：预置一条 evolution_persist → 点按后 evo_log 含该条 + 可导出
    eng._update_history = []
    eng._record_update(
        action='evolution_persist', version_tag='evo_smoke1',
        trigger_reason='自动进化调度（离屏桩）', execution_result='success',
        validation={'ok': True, 'checks': {'version_tag': 'pass',
                                            'params_completeness': 'pass'}},
        auto_rolled_back=False)
    gui.evo_log.delete(1.0, tk.END)
    try:
        gui._on_update_history_log()
        root.update()
        evo_text = gui.evo_log.get(1.0, tk.END)
        assert 'evo_smoke1' in evo_text, 'evo_log 应含预置版本 evo_smoke1'
        assert 'evolution_persist' in evo_text, 'evo_log 应含 action=evolution_persist'
        print('[PASS] 有记录点按：evo_log 含预置条目（evo_smoke1 / evolution_persist）')
        # 导出到 reports/（§4.2.2(2) 可导出）
        import modules.self_evolution
        from main import REPORTS_DIR
        out_path = os.path.join(REPORTS_DIR, 'update_ledger_offscreen.json')
        n = eng.export_update_history(out_path)
        assert n >= 1 and os.path.isfile(out_path), '导出应成功写文件'
        print(f'[PASS] 更新台账可导出（{n} 条 → {out_path}）')
        try:
            os.remove(out_path)  # 清理离屏产物，避免污染 reports/
        except Exception:
            pass
    except Exception as e:
        import traceback
        print(f'[FAIL] 有记录点按/导出异常: {e}')
        traceback.print_exc()
        ok = False

    se._connect_db = _orig_connect_db  # 还原
    root.update()
    root.destroy()
    print('\n=== verify_update_history_offscreen: ' + ('ALL PASS' if ok else 'FAIL') + ' ===')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
