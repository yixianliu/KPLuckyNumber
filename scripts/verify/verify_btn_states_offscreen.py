"""离屏 e2e 冒烟测试：验证按钮 hover/click/loading 态交互精化（v4.0 §2.4）。

背景：
  - _add_big_button / _add_action_button / _add_inline_button / _create_toolbar_button
    已记录正常态配色（_normal_bg/_normal_fg）并加禁用守卫；
  - _set_buttons_state(DISABLED) 统一置灰，(NORMAL) 还原配色；
  - 工具栏按钮 hover 在禁用态不再变色。
本脚本在 offscreen 环境下构造 GUI，断言：
  1. 主按钮记录正常态配色；
  2. 禁用态后所有按钮置灰（bg=bg_input, fg=text_disabled）且 state=disabled；
  3. 禁用态下 <Enter> 不触发变色（守卫生效）；
  4. 恢复 NORMAL 后按钮还原正常态配色。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['HOME'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_btn_test_home')
os.environ['USERPROFILE'] = os.environ['HOME']

import tkinter as tk  # noqa: E402
import main as m  # noqa: E402


def run():
    root = tk.Tk()
    root.withdraw()
    gui = m.LotteryGUI(root)
    root.update_idletasks()

    btns = gui._buttons
    assert btns, "应存在已注册按钮"

    # ---- 断言 1：至少一个按钮记录了正常态配色 ----
    with_norm = [b for b in btns if hasattr(b, '_normal_bg')]
    assert with_norm, "无按钮记录 _normal_bg，loading 态还原将失效"
    sample = with_norm[0]
    print(f"[PASS] 按钮记录正常态配色: {len(with_norm)}/{len(btns)} 个有 _normal_bg")

    # ---- 断言 2：禁用态置灰 ----
    gui._set_buttons_state(tk.DISABLED)
    root.update_idletasks()
    all_disabled = all(str(b['state']) == 'disabled' for b in btns)
    assert all_disabled, "禁用后应全部 state=disabled"
    bg_input = m.COLORS['bg_input']
    text_disabled = m.COLORS['text_disabled']
    grayed = [b for b in with_norm
              if b.cget('bg') == bg_input and b.cget('fg') == text_disabled]
    assert grayed, "禁用后记录过配色的按钮应置灰"
    print(f"[PASS] 禁用态置灰: {len(grayed)} 个按钮 bg=bg_input fg=text_disabled")

    # ---- 断言 3：禁用态下 <Enter> 守卫不触发变色 ----
    probe = with_norm[0]
    probe.event_generate('<Enter>')
    root.update_idletasks()
    still_gray = probe.cget('bg') == bg_input
    assert still_gray, "禁用态 <Enter> 不应改变背景色（守卫失效）"
    print(f"[PASS] 禁用态 <Enter> 守卫生效（背景维持置灰 {probe.cget('bg')}）")

    # ---- 断言 4：恢复 NORMAL 还原配色（token 经 _resolve_color_token 动态解析）----
    gui._set_buttons_state(tk.NORMAL)
    root.update_idletasks()
    restored = [b for b in with_norm
                if b.cget('bg') == gui._resolve_color_token(b._normal_bg)
                and b.cget('fg') == gui._resolve_color_token(b._normal_fg)]
    assert restored, "恢复后应按 token/静态色还原正常态配色"
    assert all(str(b['state']) == 'normal' for b in btns), "恢复后 state=normal"
    print(f"[PASS] 恢复 NORMAL 还原配色: {len(restored)} 个按钮还原成功")

    # ---- 断言 5：主题切换后还原仍正确（token 化不泄漏旧主题色）----
    # 切到另一主题，再恢复按钮，确认 bg 跟随当前主题 token 值而非创建时的 hex 快照
    old_theme = m.COLORS
    new_theme = m.ThemeManager.LIGHT_THEME if old_theme is m.ThemeManager.DARK_THEME \
        else m.ThemeManager.DARK_THEME
    gui._apply_theme(new_theme)
    root.update_idletasks()
    gui._set_buttons_state(tk.NORMAL)  # 在新主题下还原
    root.update_idletasks()
    bg_card_now = m.COLORS['bg_card']
    ok_theme = [b for b in with_norm
                if b._normal_bg == 'bg_card' and b.cget('bg') == bg_card_now]
    assert ok_theme, "主题切换后 token 按钮应取当前主题 bg_card 值，而非旧 hex 快照"
    print(f"[PASS] 主题切换后 {len(ok_theme)} 个 token 按钮 bg 跟随新主题 bg_card={bg_card_now}")

    root.destroy()
    print("\n=== verify_btn_states_offscreen: ALL PASS ===")
    return 0


if __name__ == '__main__':
    try:
        code = run()
        import shutil
        shutil.rmtree(os.environ.get('HOME', ''), ignore_errors=True)
        raise SystemExit(code)
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
