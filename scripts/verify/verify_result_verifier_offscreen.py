# -*- coding: utf-8 -*-
"""
离屏冒烟：§4.4 结果自我验证与优化建议「🔬 自我验证」可见面

验证目标：
1. 进化页工具栏存在「🔬 自我验证」按钮，回调绑定 _on_result_self_verify。
2. 方法 _on_result_self_verify 存在。
3. result_verifier API 齐备：ResultSelfVerifier / get_result_verifier /
   self_verify / generate_suggestions / get_coverage。
4. 无流水线产物时：点按「自我验证」→ 友好提示「请先开始分析」，不崩。
5. 预置一段 _last_pipeline_final 后：点按 → evo_log 渲染验证维度 + 分级建议，
   含诚实边界声明，不崩。
6. RESULT_VERIFIER_ENABLE=False 时：点按 → 提示已关闭，不崩。

运行：QT_QPA_PLATFORM=offscreen HOME=<隔离目录> /d/anaconda3/python.exe 本脚本
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _find_button(gui, keyword):
    """递归收集 gui 上所有 tk.Button，返回匹配 keyword 的 (text, command) 或 None。"""
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


def _sample_final():
    """构造一段最小可用的 final_report 形状（fused 嵌套在 prediction）。"""
    return {
        'current_issue': '2026109',
        'next_issue': '2026110',
        'recommended_combinations': [
            {'rank': 1, 'combination': '12345', 'numbers': [1, 2, 3, 4, 5]}
        ],
        'per_algo_top_predictions': {'frequency_weighted': {'hit_rate': 0.10}},
        'prediction': {
            'fused_probabilities': [
                {0: 0.2, 1: 0.2, 2: 0.2, 3: 0.2, 4: 0.2} for _ in range(5)
            ],
            'top_combinations': [{'numbers': [1, 2, 3, 4, 5]}],
        },
    }


def main():
    ok = True
    import tkinter as tk

    from main import LotteryGUI as App
    root = tk.Tk()
    root.withdraw()
    gui = App(root)
    root.update()

    # 1. 方法存在
    if not hasattr(gui, '_on_result_self_verify'):
        print('[FAIL] 方法缺失: _on_result_self_verify')
        ok = False
    else:
        print('[PASS] 方法存在: _on_result_self_verify')

    # 2. 工具栏存在「🔬 自我验证」按钮
    btn = _find_button(gui, '自我验证')
    if btn:
        text, cmd = btn
        callable_ok = cmd is not None and (callable(cmd) or isinstance(cmd, str))
        print(f'[PASS] 工具栏存在「{text}」按钮 (command 可解析={callable_ok})')
        if not callable_ok:
            ok = False
    else:
        print('[FAIL] 工具栏未找到「自我验证」按钮')
        ok = False

    # 3. result_verifier API 齐备
    import modules.result_verifier as rvm
    for m in ('ResultSelfVerifier', 'get_result_verifier', 'self_verify',
              'generate_suggestions', 'get_coverage'):
        if not hasattr(rvm, m):
            print(f'[FAIL] result_verifier 缺 API: {m}')
            ok = False
    print('[PASS] result_verifier API 齐备（ResultSelfVerifier/get_result_verifier/'
          'self_verify/generate_suggestions/get_coverage）') if ok else None

    # 4. 无产物时点按 → 友好提示，不崩
    gui._last_pipeline_final = None
    gui._last_quick_final = None
    try:
        gui._on_result_self_verify()
        root.update()
        print('[PASS] 无产物点按：友好提示（"请先开始分析"），不崩')
    except Exception as e:
        import traceback
        print(f'[FAIL] 无产物点按异常: {e}')
        traceback.print_exc()
        ok = False

    # 5. 预置产物后点按 → evo_log 渲染验证维度 + 分级建议 + 诚实边界
    gui._last_pipeline_final = _sample_final()
    gui.evo_log.delete(1.0, tk.END)
    try:
        gui._on_result_self_verify()
        root.update()
        evo_text = gui.evo_log.get(1.0, tk.END)
        assert '结果自我验证' in evo_text, 'evo_log 应含「结果自我验证」标题'
        assert '分级优化建议' in evo_text, 'evo_log 应含「分级优化建议」'
        assert '诚实边界' in evo_text, 'evo_log 应含诚实边界声明'
        print('[PASS] 有产物点按：evo_log 含验证维度 + 分级建议 + 诚实边界，不崩')
    except Exception as e:
        import traceback
        print(f'[FAIL] 有产物点按异常: {e}')
        traceback.print_exc()
        ok = False

    # 6. RESULT_VERIFIER_ENABLE=False 时点按 → 提示已关闭，不渲染 evo_log
    try:
        import config as _cfg
        _orig = getattr(_cfg, 'RESULT_VERIFIER_ENABLE', True)
        _cfg.RESULT_VERIFIER_ENABLE = False
        gui.evo_log.delete(1.0, tk.END)
        gui._on_result_self_verify()
        root.update()
        evo_text = gui.evo_log.get(1.0, tk.END)
        # 关闭态应 early-return：evidence_log 未被插入「结果自我验证」段落
        assert '结果自我验证' not in evo_text, '关闭态不应渲染验证段落'
        print('[PASS] RESULT_VERIFIER_ENABLE=False 点按：提示已关闭、未渲染验证，不崩')
    except Exception as e:
        import traceback
        print(f'[FAIL] 开关关闭点按异常: {e}')
        traceback.print_exc()
        ok = False
    finally:
        try:
            import config as _cfg2
            _cfg2.RESULT_VERIFIER_ENABLE = _orig
        except Exception:
            pass

    root.update()
    root.destroy()
    print('\n=== verify_result_verifier_offscreen: '
          + ('ALL PASS' if ok else 'FAIL') + ' ===')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
