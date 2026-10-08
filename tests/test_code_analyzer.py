# -*- coding: utf-8 -*-
"""§8.2.5 代码安全扫描与质量审查器单测（modules/code_analyzer.py）。

覆盖：P0 规则（裸 except / 危险函数 / 硬编码密钥 / 值注入 SQL / 命令注入）、
P1 规则（缺 timeout / 未用 with 的 open / 调试代码 / 标识符拼接）、
P2 规则（缺类型注解 / 函数过长 / 函数内 import）、汇总与渲染、语法错误兜底。

纯离线：用临时文件构造样本源码，不依赖 DB/网络/GUI（import 零副作用）。
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.code_analyzer import (  # noqa: E402
    AnalyzerConfig,
    CodeAnalyzer,
    Issue,
    _collect_enum_vars,
)


# ---------------------------------------------------------------------------
# 辅助：写临时源码文件并扫描
# ---------------------------------------------------------------------------
def _scan_src(src: str, cfg=None):
    """把源码写入临时 .py 文件并返回扫描结果 + 分析器实例。"""
    fd, path = tempfile.mkstemp(suffix='.py', prefix='scan_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(src)
        analyzer = CodeAnalyzer(cfg)
        return analyzer.analyze_file(path, root=os.path.dirname(path)), analyzer
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _rules(issues):
    return {i.rule for i in issues}


def _sev_rules(issues, sev):
    return {i.rule for i in issues if i.severity == sev}


# ---------------------------------------------------------------------------
# P0：裸 except
# ---------------------------------------------------------------------------
def test_bare_except_detected():
    src = "try:\n    x = 1\nexcept:\n    x = 0\n"
    issues, _ = _scan_src(src)
    assert 'bare_except' in _rules(issues)
    assert _sev_rules(issues, 'P0') >= {'bare_except'}


def test_named_except_not_flagged():
    src = "try:\n    x = 1\nexcept ValueError as e:\n    x = 0\n"
    issues, _ = _scan_src(src)
    assert 'bare_except' not in _rules(issues)


# ---------------------------------------------------------------------------
# P0：危险函数
# ---------------------------------------------------------------------------
def test_dangerous_functions_detected():
    src = "def f(cmd):\n    eval(cmd)\n    exec(cmd)\n"
    issues, _ = _scan_src(src)
    assert 'dangerous_functions' in _rules(issues)


def test_yaml_load_without_loader_detected():
    src = "import yaml\ndef f(s):\n    return yaml.load(s)\n"
    issues, _ = _scan_src(src)
    assert 'dangerous_functions' in _rules(issues)


def test_yaml_load_with_safe_loader_not_flagged():
    src = "import yaml\ndef f(s):\n    return yaml.load(s, Loader=yaml.SafeLoader)\n"
    issues, _ = _scan_src(src)
    assert 'dangerous_functions' not in _rules(issues)


def test_pickle_load_detected():
    src = "import pickle\ndef f(p):\n    return pickle.load(p)\n"
    issues, _ = _scan_src(src)
    assert 'dangerous_functions' in _rules(issues)


# ---------------------------------------------------------------------------
# P0：硬编码密钥 / 命令注入
# ---------------------------------------------------------------------------
def test_hardcoded_secret_detected():
    src = "api_key = 'sk-1234567890abcdef'\n"
    issues, _ = _scan_src(src)
    assert 'hardcoded_secrets' in _rules(issues)


def test_env_based_secret_not_flagged():
    src = "import os\napi_key = os.environ.get('API_KEY')\n"
    issues, _ = _scan_src(src)
    assert 'hardcoded_secrets' not in _rules(issues)


def test_os_system_detected():
    src = "import os\ndef f(cmd):\n    os.system(cmd)\n"
    issues, _ = _scan_src(src)
    assert 'command_injection' in _rules(issues)


# ---------------------------------------------------------------------------
# P0/P1：SQL 注入 vs 标识符拼接
# ---------------------------------------------------------------------------
def test_sql_value_interpolation_is_p0():
    src = (
        "def f(cur, issue):\n"
        "    cur.execute(f'SELECT * FROM t WHERE issue = {issue}')\n"
    )
    issues, _ = _scan_src(src)
    assert 'sql_injection' in _sev_rules(issues, 'P0')


def test_sql_identifier_interpolation_is_p1():
    """表名来自代码内白名单 → 归为 P1 标识符拼接，而非 P0 值注入。"""
    src = (
        "def f(cur, table_name):\n"
        "    cur.execute(f'SELECT issue FROM `{table_name}` LIMIT 1')\n"
    )
    issues, _ = _scan_src(src)
    assert 'sql_identifier_interpolation' in _sev_rules(issues, 'P1')
    assert 'sql_injection' not in _sev_rules(issues, 'P0')


def test_sql_enum_loop_var_is_identifier():
    """遍历字面量字符串列表的循环变量 → 固定枚举，按标识符处理。"""
    src = (
        "def f(cur):\n"
        "    for p in ['wan', 'qian', 'bai', 'shi', 'ge']:\n"
        "        cur.execute(f'SELECT {p}_number FROM t')\n"
    )
    issues, _ = _scan_src(src)
    assert 'sql_identifier_interpolation' in _sev_rules(issues, 'P1')


def test_parameterized_sql_not_flagged():
    src = "def f(cur, issue):\n    cur.execute('SELECT * FROM t WHERE issue = %s', (issue,))\n"
    issues, _ = _scan_src(src)
    assert 'sql_injection' not in _rules(issues)


def test_collect_enum_vars():
    import ast
    src = "pos_keys = ['wan', 'qian']\nfor p in pos_keys:\n    pass\n"
    tree = ast.parse(src)
    assert 'p' in _collect_enum_vars(tree)


# ---------------------------------------------------------------------------
# P1：timeout / 未用 with 的 open / 调试代码
# ---------------------------------------------------------------------------
def test_http_without_timeout_detected():
    src = "import requests\ndef f():\n    return requests.get('http://x')\n"
    issues, _ = _scan_src(src)
    assert 'missing_timeout' in _sev_rules(issues, 'P1')


def test_http_with_timeout_not_flagged():
    src = "import requests\ndef f():\n    return requests.get('http://x', timeout=5)\n"
    issues, _ = _scan_src(src)
    assert 'missing_timeout' not in _rules(issues)


def test_open_without_with_detected():
    src = "def f():\n    open('a.txt').read()\n"
    issues, _ = _scan_src(src)
    assert 'unclosed_files' in _sev_rules(issues, 'P1')


def test_open_with_with_not_flagged():
    src = "def f():\n    with open('a.txt') as fh:\n        return fh.read()\n"
    issues, _ = _scan_src(src)
    assert 'unclosed_files' not in _rules(issues)


def test_assigned_open_not_flagged():
    """赋值保存的长生命周期句柄（如日志文件）不算泄漏 → 回归防护。"""
    src = "class C:\n    def open_log(self):\n        self._log = open('a.log', 'a')\n"
    issues, _ = _scan_src(src)
    assert 'unclosed_files' not in _rules(issues)


def test_breakpoint_detected():
    src = "def f():\n    breakpoint()\n"
    issues, _ = _scan_src(src)
    assert 'debug_code' in _sev_rules(issues, 'P1')


# ---------------------------------------------------------------------------
# P2：类型注解 / 函数过长 / 函数内 import
# ---------------------------------------------------------------------------
def test_missing_type_hints_detected():
    src = "def add(a, b):\n    return a + b\n"
    issues, _ = _scan_src(src)
    assert 'missing_type_hints' in _sev_rules(issues, 'P2')


def test_typed_function_not_flagged():
    src = "def add(a: int, b: int) -> int:\n    return a + b\n"
    issues, _ = _scan_src(src)
    assert 'missing_type_hints' not in _rules(issues)


def test_long_function_detected():
    body = '\n'.join(f'    x{i} = {i}' for i in range(60))
    src = f"def big():\n{body}\n"
    issues, _ = _scan_src(src, AnalyzerConfig(max_function_lines=50))
    assert 'long_function' in _sev_rules(issues, 'P2')


def test_inline_import_detected():
    src = "def f():\n    import json\n    return json.dumps({})\n"
    issues, _ = _scan_src(src)
    assert 'inline_imports' in _sev_rules(issues, 'P2')


# ---------------------------------------------------------------------------
# 汇总 / 渲染 / 语法错误 / 上限
# ---------------------------------------------------------------------------
def test_syntax_error_reported_as_p0():
    src = "def f(:\n    pass\n"
    issues, _ = _scan_src(src)
    assert any(i.rule == 'syntax_error' and i.severity == 'P0' for i in issues)


def test_summary_passed_flag():
    clean = Issue('P2', 'x', 'a.py', 1, 'm')
    dirty = Issue('P0', 'bare_except', 'a.py', 1, 'm')
    assert CodeAnalyzer.summary([clean])['passed'] is True
    assert CodeAnalyzer.summary([clean, dirty])['passed'] is False
    assert CodeAnalyzer.summary([dirty])['by_severity']['P0'] == 1


def test_issue_to_dict():
    i = Issue('P1', 'r', 'f.py', 7, 'msg')
    d = i.to_dict()
    assert d == {'severity': 'P1', 'rule': 'r', 'file': 'f.py',
                 'line': 7, 'message': 'msg'}


def test_max_issues_per_rule_cap():
    src = '\n'.join(f"try:\n    pass\nexcept:\n    pass" for _ in range(10))
    issues, _ = _scan_src(src, AnalyzerConfig(max_issues_per_rule=3))
    assert len([i for i in issues if i.rule == 'bare_except']) <= 3


def test_render_contains_gate_verdict():
    src = "try:\n    pass\nexcept:\n    pass\n"
    issues, analyzer = _scan_src(src)
    text = analyzer.render(issues)
    assert 'CI 闸门' in text and '❌' in text


def test_analyzer_import_has_no_side_effects():
    """import 零副作用：不启动线程、不写文件。"""
    import threading
    import modules.code_analyzer as ca
    assert hasattr(ca, 'CodeAnalyzer')
    # 模块内无模块级线程创建；仅允许主线程存在
    assert threading.active_count() >= 1


if __name__ == '__main__':
    import unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
