"""离屏 e2e 守卫：v5.0 §2.1/§2.2 阶段二 UI/UX 专项验收（51 项断言）。

覆盖 §2.2 验收标准：
  A. 按钮三态（hover / disabled / 正常态还原）+ 中央样式工厂契约（13 项）
  B. 键盘可达性（takefocus + 快捷键绑定，7 项）
  C. 明暗主题切换无需重启即全控件刷新（无旧主题色残留，17 项：
  含 v5.2 GUI-06 新增 C14 全树 10 属性残留扫描 / C14b 切回双向扫描 /
  C15 tag 重放 spy / C16 tag 与 canvas 注册表非空防假覆盖）
  D. 工厂纯函数契约（token 解析 / 反查 / 变亮 / 禁用守卫，13 项）
  E. 进度反馈与状态栏组件存在性（5 项）

设计要点：
  * offscreen 构造 GUI（withdraw），不依赖显示设备；
  * **不触发真实分析任务**（避免分钟级等待与 DB 副作用），进度反馈仅断言组件存在；
  * 主题切换断言「token 化按钮跟随新主题」，而非比对硬编码 hex；
  * 运行前备份、结束必还原 ``data/theme_preference.json``——_toggle_theme 会写回
    真实偏好，测试不得污染用户设置；
  * hover 用例不依赖 Tk 事件派发：bind_hover 返回回调，测试直接调用，避免
    ``event_generate('<Enter>')`` 在部分平台对合成事件失效导致「假通过」。

用法:
    python scripts/verify/verify_ui_offscreen.py      # 退出码 0 = 全部通过
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ui_test_home')
os.environ['HOME'] = _BASE
os.environ['USERPROFILE'] = _BASE
os.makedirs(_BASE, exist_ok=True)

# 主题偏好落在 PROJECT_ROOT/data 下（非 HOME），_toggle_theme 会写回真实偏好文件。
# 守卫：运行前备份、结束必还原，避免离屏测试污染用户的真实主题设置。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PREF_FILE = os.path.join(_PROJECT_ROOT, 'data', 'theme_preference.json')
_PREF_BACKUP = _PREF_FILE + '.ui_verify_bak'


def _backup_theme_pref():
    """备份主题偏好（仅在尚无备份时，防止覆盖真实偏好）。"""
    try:
        if os.path.exists(_PREF_FILE) and not os.path.exists(_PREF_BACKUP):
            shutil.copy2(_PREF_FILE, _PREF_BACKUP)
    except Exception:
        pass


def _restore_theme_pref():
    """还原主题偏好，清除备份临时文件。"""
    try:
        if os.path.exists(_PREF_BACKUP):
            shutil.copy2(_PREF_BACKUP, _PREF_FILE)
            os.remove(_PREF_BACKUP)
    except Exception:
        pass

import tkinter as tk  # noqa: E402
import tkinter.ttk as ttk  # noqa: E402
import main as m  # noqa: E402
from modules import ui_styles  # noqa: E402

_PASSED = []


def check(cond, desc):
    """断言并记分；失败时抛出 AssertionError 终止。"""
    assert cond, f'FAIL: {desc}'
    _PASSED.append(desc)
    print(f'[PASS] {desc}')


def _has_bind(widget, seq):
    """判断控件是否绑定了某事件序列。"""
    try:
        return bool(widget.bind(seq))
    except Exception:
        return False


def run():
    root = tk.Tk()
    root.withdraw()
    gui = m.LotteryGUI(root)
    root.update_idletasks()

    COLORS0 = dict(m.COLORS)
    btns = gui._buttons
    check(bool(btns), 'A1 主界面存在已注册按钮')

    # ---------------- A. 按钮三态 + 工厂契约 ----------------
    check(all(hasattr(b, '_normal_bg') for b in btns),
          f'A2 全部按钮记录 _normal_bg（{len(btns)} 个）')
    check(all(hasattr(b, '_normal_fg') for b in btns),
          'A3 全部按钮记录 _normal_fg')
    tokenized = [b for b in btns
                 if isinstance(getattr(b, '_normal_bg', None), str)
                 and b._normal_bg in m.COLORS]
    check(len(tokenized) == len(btns),
          f'A4 _normal_bg 全部 token 化（{len(tokenized)}/{len(btns)}，hex 快照=0）')
    check(all(str(b.cget('takefocus')) in ('1', 'True', 'true') for b in btns),
          'A5 全部按钮 takefocus=True（可 Tab 聚焦）')
    check(all(_has_bind(b, '<Enter>') for b in btns), 'A6 全部按钮绑定 <Enter>（hover）')
    check(all(_has_bind(b, '<Leave>') for b in btns), 'A7 全部按钮绑定 <Leave>（还原）')
    # 按下态：主按钮（_add_big/_add_action/_add_inline）保留，工具栏按钮本就无此绑定，
    # 故用「存在性 + 成对性」断言，而非要求全部按钮都有。
    press_btns = [b for b in btns if _has_bind(b, '<Button-1>')]
    check(len(press_btns) > 0,
          f'A8 存在按钮保留按下态 <Button-1>（{len(press_btns)} 个，工厂未覆盖）')
    check(all(_has_bind(b, '<ButtonRelease-1>') for b in press_btns),
          'A9 按下态按钮均配对 <ButtonRelease-1>（按下反馈完整）')

    # 禁用态：统一置灰 + hover 守卫
    gui._set_buttons_state(tk.DISABLED)
    root.update_idletasks()
    check(all(str(b.cget('state')) == 'disabled' for b in btns),
          'A10 全部按钮可置 disabled（长任务期间禁用）')
    check(all(str(b.cget('fg')) == m.COLORS['text_disabled'] for b in btns),
          'A11 禁用态文字降为 text_disabled（置灰）')

    before = [str(b.cget('bg')) for b in btns]
    for b in btns:
        b.event_generate('<Enter>')
    root.update()
    check([str(b.cget('bg')) for b in btns] == before,
          'A12 禁用态 hover 守卫生效（背景不变色）')

    gui._set_buttons_state(tk.NORMAL)
    root.update_idletasks()
    check(all(str(b.cget('bg')) == m.COLORS[b._normal_bg] for b in btns
              if b._normal_bg in m.COLORS),
          'A13 恢复 NORMAL 后按 token 还原配色')

    # ---------------- B. 键盘可达性 ----------------
    check(_has_bind(root, '<Control-Return>'), 'B1 绑定 <Control-Return>（开始分析）')
    check(_has_bind(root, '<Control-KP_Enter>'), 'B2 绑定 <Control-KP_Enter>（小键盘回车）')
    check(_has_bind(root, '<Control-e>') or _has_bind(root, '<Control-E>'),
          'B3 绑定 <Control-e>（导出）')
    check(_has_bind(root, '<Control-c>'), 'B4 保留 <Control-c>（复制）')
    check(_has_bind(root, '<Escape>'), 'B5 保留 <Escape>（取消任务）')
    check(not _has_bind(root, '<Control-f>'), 'B6 已停用 <Control-f>（日志页移除）')
    check(not _has_bind(root, '<Control-a>'), 'B7 已停用 <Control-a>（日志页移除）')

    # ---------------- C. 明暗主题切换（无需重启） ----------------
    start_theme = gui._current_theme
    check(start_theme in ('dark', 'light'), f'C1 初始主题合法: {start_theme}')
    old_bg_card = m.COLORS['bg_card']
    old_text_primary = m.COLORS['text_primary']

    gui._toggle_theme()
    root.update_idletasks()
    check(gui._current_theme != start_theme, 'C2 切换后主题标记变化（无需重启）')
    check(m.COLORS['bg_card'] != old_bg_card, 'C3 切换后 COLORS.bg_card 已更新')

    token_btns = [b for b in btns if b._normal_bg in m.COLORS]
    check(len(token_btns) > 0, 'C4 存在 token 化按钮用于校验跟随')
    _mm = [(str(b.cget('text'))[:12], str(b.cget('bg')), m.COLORS[b._normal_bg])
           for b in token_btns
           if str(b.cget('bg')) != m.COLORS[b._normal_bg]]
    check(all(str(b.cget('bg')) == m.COLORS[b._normal_bg] for b in token_btns),
          f'C5 切换后 {len(token_btns)} 个 token 按钮 bg 跟随新主题'
          + (f'（未跟随(文本,实际,期望): {_mm[:5]}）' if _mm else ''))
    check(not any(str(b.cget('bg')) == old_bg_card for b in token_btns),
          'C6 切换后无按钮残留旧主题 bg_card（无白块/旧色）')
    check(not any(str(b.cget('fg')) == old_text_primary for b in token_btns),
          'C7 切换后无按钮残留旧主题 text_primary（文字可见）')
    check(str(root.cget('bg')) == m.COLORS['bg_primary'], 'C8 根窗口 bg 跟随新主题')
    check(gui._theme_btn.cget('text') in ('☀️', '🌙'), 'C9 主题按钮 emoji 已更新')

    gui._toggle_theme()  # 切回
    root.update_idletasks()
    check(gui._current_theme == start_theme, 'C10 再次切换回到初始主题')
    check(all(str(b.cget('bg')) == m.COLORS[b._normal_bg] for b in token_btns),
          'C11 切回后 token 按钮 bg 恢复初始主题值')

    # C12：hover 离开须还原为「切换后」主题色，而非绑定瞬间捕获的旧主题快照。
    # 直接在主界面上重绑一组可测回调，覆盖真实按钮（含真实 _normal_bg token）。
    _hover_btn = token_btns[0]
    ui_styles.remember_button_normal(_hover_btn, _hover_btn._normal_bg,
                                     _hover_btn._normal_fg)
    _enter_cb, _leave_cb = ui_styles.bind_hover(_hover_btn, lambda: m.COLORS)
    check(_enter_cb is not None and _leave_cb is not None, 'C12 bind_hover 主界面按钮可重绑')
    _leave_cb()
    check(str(_hover_btn.cget('bg')) == m.COLORS[_hover_btn._normal_bg],
          'C13 hover 离开按当前主题还原（闭包不残留旧主题快照）')

    # C14：widget 树全扫（10 属性）——切换后无旧主题独有值残留。
    # 覆盖 _update_widget_colors 扩展清单：activebackground/
    # insertbackground/highlightbackground/troughcolor 等此前不在清单，
    # 切换后残留旧色（Text 插入光标/高亮边框/滚动槽）。
    _attrs_10 = ('background', 'foreground',
                 'activebackground', 'activeforeground',
                 'insertbackground', 'highlightbackground',
                 'highlightcolor', 'selectbackground',
                 'selectforeground', 'troughcolor')

    def _only_vals(theme, other):
        """仅 theme 独有的色值集合（排除两主题共用值）。"""
        return ({v for v in theme.values() if isinstance(v, str)}
                - {v for v in other.values() if isinstance(v, str)})

    def _scan_residue(only_vals):
        """全树扫描命中旧主题独有值的 (控件, 属性, 色值) 列表。"""
        hits = []
        stack = [root] + list(root.winfo_children())
        seen_ids = {id(root)}
        while stack:
            w = stack.pop()
            if id(w) in seen_ids:
                continue
            seen_ids.add(id(w))
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass
            for attr in _attrs_10:
                try:
                    val = str(w.cget(attr))
                except Exception:
                    continue
                if val in only_vals:
                    hits.append((type(w).__name__, attr, val))
        return hits

    _old_theme_obj = m.COLORS
    _other_theme_obj = (m.ThemeManager.LIGHT_THEME
                        if _old_theme_obj is m.ThemeManager.DARK_THEME
                        else m.ThemeManager.DARK_THEME)
    gui._toggle_theme()  # 切到另一主题
    root.update_idletasks()
    _hits = _scan_residue(_only_vals(_old_theme_obj, _other_theme_obj))
    check(not _hits,
          f'C14 切换后全树 10 属性无旧主题独有值残留（命中: {_hits[:5]}）')
    _cur_theme_obj = m.COLORS
    gui._toggle_theme()  # 切回
    root.update_idletasks()
    _hits2 = _scan_residue(_only_vals(_cur_theme_obj, _old_theme_obj))
    check(not _hits2,
          f'C14b 切回后全树 10 属性无中途主题残留（命中: {_hits2[:5]}）')

    # C15：Text/Treeview tag 重放 spy——逐项重放后实际取到当前主题值。
    # tag_configure 无 cget 反查入口，唯一正确性是「重放后取当前值」。
    _tag_regs = list(getattr(gui, '_theme_tag_registrations', []) or [])
    gui._refresh_theme_tags(m.COLORS)
    _tag_bad = []
    for _w, _props in _tag_regs:
        for _tag, _opt_tok in _props.items():
            for _opt, _tok in _opt_tok.items():
                _want = m.COLORS.get(_tok)
                if not isinstance(_want, str):
                    continue
                try:
                    _got = str(_w.tag_cget(_tag, _opt)).lower()
                except Exception:
                    continue  # 控件/tag 已销毁：跳过（重放侧同策略）
                if _got != _want.lower():
                    _tag_bad.append((_tag, _opt, _got, _want.lower()))
    check(not _tag_bad,
          f'C15 tag 重放后取当前主题值（偏离: {_tag_bad[:5]}）')

    # C16：tag / canvas 注册表非空——evolution、cli 标签页的登记真实发生，
    # 否则 C15 退化为空转（假覆盖）。
    _cv_regs = list(getattr(gui, '_theme_canvas_items', []) or [])
    check(len(_tag_regs) > 0,
          f'C16a tag 注册表非空（{len(_tag_regs)} 项）')
    check(len(_cv_regs) > 0,
          f'C16b canvas 绘图项注册表非空（{len(_cv_regs)} 项）')

    # ---------------- D. 工厂纯函数契约 ----------------
    check(ui_styles.resolve_color_token('bg_card', m.COLORS) == m.COLORS['bg_card'],
          'D1 resolve_color_token 解析 token')
    check(ui_styles.resolve_color_token('#123456', m.COLORS) == '#123456',
          'D2 resolve_color_token 静态 hex 原样返回')
    # D3：反查的正确契约是「回解同值 + 优先语义槽」，而非「精确命中 bg_card」——
    # 浅色主题 bg_secondary 与 bg_card 同为 #ffffff，按值无法区分，断言精确键名属假契约。
    _d3_val = m.COLORS['bg_card']
    _d3_tok = ui_styles.token_for_color(_d3_val, m.COLORS)
    check(isinstance(_d3_tok, str) and _d3_tok in m.COLORS
          and m.COLORS[_d3_tok] == _d3_val,
          'D3 token_for_color 反查命中且回解同值')
    check(_d3_tok.startswith(('bg_', 'text_')),
          'D3b 反查优先命中背景/文字语义槽（不被徽章/阴影键抢占）')
    lighter = ui_styles.lighten_color('#1e293b', 1.2)
    check(lighter != '#1e293b' and lighter.startswith('#'), 'D4 lighten_color 变亮')
    check(ui_styles.lighten_color('bad', 1.2) == 'bad', 'D5 lighten_color 非法输入兜底')

    # 工厂 hover / 禁用守卫（新建测试按钮，不污染主界面）
    # 须以主题色显式创建——与真实按钮一致；否则 Tk 默认灰底会让「离开还原」
    # 与创建时配色无意义比较（假契约）。
    probe = tk.Button(root, text='probe', bg=m.COLORS['bg_card'],
                      fg=m.COLORS['text_primary'])
    ui_styles.remember_button_normal(probe, 'bg_card', 'text_primary')
    on_enter, on_leave = ui_styles.bind_hover(probe, m.COLORS)
    check(on_enter is not None and on_leave is not None, 'D6 bind_hover 返回可测回调')
    normal = str(probe.cget('bg'))
    on_enter()
    check(str(probe.cget('bg')) != normal, 'D7 hover 进入改变背景色')
    on_leave()
    check(str(probe.cget('bg')) == normal, 'D8 hover 离开还原背景色')
    probe.config(state='disabled')
    on_enter()
    check(str(probe.cget('bg')) == normal, 'D9 禁用态 hover 不改色（守卫）')

    ui_styles.set_button_state(probe, 'normal', m.COLORS)
    check(str(probe.cget('state')) == 'normal', 'D10 set_button_state 恢复启用')
    ui_styles.set_button_state(probe, 'disabled', m.COLORS)
    check(str(probe.cget('fg')) == m.COLORS['text_muted'], 'D11 set_button_state 禁用置灰')

    style = ui_styles.build_ttk_styles(root, m.COLORS)
    check(style is None or isinstance(style, ttk.Style), 'D12 build_ttk_styles 返回 Style 或 None')

    # ---------------- E. 进度反馈组件 ----------------
    check(hasattr(gui, 'status_var'), 'E1 存在状态栏变量 status_var')
    check(hasattr(gui, '_show_toast'), 'E2 存在提示方法 _show_toast（完成/异常提示）')
    check(any('开始分析' in str(b.cget('text')) for b in btns),
          'E3 存在「开始分析」按钮（端到端反馈入口）')
    check(hasattr(gui, 'task_mgr') and hasattr(gui.task_mgr, 'is_running'),
          'E4 存在任务管理器（长任务禁用态依据）')
    check(hasattr(gui, '_cancel_current_task'), 'E5 存在取消任务入口（Escape 绑定目标）')

    # ---------------- F. 统一进度文案模板（§2.1 第 3 项） ----------------
    check(hasattr(gui, '_update_progress_ui'), 'F1 存在统一进度渲染入口 _update_progress_ui')
    check(isinstance(getattr(gui, '_progress_est', None), ui_styles.ProgressEstimator),
          'F2 _progress_est 为 ProgressEstimator 实例')

    gui._current_task_name = '智能分析'
    gui._progress_est.reset()
    gui._last_progress_value = -1
    gui._last_progress_text = ''

    # F3：运行中模板 = 阶段名 · 百分比 · 预计剩余（阶段名优先于任务名，任务名仅终态呈现）
    gui._update_progress_ui(42, '融合计算中')
    _t3 = str(gui.task_status_label.cget('text'))
    check(_t3.count('·') >= 2 and '42%' in _t3 and '融合计算中' in _t3,
          'F3 运行中模板含阶段名+百分比+ETA')
    # F4：ETA 样本不足显示「计算中」，不编造数字
    check('计算中' in _t3 and '预计剩余' not in _t3,
          'F4 ETA 样本不足显示「计算中」（不编造数字）')
    # F5：进度条本体只显示百分比，与状态标签分工
    check(float(gui.progress.cget('value')) == 42.0
          and str(gui.progress_label.cget('text')) == '42%',
          'F5 进度条本体同步百分比（与状态标签分工）')

    # F6：同值同文案节流跳过（值低于 ETA 阈值→样本被忽略→文案稳定）
    # 证明方式：节流命中时不应触碰进度条/标签，故先破坏其值再调用，未被重写即证明跳过。
    gui._progress_est.reset()
    gui._update_progress_ui(3, '融合计算中')
    gui.progress['value'] = 77
    gui.progress_label.config(text='77%')
    gui._update_progress_ui(3, '融合计算中')
    check(float(gui.progress.cget('value')) == 77.0,
          'F6 同值同文案节流跳过（不重写进度条）')
    # F7：同值不同阶段名仍刷新（文本维度节流生效）
    _before7 = str(gui.task_status_label.cget('text'))
    gui._update_progress_ui(3, '特征计算中')
    _after7 = str(gui.task_status_label.cget('text'))
    check(_after7 != _before7 and '特征计算中' in _after7,
          'F7 同值不同阶段名仍刷新（文本维度节流）')

    # F8-F10：三类终态模板
    gui._update_progress_ui(100, '完成')
    _t8 = str(gui.task_status_label.cget('text'))
    check('已完成' in _t8 and '100%' in _t8 and '智能分析' in _t8,
          'F8 终态「已完成」模板')
    gui._update_progress_ui(0, '数据库连接失败')
    _t9 = str(gui.task_status_label.cget('text'))
    check('数据库连接失败' in _t9 and '智能分析' in _t9,
          'F9 终态失败原因模板（原因不被吞掉）')
    gui._update_progress_ui(35, '已取消')
    _t10 = str(gui.task_status_label.cget('text'))
    check('已取消' in _t10 and '35%' in _t10,
          'F10 终态「已取消」保留取消瞬间百分比')

    # F11-F14：TaskManager 派发的 TASK_CANCELLED / TASK_TIMEOUT 回调须存在并可恢复交互
    check(hasattr(gui, '_on_task_cancelled'), 'F11 存在 _on_task_cancelled 回调')
    check(hasattr(gui, '_on_task_timeout'), 'F12 存在 _on_task_timeout 回调')
    gui._task_finish_reason = 'timeout'
    gui._on_task_cancelled('probe')   # 模拟后台 TASK_CANCELLED 到达
    _t11 = str(gui.task_status_label.cget('text'))
    check('超时' in _t11 and gui.task_mgr.is_running() is False,
          'F13 超时取消后按「超时」呈现且任务管理器不再运行')
    gui._task_finish_reason = ''
    gui._on_task_timeout(task_id='probe', name='智能分析')
    _t12 = str(gui.task_status_label.cget('text'))
    check(gui._task_finish_reason == 'timeout' and '超时' in _t12,
          'F14 超时消息置位原因并统一恢复交互')

    # ---------------- G. 仪表盘响应速度与截断一致性（§2.1 第 4 项） ----------------
    # G1：候选组合截断契约——recommended_combinations 有 500 条时，聚合层每源只取 1 条，
    #     渲染代价与候选总数无关（O(1) ≤ O(N)）
    _huge = {'recommended_combinations': [
        {'combination': str(i % 10), 'reason': f'r{i}'} for i in range(500)]}
    _picks, _top5, _combos, _ti = gui._extract_source_data(_huge, None, None)
    check(set(_combos.keys()) <= {'pipeline', 'quick', 'trend'}
          and all(isinstance(v, str) and len(v) <= 5 for v in _combos.values()),
          'G1 聚合层每源仅取 1 条组合（与候选总数无关）')
    # G2：渲染入口支持复用预聚合结果，避免同一次渲染重复计算
    import inspect as _inspect
    _sig = _inspect.signature(gui._render_dashboard_impl)
    check('_pre_agg' in _sig.parameters,
          'G2 _render_dashboard_impl 接受 _pre_agg 复用参数（聚合只算一次）')
    # G3：单源兜底不得覆盖已写好的多源聚合剪贴板。
    #     让流水线产物能聚合出有效主推荐（走聚合路径），再把单源构建替换为哨兵串，
    #     若最终剪贴板等于哨兵串，说明兜底块做了无条件覆盖（回归到旧缺陷）。
    gui._last_pipeline_final = {
        'next_issue': '2026001',
        'trend_prediction': {
            'wan': {'numbers': [1, 2, 3, 4, 5]},
            'qian': {'numbers': [2, 3, 4, 5, 6]},
            'bai': {'numbers': [3, 4, 5, 6, 7]},
            'shi': {'numbers': [4, 5, 6, 7, 8]},
            'ge': {'numbers': [5, 6, 7, 8, 9]},
        },
        'recommended_combinations': [{'combination': '12345', 'reason': 'x'}],
    }
    gui._last_trend_result = None
    gui._last_quick_final = None
    _SENTINEL = '【SINGLE_SOURCE_SENTINEL】'
    _orig_build = gui._build_prediction_clipboard
    gui._build_prediction_clipboard = lambda pf: _SENTINEL
    try:
        gui._render_unified_dashboard(None)
    finally:
        gui._build_prediction_clipboard = _orig_build
    check(str(gui._prediction_clipboard) != _SENTINEL
          and '12345' in str(gui._prediction_clipboard),
          'G3 单源兜底不覆盖已生成的多源聚合剪贴板')

    root.destroy()


if __name__ == '__main__':
    _backup_theme_pref()  # 离屏测试不得污染用户真实主题偏好
    try:
        run()
    finally:
        _restore_theme_pref()
    total = len(_PASSED)
    print(f'\n=== verify_ui_offscreen: {total} 项 PASS ===')
    if total < 68:
        print(f'（目标 68 项，当前 {total} 项）')
    raise SystemExit(0)
