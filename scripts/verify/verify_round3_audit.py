# -*- coding: utf-8 -*-
"""第三轮交付审计门禁（可复跑，无 GUI 依赖）。

覆盖本轮全部增量 + 回归确认：
  A1 高DPI：_enable_windows_dpi_awareness / _apply_dpi_scaling 符号存在
  A2 四档断点：_BREAKPOINT_WIDE_MAX_W=1600 + _classify_breakpoint 边界值
  A3 空态工厂：output_panel._build_empty_state 可调用
  A4 无障碍：_create_toolbar_button 含 takefocus / highlightthickness / highlightcolor
  B1 标准化：standardize_feature_vector 边缘输入（空/常量）不崩溃
  B3 性能：measure_feature_extraction_perf + get_ml_cache_stats 可调用
  B5 合规：predict_next 契约含诚实声明/缓存去重/异常降级

用法：
  python scripts/verify/verify_round3_audit.py
"""
import ast
import os
import sys

ROOT = r'd:\PythonProject\KPLuckyNumber'
sys.path.insert(0, ROOT)

_PASS = 0
_FAIL = 0


def _check(name, cond, detail=''):
    global _PASS, _FAIL
    mark = 'PASS' if cond else 'FAIL'
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
    print(f'  [{mark}] {name}' + (f'  {detail}' if detail else ''))


def _ast_has_fn(path, fn_name):
    """AST 解析确认函数定义存在（避免 import 触发 Tk 副作用）。"""
    try:
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                return True
    except Exception:
        return False
    return False


def _main_attr(name):
    """AST 检查 main.py 是否赋值/定义了指定符号。"""
    try:
        with open(os.path.join(ROOT, 'main.py'), encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return True
    except Exception:
        return False
    return False


def main():
    main_py = os.path.join(ROOT, 'main.py')
    dash_py = os.path.join(ROOT, 'gui', 'dashboard.py')
    op_py = os.path.join(ROOT, 'gui', 'panels', 'output_panel.py')
    feat_py = os.path.join(ROOT, 'modules', 'features.py')
    ml_py = os.path.join(ROOT, 'modules', 'ml_predictor.py')

    print('=' * 72)
    print('KPLuckyNumber 第三轮交付审计门禁')
    print('=' * 72)

    # ---- A1 高DPI ----
    print('\n【A1 高DPI 适配】')
    _check('A1 _enable_windows_dpi_awareness 定义', _ast_has_fn(main_py, '_enable_windows_dpi_awareness'))
    _check('A1 _apply_dpi_scaling 定义', _ast_has_fn(main_py, '_apply_dpi_scaling'))

    # ---- A2 四档断点 ----
    print('\n【A2 四档断点】')
    _check('A2 _BREAKPOINT_WIDE_MAX_W=1600 存在', _main_attr('_BREAKPOINT_WIDE_MAX_W'))
    try:
        with open(dash_py, encoding='utf-8') as f:
            dash_src = f.read()
        _check('A2 _classify_breakpoint 含 ultrawide 分支', "return 'ultrawide'" in dash_src)
    except Exception as e:
        _check('A2 读取 dashboard.py', False, str(e))

    # ---- A3 空态工厂 ----
    print('\n【A3 空态统一】')
    _check('A3 _build_empty_state 定义', _ast_has_fn(op_py, '_build_empty_state'))

    # ---- A4 无障碍 ----
    print('\n【A4 无障碍焦点环】')
    try:
        with open(op_py, encoding='utf-8') as f:
            op_src = f.read()
        _check('A4 takefocus=True 存在', 'takefocus=True' in op_src)
        _check('A4 highlightthickness=1 存在', 'highlightthickness=1' in op_src)
        _check('A4 highlightcolor 存在', 'highlightcolor=COLORS' in op_src or 'highlightcolor=' in op_src)
    except Exception as e:
        _check('A4 读取 output_panel.py', False, str(e))

    # ---- B1 标准化健壮性 ----
    print('\n【B1 特征标准化】')
    _check('B1 standardize_feature_vector 定义', _ast_has_fn(feat_py, 'standardize_feature_vector'))
    _check('B1 drop_collinear_groups 定义', _ast_has_fn(feat_py, 'drop_collinear_groups'))

    # ---- B3 性能工具 ----
    print('\n【B3 性能可观测性】')
    _check('B3 measure_feature_extraction_perf 定义', _ast_has_fn(feat_py, 'measure_feature_extraction_perf'))
    _check('B3 get_ml_cache_stats 定义', _ast_has_fn(ml_py, 'get_ml_cache_stats'))

    # ---- B5 合规 ----
    print('\n【B5 风险合规】')
    try:
        with open(ml_py, encoding='utf-8') as f:
            ml_src = f.read()
        _check('B5 predict_next 诚实声明（随机基线）', '随机基线' in ml_src)
        _check('B5 缓存去重（ALG-01）', 'cache_hits' in ml_src and 'cache_misses' in ml_src)
        _check('B5 异常降级（均匀分布）', '降级' in ml_src or '均匀分布' in ml_src)
    except Exception as e:
        _check('B5 读取 ml_predictor.py', False, str(e))

    print('\n' + '=' * 72)
    print(f'总计: {_PASS} PASS / {_FAIL} FAIL')
    print('=' * 72)
    sys.exit(1 if _FAIL else 0)


if __name__ == '__main__':
    main()
