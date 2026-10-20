#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 绑定审计脚本（阶段二 M2 + v5.2 GUI-06）。
扫描 main.py 中所有 tkinter 绑定（command=、Menu.add_command command=、bind）
并判定回调是否指向 LotteryGUI 类中真实存在的方法。
输出 docs/ui_binding_map.json 并提供 --check 校验退出码。

GUI-06 主题刷新覆盖审计（v5.2 §3.1）：枚举 main.py 全部颜色设置点并按
静态可判定性分类（theme-token / static 白名单 / variable 委托 / derived
派生 / gap），另做 AST 全量 hex 字面量兜底消账，输出
theme_refresh_coverage 覆盖率；未被白名单登记的 hex 一律计 gap，
门禁要求清零——保证 100% 覆盖率不是掺水数字。

用法:
    python scripts/audit_ui_bindings.py          # 绑定映射 + 主题审计报告
    python scripts/audit_ui_bindings.py --theme  # 仅跑主题刷新覆盖审计
    python scripts/audit_ui_bindings.py --check  # 绑定 + 主题双门禁（0/1）
"""

import ast
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Set

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MAIN_PY = os.path.join(PROJECT_ROOT, "main.py")
OUTPUT_JSON = os.path.join(PROJECT_ROOT, "docs", "ui_binding_map.json")


@dataclass
class BindingIssue:
    line: int
    kind: str                      # "command" | "menu_command" | "bind"
    target: str                    # 解析到的目标名（如 "_toggle_theme"）或特殊标记
    status: str                    # OK/MISSING/LAMBDA/VARIABLE/UNRESOLVED
    context: str                   # 原始源码行（截断）
    resolved: bool                 # 是否视为已解决（OK/LAMBDA/VARIABLE 视为已解决）


def _extract_source_lines() -> List[str]:
    with open(MAIN_PY, "r", encoding="utf-8") as f:
        return f.readlines()


def _get_lottery_gui_methods(tree: ast.AST) -> Set[str]:
    """返回 LotteryGUI 类中定义的所有方法名（包括以 _ 开头的私有方法）。"""
    methods: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LotteryGUI":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(item.name)
            break
    return methods


def _is_partial_call(call: ast.Call) -> bool:
    """判断调用是否为 functools.partial(...) 形式（可产出可调用对象）。"""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "partial":
        return True
    return isinstance(func, ast.Name) and func.id == "partial"


def _method_is_closure_factory(item: ast.FunctionDef) -> bool:
    """判定方法是否为「闭包工厂」：体内定义嵌套可调用（lambda/def），
    且 return 语句产出可调用对象（lambda/嵌套 def 本体/functools.partial
    调用/指向该嵌套函数名的 Name）。

    该判定是纯 AST 可验证的准入条件——用于把
    ``command=self._make_xxx()`` 这类「工厂调用产出回调」的写法从
    UNRESOLVED 升级为 OK，而非对具体行号做无理由白名单。
    """
    nested_defs = {
        sub.name
        for sub in ast.walk(item)
        if isinstance(sub, ast.FunctionDef) and sub is not item
    }
    has_lambda = any(
        isinstance(sub, ast.Lambda) for sub in ast.walk(item)
    )
    if not (nested_defs or has_lambda):
        return False
    for sub in ast.walk(item):
        if isinstance(sub, ast.Return) and sub.value is not None:
            val = sub.value
            if isinstance(val, (ast.Lambda, ast.FunctionDef)):
                return True
            if isinstance(val, ast.Name) and val.id in nested_defs:
                return True
            if isinstance(val, ast.Call) and _is_partial_call(val):
                return True
    return False


def _get_closure_factory_methods(tree: ast.AST) -> Set[str]:
    """返回 LotteryGUI 类中全部闭包工厂方法名（_method_is_closure_factory）。"""
    factories: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LotteryGUI":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    if _method_is_closure_factory(item):
                        factories.add(item.name)
            break
    return factories


# 已知的 Tkinter 小部件通用方法（假定存在）
_SAFE_WIDGET_METHODS = {"yview", "xview", "destroy"}


def _resolve_callback_value(
    node: ast.AST,
    methods: Set[str],
    factories: Optional[Set[str]] = None,
) -> tuple[str, str, bool]:
    """
    尝试将回调节点解析为一个方法名。
    返回 (target, status, resolved) 三元组。
    - target: 例如 "_toggle_theme" 或 "<lambda>" 或 "<variable:name>" 等
    - status: "OK" | "MISSING" | "LAMBDA" | "VARIABLE" | "UNRESOLVED"
    - resolved: True 如果 status 在 {OK, LAMBDA, VARIABLE} 否则 False
    """
    # 1. 直接属性访问 self.xxx
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        meth_name = node.attr
        if meth_name in methods:
            return meth_name, "OK", True
        # 即使不在 LotteryGUI 方法中，若是安全的小部件方法也视为已解决
        if meth_name in _SAFE_WIDGET_METHODS:
            return meth_name, "OK", True
        return meth_name, "MISSING", False

    # 2. 其他属性访问（如 widget.yview、win.destroy 等）：
    #    只要属性名在安全列表中，无论基是什么（假定是有效小部件），即视为已解决。
    if isinstance(node, ast.Attribute) and node.attr in _SAFE_WIDGET_METHODS:
        return node.attr, "OK", True

    # 3. lambda 表达式：视为已定义内联，算作已解决
    if isinstance(node, ast.Lambda):
        return "<lambda>", "LAMBDA", True

    # 4. 变量（可能是参数传入的回调函数）：无法静态知晓，但视为已解决（避免误报）
    if isinstance(node, ast.Name):
        return f"<variable:{node.id}>", "VARIABLE", True

    # 5. 闭包工厂调用 self.<factory>(...)：工厂方法体已通过
    #    _method_is_closure_factory 验证 return 出可调用对象，其调用
    #    结果即为有效回调（GUI-03 _make_alt_toggle 模式）——准入条件
    #    AST 可验证，不是行号白名单。
    if (
        factories
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr in methods
        and node.func.attr in factories
    ):
        return node.func.attr, "OK", True

    # 6. 其他复杂表达式（如函数调用、三元等）：暂时标记为 UNRESOLVED，需要人工复查
    return "<complex>", "UNRESOLVED", False


def _collect_bindings(
    tree: ast.AST,
    source_lines: List[str],
) -> List[BindingIssue]:
    issues: List[BindingIssue] = []
    methods = _get_lottery_gui_methods(tree)
    # 闭包工厂集合：self.<factory>() 调用产出回调的写法据此升级为 OK
    factories = _get_closure_factory_methods(tree)

    # 辅助：获取某行的源码（截断到 200 字符）
    def line_context(lineno: int) -> str:
        if 1 <= lineno <= len(source_lines):
            return source_lines[lineno - 1].rstrip("\n")
        return ""

    # 遍历查找 command 关键字
    for node in ast.walk(tree):
        # 命令按钮的 command=
        if isinstance(node, ast.keyword) and node.arg == "command":
            target, status, resolved = _resolve_callback_value(
                node.value, methods, factories)
            issues.append(
                BindingIssue(
                    line=getattr(node.value, "lineno", 0),
                    kind="command",
                    target=target,
                    status=status,
                    context=line_context(getattr(node.value, "lineno", 0)),
                    resolved=resolved,
                )
            )
        # Menu.add_command 的 command=
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_command":
                for kw in node.keywords:
                    if kw.arg == "command":
                        target, status, resolved = _resolve_callback_value(
                            kw.value, methods, factories)
                        issues.append(
                            BindingIssue(
                                line=getattr(kw.value, "lineno", 0),
                                kind="menu_command",
                                target=target,
                                status=status,
                                context=line_context(
                                    getattr(kw.value, "lineno", 0)),
                                resolved=resolved,
                            )
                        )
        # bind(..., callback)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "bind" and len(node.args) >= 2:
                callback = node.args[1]
                target, status, resolved = _resolve_callback_value(
                    callback, methods, factories)
                issues.append(
                    BindingIssue(
                        line=getattr(callback, "lineno", 0),
                        kind="bind",
                        target=target,
                        status=status,
                        context=line_context(getattr(callback, "lineno", 0)),
                        resolved=resolved,
                    )
                )

    return issues


def _write_mapping(issues: List[BindingIssue]) -> None:
    data = [asdict(issue) for issue in issues]
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 绑定映射已写入: {OUTPUT_JSON} ({len(data)} 条)")


def _check_all_resolved(issues: List[BindingIssue]) -> bool:
    return all(issue.resolved for issue in issues)


# ═══════════════════════════════════════════════════════════════════
# GUI-06 主题刷新覆盖审计（v5.2 §3.1）
# ═══════════════════════════════════════════════════════════════════
#
# 审计问题：main.py 中每一处颜色设置，主题切换后能否被 _apply_theme 的
# 「旧主题色值 → 语义槽 → 新主题值」反查机制正确接管？
#
# 颜色设置点枚举（静态可判定）：
#   1) 任意调用的颜色 kwargs（tk 构造器 / Canvas 绘图 / config /
#      tag_config / itemconfig / matplotlib 颜色参数）
#   2) props 载荷字典中键为颜色名的项
#
# 值分类：
#   theme-token  COLORS['x'] / COLORS.get('x'[,...]) / theme.get /
#                resolve_color_token('x') → 机制自动接管（计 covered）
#   static       hex 命中 STATIC_HEX_WHITELIST（附理由的合法静态色）
#   variable     bg=style['bg'] 等变量委托 → 单列统计，不掺水
#   derived      lighten_color(...) 等函数派生 → 单列统计，待人工复核
#   gap          未登记 hex 字面量（颜色入口或兜底扫描命中）→ 门禁清零
#
# 兜底扫描：AST 全量 hex 字面量（ThemeManager 主题定义区免检）逐一消账，
# 未被颜色入口枚举消耗的字面量也须落在白名单内，否则计 gap——
# 保证 theme_refresh_coverage=100% 不是掺水数字。

# tkinter / matplotlib 颜色类参数名（调用 kwargs 与 props 字典键共用）
_COLOR_KWARGS: Set[str] = frozenset({
    'bg', 'fg', 'background', 'foreground',
    'activebackground', 'activeforeground',
    'disabledbackground', 'disabledforeground',
    'insertbackground', 'highlightbackground', 'highlightcolor',
    'selectbackground', 'selectforeground', 'selectcolor',
    'troughcolor', 'color', 'fill', 'outline',
    'activefill', 'disabledfill', 'activeoutline', 'disabledoutline',
    'ecolor', 'facecolor', 'edgecolor',
})

# 主题色字典变量名（COLORS 全局 / _apply_theme 的 theme 参数）
_THEME_DICT_NAMES: Set[str] = frozenset({'COLORS', 'theme'})

# 读取当前主题、返回主题色的工具函数（调用点即主题敏感）
_THEME_TOKEN_FUNCS: Set[str] = frozenset({
    'resolve_color_token', 'default_hover_bg',
})

# 合法静态 hex 白名单：值 → 中文理由。登记依据为「双主题实值交叉比对」：
# 仅双主题同值、或双主题下一致重绘（非控件切换残留面）的语义静态色
# 方可登记；任何随主题漂移的字面量一律不得入表（必须改查表）。
STATIC_HEX_WHITELIST: Dict[str, str] = {
    # accent 底白字族：两主题 accent 槽均为饱和深色，白字对比度一致
    '#ffffff': 'accent 底白字：两主题 accent 槽均为饱和深色，对比度一致',
    '#fff': '同 #ffffff（三位缩写）：accent 底白字',
    # 命中语义静态色（预测详情行）：提示语义色，双主题表现一致
    '#2e7d32': '首选命中绿：命中语义静态提示色，双主题一致',
    '#e65100': 'Top-3 含开奖橙：命中语义静态提示色，双主题一致',
    '#b71c1c': '未含红：命中语义静态提示色，双主题一致',
    '#c8e6c9': '命中格浅绿底：命中高亮静态语义色，双主题一致',
    '#1b5e20': '命中格深绿字：命中高亮静态语义色，双主题一致',
    # 共识矩阵高亮：固定语义色，双主题一致
    '#d1fae5': '共识一致浅绿字：共识高亮静态语义色，双主题一致',
    '#065f46': '共识一致深绿底：共识高亮静态语义色，双主题一致',
    '#e0e7ff': 'accent_p5 底浅紫白字：静态高对比前景，双主题一致',
    # matplotlib 图表静态色：图表按数据整体重绘，非控件切换残留面
    '#059669': '图表柱色（accent_p5 同值）：整体重绘，无控件残留',
    '#10b981': '图表柱色（accent_p5_light 同值）：整体重绘，无残留',
    '#34d399': '图表柱色浅绿（accent_p5 系同族）：整体重绘，无残留',
    '#6ee7b7': '图表柱色浅绿：静态图表配色，双主题一致',
    '#e53e3e': '图表系统实测线红：静态图表配色，双主题一致',
    '#9b2c2c': '图表系统实测误差线暗红：静态图表配色，双主题一致',
    '#3182ce': '图表随机基线蓝：静态图表配色，双主题一致',
    '#2c5282': '图表随机基线误差线暗蓝：静态图表配色，双主题一致',
    '#4a5568': '图表注释文字灰：静态图表配色，双主题一致',
    '#f7fafc': '图表注释框底色：静态图表配色，双主题一致',
    '#cbd5e0': '图表注释框边色：静态图表配色，双主题一致',
}

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{3,8}$')

# 计入覆盖率的分类 / 计为缺口（门禁清零）的分类
_COVERED_CATEGORIES: Set[str] = frozenset({
    'theme-token', 'static', 'static-unlisted',
})
_GAP_CATEGORIES: Set[str] = frozenset({
    'gap', 'gap-unlisted', 'other',
})

THEME_AUDIT_JSON = os.path.join(
    PROJECT_ROOT, "docs", "ui_theme_refresh_audit.json")


@dataclass
class ColorEntry:
    line: int
    site: str             # 颜色设置点（kwarg:bg / dict:fg / <unlisted>）
    value: str            # 源码片段（截断）
    category: str         # theme-token/static/static-unlisted/variable/
    #                     derived/gap/gap-unlisted/other
    token: str = ''       # theme-token 解析到的语义槽键
    reason: str = ''      # static 白名单理由
    suggestion: str = ''  # gap 修复建议
    drift: bool = False   # gap 时该色双主题是否漂移


def _extract_theme_dicts(tree: ast.AST) -> tuple:
    """从 ThemeManager 类提取 DARK_THEME / LIGHT_THEME 字面量字典。

    用于 gap 修复建议（色值 → 语义槽键反查）与双主题漂移判定；
    解析失败时返回空字典（降级为「无建议」，不影响审计计数）。
    """
    themes: Dict[str, dict] = {'DARK_THEME': {}, 'LIGHT_THEME': {}}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef)
                and node.name == 'ThemeManager'):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for tgt in item.targets:
                if (isinstance(tgt, ast.Name) and tgt.id in themes
                        and isinstance(item.value, ast.Dict)):
                    try:
                        themes[tgt.id] = ast.literal_eval(item.value)
                    except (ValueError, SyntaxError, TypeError):
                        pass
    return themes['DARK_THEME'], themes['LIGHT_THEME']


def _snippet(source_lines: List[str], node: ast.AST,
             limit: int = 64) -> str:
    """取节点所在源码行作为值片段（截断到 limit 字符）。"""
    lineno = getattr(node, 'lineno', 0)
    text = ''
    if 1 <= lineno <= len(source_lines):
        text = source_lines[lineno - 1].strip()
    return text if len(text) <= limit else text[:limit] + '…'


def _suggest_fix(value: str, dark: dict, light: dict) -> tuple:
    """按双主题实值为 gap 值生成修复建议，并判定是否双主题漂移。

    返回 (suggestion, drift)：
    - 双主题同键同值      → 建议改查表（token 化后仍无视觉变化）
    - 仅单主题命中        → 建议改查表并标注 light/dark 两侧实值（漂移）
    - 两主题均无          → 建议要么登记白名单附理由，要么改查表
    """
    dark_keys = [k for k, v in dark.items() if v == value]
    light_keys = [k for k, v in light.items() if v == value]
    stable = [k for k in dark_keys if k in light_keys]
    if stable:
        key = stable[0]
        return ("改查表 COLORS['%s']（双主题同值 %s）"
                "或登记白名单并补理由" % (key, value), False)
    if dark_keys:
        key = dark_keys[0]
        return ("改查表 COLORS['%s']（dark=%s / light=%s，"
                "token 化后自动跟随主题）"
                % (key, dark[key], light.get(key, '?')), True)
    if light_keys:
        key = light_keys[0]
        return ("改查表 COLORS['%s']（light=%s / dark=%s）"
                % (key, light[key], dark.get(key, '?')), True)
    return ("非主题色：合法静态语义色请加入 STATIC_HEX_WHITELIST "
            "并附理由，否则改查表", False)


def _classify_hex(value: str, line: int, site: str, snippet: str,
                  dark: dict, light: dict) -> ColorEntry:
    """分类一个十六进制字面量：白名单内 → static，否则 → gap。"""
    reason = STATIC_HEX_WHITELIST.get(value.lower())
    if reason:
        return ColorEntry(line, site, snippet, 'static', reason=reason)
    suggestion, drift = _suggest_fix(value, dark, light)
    return ColorEntry(line, site, snippet, 'gap',
                      suggestion=suggestion, drift=drift)


def _classify_into(node: ast.AST, site: str, branch: str,
                   entries: List[ColorEntry], consumed: Set[tuple],
                   dark: dict, light: dict,
                   source_lines: List[str]) -> None:
    """把颜色值节点分类为 ColorEntry 追加进 entries。

    IfExp / List / Tuple 递归展开（分支标注 (if)/(else)）；
    遇到的 hex 字面量位置登记进 consumed（避免兜底扫描重复计账）。
    """
    label = site + branch
    # 常量：hex 走白名单判定，其余常量（如 'selected'）不计账
    if isinstance(node, ast.Constant):
        if (isinstance(node.value, str)
                and _HEX_RE.match(node.value)):
            consumed.add((node.lineno, node.col_offset))
            entries.append(_classify_hex(
                node.value, node.lineno, label,
                _snippet(source_lines, node), dark, light))
        return
    # 条件表达式：两个分支分别分类
    if isinstance(node, ast.IfExp):
        _classify_into(node.body, site, branch + '(if)',
                       entries, consumed, dark, light, source_lines)
        _classify_into(node.orelse, site, branch + '(else)',
                       entries, consumed, dark, light, source_lines)
        return
    # 列表/元组（如 color=[...] / foreground=[('selected', '#fff')]）
    if isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            _classify_into(elt, site, branch,
                           entries, consumed, dark, light, source_lines)
        return
    # 主题字典下标：theme-token（机制自动接管）
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Name) and base.id in _THEME_DICT_NAMES:
            token = ''
            sl = node.slice
            if isinstance(sl, ast.Index):  # Python 3.8 兼容
                sl = sl.value
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                token = sl.value
            entries.append(ColorEntry(
                node.lineno, label, _snippet(source_lines, node),
                'theme-token', token=token))
            return
        entries.append(ColorEntry(
            node.lineno, label, _snippet(source_lines, node), 'variable'))
        return
    # 变量 / 属性委托：单列统计不掺水
    if isinstance(node, (ast.Name, ast.Attribute)):
        entries.append(ColorEntry(
            node.lineno, label, _snippet(source_lines, node), 'variable'))
        return
    # 调用：主题工具 → theme-token；COLORS.get 兜底默认值单独分类；
    # 其余函数派生 → derived（嵌套 hex 由兜底扫描消账）
    if isinstance(node, ast.Call):
        func = node.func
        fname = ''
        if isinstance(func, ast.Name):
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = func.attr
        if fname in _THEME_TOKEN_FUNCS:
            token = ''
            if (node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and not node.args[0].value.startswith('#')):
                token = node.args[0].value
            entries.append(ColorEntry(
                node.lineno, label, _snippet(source_lines, node),
                'theme-token', token=token))
            return
        is_theme_get = (
            fname == 'get' and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in _THEME_DICT_NAMES
        )
        if is_theme_get:
            token = ''
            if (node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                token = node.args[0].value
            entries.append(ColorEntry(
                node.lineno, label, _snippet(source_lines, node),
                'theme-token', token=token))
            default_node = None
            if len(node.args) > 1:
                default_node = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == 'default':
                        default_node = kw.value
            if default_node is not None:
                _classify_into(default_node, site, branch + '(default)',
                               entries, consumed, dark, light, source_lines)
            return
        entries.append(ColorEntry(
            node.lineno, label, _snippet(source_lines, node), 'derived'))
        return
    # 其他表达式（BinOp 等）：不可静态判定，计缺口迫使人工复核
    entries.append(ColorEntry(
        node.lineno, label, _snippet(source_lines, node), 'other'))


def _iter_color_sites(tree: ast.AST):
    """枚举 main.py 全部颜色设置点，产出 (site 标签, 值节点)。

    覆盖：任意调用的颜色 kwargs + props 载荷字典的颜色键值项。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _COLOR_KWARGS:
                    yield 'kwarg:%s' % kw.arg, kw.value
        elif isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value in _COLOR_KWARGS):
                    yield 'dict:%s' % key.value, val


def _classify_unlisted(node: ast.Constant, dark: dict, light: dict,
                       source_lines: List[str]) -> ColorEntry:
    """兜底扫描命中（未被颜色入口枚举消耗）的 hex 字面量分类。

    位置参数传色（如 _create_card(parent, title, '#059669')）、元组
    载荷（如 ('accent_p5', '#ffffff')）、普通赋值的 Hex 都会走到这里：
    白名单内 → static-unlisted（covered），否则 → gap-unlisted。
    """
    value = node.value
    reason = STATIC_HEX_WHITELIST.get(value.lower())
    snippet = _snippet(source_lines, node)
    if reason:
        return ColorEntry(node.lineno, '<unlisted>', snippet,
                          'static-unlisted', reason=reason)
    suggestion, drift = _suggest_fix(value, dark, light)
    return ColorEntry(node.lineno, '<unlisted>', snippet,
                      'gap-unlisted', suggestion=suggestion, drift=drift)


def audit_theme_refresh_coverage(tree: ast.AST,
                                 source_lines: List[str]) -> Dict[str, Any]:
    """GUI-06 主题刷新覆盖审计主入口。

    返回含 theme_refresh_coverage（覆盖率百分比）、分类计数、gap 明细
    与全量 entries 的报告 dict；JSON 可序列化。
    """
    dark, light = _extract_theme_dicts(tree)
    consumed: Set[tuple] = set()
    entries: List[ColorEntry] = []

    # ThemeManager 主题定义区：色值即语义槽来源，免检
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'ThemeManager':
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)
                        and _HEX_RE.match(sub.value)):
                    consumed.add((sub.lineno, sub.col_offset))

    # 1) 颜色设置点枚举与分类
    for site, value_node in _iter_color_sites(tree):
        _classify_into(value_node, site, '', entries, consumed,
                       dark, light, source_lines)

    # 2) 兜底：全量 hex 字面量消账（未被入口消耗的必须落白名单）
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _HEX_RE.match(node.value)):
            if (node.lineno, node.col_offset) in consumed:
                continue
            entries.append(
                _classify_unlisted(node, dark, light, source_lines))

    # 3) 汇总：variable/derived 单列不掺水，不进覆盖率分母
    by_cat: Dict[str, int] = {}
    for e in entries:
        by_cat[e.category] = by_cat.get(e.category, 0) + 1
    covered = sum(n for c, n in by_cat.items() if c in _COVERED_CATEGORIES)
    gap = sum(n for c, n in by_cat.items() if c in _GAP_CATEGORIES)
    scored = covered + gap
    coverage = round(covered * 100.0 / scored, 2) if scored else 100.0

    return {
        'generated_by': 'scripts/audit_ui_bindings.py (GUI-06)',
        'target': 'main.py',
        'theme_refresh_coverage': coverage,
        'summary': {
            'total_scored': scored,
            'covered': covered,
            'gap': gap,
            'variable': by_cat.get('variable', 0),
            'derived': by_cat.get('derived', 0),
            'by_category': by_cat,
            'whitelist_size': len(STATIC_HEX_WHITELIST),
        },
        'gaps': [_entry_to_dict(e) for e in entries
                 if e.category in _GAP_CATEGORIES],
        'entries': [_entry_to_dict(e) for e in entries],
    }


def _entry_to_dict(entry: ColorEntry) -> Dict[str, Any]:
    """ColorEntry → JSON 可序列化 dict。"""
    return {
        'line': entry.line,
        'site': entry.site,
        'value': entry.value,
        'category': entry.category,
        'token': entry.token,
        'reason': entry.reason,
        'suggestion': entry.suggestion,
        'drift': entry.drift,
    }


def _write_theme_audit(report: Dict[str, Any]) -> None:
    """主题刷新覆盖审计报告落盘 docs/ui_theme_refresh_audit.json。"""
    os.makedirs(os.path.dirname(THEME_AUDIT_JSON), exist_ok=True)
    with open(THEME_AUDIT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    s = report['summary']
    print(f"✅ 主题刷新审计已写入: {THEME_AUDIT_JSON} "
          f"(coverage={report['theme_refresh_coverage']}%, "
          f"gap={s['gap']})")


def _print_theme_report(report: Dict[str, Any], verbose: bool) -> None:
    """控制台打印主题刷新覆盖审计摘要（verbose 时列 gap 明细）。"""
    s = report['summary']
    mark = '✅' if s['gap'] == 0 else '❌'
    print(f"🎨 主题刷新覆盖审计（GUI-06）: "
          f"theme_refresh_coverage={report['theme_refresh_coverage']}% "
          f"(scored={s['total_scored']}, covered={s['covered']}, "
          f"gap={s['gap']}, variable={s['variable']}, "
          f"derived={s['derived']}, 白名单={s['whitelist_size']}) {mark}")
    if s['gap'] == 0 or not verbose:
        return
    print(f"   缺口 {s['gap']} 处（完整修复指引见 "
          f"docs/ui_theme_refresh_audit.json）：")
    for g in report['gaps'][:40]:
        drift = '  [双主题漂移]' if g['drift'] else ''
        print(f"   L{g['line']} {g['site']} {g['value']}{drift}")
        if g['suggestion']:
            print(f"      → {g['suggestion']}")
    if len(report['gaps']) > 40:
        print(f"   …另有 {len(report['gaps']) - 40} 处见 JSON 报告")


def main() -> None:
    if not os.path.isfile(MAIN_PY):
        sys.exit(f"❌ 未找到主文件: {MAIN_PY}")

    with open(MAIN_PY, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=MAIN_PY)

    source_lines = _extract_source_lines()

    # GUI-06 主题刷新覆盖审计（每次运行都执行，报告落盘 docs/）
    theme_report = audit_theme_refresh_coverage(tree, source_lines)
    theme_ok = theme_report['summary']['gap'] == 0

    # --theme：仅跑主题刷新覆盖审计
    if "--theme" in sys.argv:
        _print_theme_report(theme_report, verbose=True)
        _write_theme_audit(theme_report)
        sys.exit(0 if theme_ok else 1)

    issues = _collect_bindings(tree, source_lines)

    # --check：绑定 + 主题双门禁
    if "--check" in sys.argv:
        ok = True
        if not _check_all_resolved(issues):
            ok = False
            missing = [i for i in issues if not i.resolved]
            print(f"❌ 发现 {len(missing)} 条无效绑定：")
            for issue in missing:
                print(f"  第{issue.line}行 [{issue.kind}] "
                      f"target={issue.target} status={issue.status}")
                print(f"      {issue.context}")
        if not theme_ok:
            ok = False
        _print_theme_report(theme_report, verbose=not theme_ok)
        _write_theme_audit(theme_report)
        if ok:
            print("✅ 所有 UI 绑定均有效；主题刷新覆盖无缺口"
                  f"（coverage="
                  f"{theme_report['theme_refresh_coverage']}%）。")
            sys.exit(0)
        sys.exit(1)

    # 默认模式：绑定映射 + 主题审计报告
    _write_mapping(issues)
    _print_theme_report(theme_report, verbose=False)
    _write_theme_audit(theme_report)


if __name__ == "__main__":
    main()
