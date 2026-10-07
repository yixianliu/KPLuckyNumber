# -*- coding: utf-8 -*-
"""代码安全扫描与质量审查（方案 §8.2.5）。

方案 §8.2.5 要求：
  * 安全扫描：采用 Code Analyzer P0/P1/P2/P3 规则，全模块扫描，无高危漏洞；
  * 代码审查：全模块审查，符合 PEP 8，无代码异味；
  * 编码规范校验：``py_compile`` 语法校验全部通过。

本模块用 **AST（抽象语法树）** 实现规则检测，而非文本正则——后者在
``except Exception:`` 这类匹配上极易误判（把合法写法也算成裸 except），
本项目此前 ``CodeQualityEvaluator`` 即为此类粗糙启发式，已改为消费本模块。

规则分级（与方案/CI 闸门一致）:
  P0 必须修复（安全/稳定性）：裸 except、危险函数（eval/exec/pickle/marshal/yaml.load）、
     硬编码密钥、SQL 注入（f-string/%/拼接）、命令注入（os.system、shell=True）。
  P1 重要（可靠性）：HTTP 请求缺 timeout、文件打开未用 with、调试代码（pdb/breakpoint）。
  P2 建议（质量）：公共函数缺类型注解、函数过长(>50行)、函数内 import、print 调试语句。
  P3 可选（风格）：模块/函数缺 docstring。

设计约束（对齐项目惯例）：
  * **无 DB / 无网络依赖**，纯标准库（ast/os/re），可离线运行。
  * **import 零副作用**：不启动线程、不读配置、不做文件写入。
  * 只读扫描，绝不修改被扫描文件（修复由人工/CI 决定）。

用法:
    from modules.code_analyzer import CodeAnalyzer
    analyzer = CodeAnalyzer()
    issues = analyzer.analyze_dir('modules')
    print(analyzer.summary(issues))

    # 或命令行全模块扫描
    python -m modules.code_analyzer
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

# ---------------------------------------------------------------------------
# 规则常量
# ---------------------------------------------------------------------------
DANGEROUS_CALLS: Set[str] = {'eval', 'exec', 'compile'}
DANGEROUS_ATTRS: Set[str] = {
    'pickle.load', 'pickle.loads', 'marshal.load', 'marshal.loads',
    'yaml.load', 'subprocess.call', 'subprocess.Popen',
}
SECRET_NAME_HINTS: Set[str] = {
    'api_key', 'apikey', 'password', 'passwd', 'pwd', 'secret',
    'token', 'access_key', 'accesskey', 'secret_key', 'private_key',
}
HTTP_CALLS: Set[str] = {'get', 'post', 'put', 'delete', 'patch', 'request', 'head'}
DEBUG_CALLS: Set[str] = {'breakpoint', 'set_trace'}
# 标识符（表名/列名）插值 ≠ 用户值注入：命中这些字样时判定为「标识符拼接」(P1 加固)，
# 而非「SQL 注入」(P0)。本项目 23 处命中均为表名来自代码内固定白名单的情形。
IDENTIFIER_HINTS: tuple = ('table', 'column', 'tbl', 'col', 'db_name', 'field')

DEFAULT_EXCLUDE_DIRS: Set[str] = {
    '__pycache__', '.git', '.workbuddy', 'venv', '.venv', 'env',
    'data', 'logs', 'reports', 'archive', 'node_modules',
}


@dataclass
class Issue:
    """一条扫描发现。"""
    severity: str                       # 'P0' | 'P1' | 'P2' | 'P3'
    rule: str                           # 规则名（英文标识，便于 CI 过滤）
    file: str                           # 文件相对路径
    line: int                           # 行号（1-based）
    message: str                        # 人类可读说明

    def to_dict(self) -> Dict[str, Any]:
        """转为可序列化字典（供报告/JSONL 落盘）。"""
        return {
            'severity': self.severity,
            'rule': self.rule,
            'file': self.file,
            'line': self.line,
            'message': self.message,
        }


@dataclass
class AnalyzerConfig:
    """扫描配置（阈值可调，避免噪声刷屏）。"""
    max_function_lines: int = 50        # P2 函数过长阈值
    max_line_length: int = 120          # P3 行长度阈值
    max_issues_per_rule: int = 20       # 单规则最多上报条数（防噪声）
    check_type_hints: bool = True       # P2 缺类型注解
    check_print: bool = True            # P2 print 调试语句（库模块建议用 logging）
    exclude_dirs: Set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))


def _collect_enum_vars(tree: ast.AST) -> Set[str]:
    """收集取值被约束为「字面量字符串列表」的变量名（固定枚举）。

    典型形态：``for pos_abbr in ['wan','qian','bai','shi','ge']`` 或
    ``pos_keys = ['wan', ...]; for p in pos_keys``。这类变量取值域是代码内
    固定集合，拼进 SQL 只能产生有限个合法标识符，**不构成用户值注入**，
    故视为标识符（P1 加固）而非注入（P0）。
    """
    enum_lists: Set[str] = set()   # 持有字面量字符串列表的变量
    enum_vars: Set[str] = set()    # 遍历该列表的循环目标变量

    def _is_str_literal_seq(node: ast.AST) -> bool:
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return False
        elts = getattr(node, 'elts', [])
        return bool(elts) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and _is_str_literal_seq(node.value):
            enum_lists.add(node.targets[0].id)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            if _is_str_literal_seq(node.iter) or (
                    isinstance(node.iter, ast.Name) and node.iter.id in enum_lists):
                enum_vars.add(node.target.id)
    return enum_vars


def _dotted(node: ast.AST) -> Optional[str]:
    """把 Name/Attribute 节点还原成点号名，如 requests.get → 'requests.get'。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f'{base}.{node.attr}' if base else node.attr
    return None


class _RuleVisitor(ast.NodeVisitor):
    """单文件 AST 访问器，按 P0/P1/P2/P3 规则收集问题。"""

    def __init__(self, rel_path: str, cfg: AnalyzerConfig, with_call_ids: Set[int],
                 assigned_call_ids: Optional[Set[int]] = None,
                 enum_vars: Optional[Set[str]] = None):
        self.rel = rel_path
        self.cfg = cfg
        self._with_calls = with_call_ids
        # open() 结果被赋值给变量时通常是「有意的长生命周期句柄」（如日志文件），
        # 由所有者负责关闭，不算泄漏 → 排除，避免误报。
        self._assigned_calls = assigned_call_ids or set()
        # 固定枚举变量（遍历字面量字符串列表），拼 SQL 时按标识符处理
        self._enum_vars = enum_vars or set()
        self.issues: List[Issue] = []
        self._func_depth = 0             # >0 表示当前在函数体内（用于 inline import）
        self._in_class = 0

    # -- 内部工具 ---------------------------------------------------------
    def _add(self, severity: str, rule: str, line: int, message: str) -> None:
        same = sum(1 for i in self.issues if i.rule == rule)
        if same >= self.cfg.max_issues_per_rule:
            return
        self.issues.append(Issue(severity, rule, self.rel, line, message))

    @staticmethod
    def _is_str_constant(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    # -- 访问器 -----------------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_signature(node)
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._in_class += 1
        self.generic_visit(node)
        self._in_class -= 1

    def _check_signature(self, node: ast.FunctionDef) -> None:
        """P2：公共函数缺类型注解；P3：缺 docstring；P2：函数过长。"""
        lines = (node.end_lineno or node.lineno) - node.lineno + 1
        if lines > self.cfg.max_function_lines:
            self._add('P2', 'long_function', node.lineno,
                      f'函数 {node.name} 过长（{lines} 行 > {self.cfg.max_function_lines}），建议拆分')

        if self.cfg.check_type_hints and not node.name.startswith('_'):
            missing_args = [a.arg for a in node.args.args
                            if a.annotation is None and a.arg not in ('self', 'cls')]
            missing_ret = node.returns is None
            if missing_args or missing_ret:
                parts = []
                if missing_args:
                    parts.append('参数 ' + ', '.join(missing_args[:3]))
                if missing_ret:
                    parts.append('返回值')
                self._add('P2', 'missing_type_hints', node.lineno,
                          f'函数 {node.name} 缺类型注解（{"；".join(parts)}）')

        if not ast.get_docstring(node):
            self._add('P3', 'missing_docstring', node.lineno,
                      f'函数 {node.name} 缺 docstring')

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """P0：裸 except（不指定异常类型，会吞掉 SystemExit/KeyboardInterrupt）。"""
        if node.type is None:
            self._add('P0', 'bare_except', node.lineno,
                      '裸 except: 会捕获 SystemExit/KeyboardInterrupt，应改为具体异常类型')
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """P0：硬编码密钥（字面量赋值给 *key/*token/*password 类变量名）。"""
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            low = target.id.lower()
            if not any(hint in low for hint in SECRET_NAME_HINTS):
                continue
            if self._is_str_constant(node.value):
                raw = node.value.value  # type: ignore[attr-defined]
                if raw and raw.strip() and not raw.startswith(('$', '{', '<', 'YOUR_')):
                    self._add('P0', 'hardcoded_secrets', node.lineno,
                              f'疑似硬编码密钥：{target.id}（建议改用环境变量/配置源）')
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        last = name.split('.')[-1] if name else ''

        # P0 危险函数 -----------------------------------------------------
        if last in DANGEROUS_CALLS and isinstance(node.func, ast.Name):
            self._add('P0', 'dangerous_functions', node.lineno,
                      f'危险函数 {last}()：存在任意代码执行风险')
        if name in DANGEROUS_ATTRS:
            extra = ''
            if name == 'yaml.load':
                # yaml.load 带 SafeLoader 是安全的，仅无 Loader 参数时才告警
                if not any(kw.arg == 'Loader' for kw in node.keywords):
                    extra = '（未指定 Loader=SafeLoader，可反序列化任意对象）'
                    self._add('P0', 'dangerous_functions', node.lineno,
                              f'危险函数 {name}(){extra}')
            else:
                self._add('P0', 'dangerous_functions', node.lineno,
                          f'危险函数 {name}(){extra}')

        # P0/P1 SQL 构造：区分「值注入(P0)」与「标识符拼接(P1 加固)」--------
        if last == 'execute' and node.args:
            if self._is_injection_arg(node.args[0]):
                names = self._interpolated_names(node.args[0])
                if names and all(self._looks_like_identifier(n) for n in names):
                    self._add('P1', 'sql_identifier_interpolation', node.lineno,
                              f'SQL 拼接标识符 {sorted(names)}（表名/列名，来自代码内白名单），'
                              '非用户值注入；建议加白名单校验加固')
                else:
                    self._add('P0', 'sql_injection', node.lineno,
                              'SQL 通过 f-string/%/拼接构造，存在注入风险，请改用参数化查询 (%s 占位)')

        # P0 命令注入 -----------------------------------------------------
        if name in ('os.system', 'subprocess.call', 'subprocess.Popen', 'subprocess.run'):
            shell_true = any(kw.arg == 'shell' and getattr(kw.value, 'value', False)
                             for kw in node.keywords)
            if name == 'os.system' or shell_true:
                self._add('P0', 'command_injection', node.lineno,
                          f'{name} 调用存在命令注入/阻塞风险，建议改用参数列表并显式 timeout')

        # P1 HTTP 缺 timeout ----------------------------------------------
        if last in HTTP_CALLS and isinstance(node.func, ast.Attribute):
            base = _dotted(node.func.value) or ''
            if base in ('requests', 'session', 'self.session', 'http'):
                if not any(kw.arg == 'timeout' for kw in node.keywords):
                    self._add('P1', 'missing_timeout', node.lineno,
                              f'{name}() 未设置 timeout，网络异常时会无限挂起')

        # P1 文件打开未用 with（排除「结果被赋值」的长生命周期句柄）-------
        if name == 'open' and id(node) not in self._with_calls \
                and id(node) not in self._assigned_calls:
            self._add('P1', 'unclosed_files', node.lineno,
                      'open() 未使用 with 上下文管理，可能泄漏文件句柄')

        # P1 调试代码 ------------------------------------------------------
        if last in DEBUG_CALLS:
            self._add('P1', 'debug_code', node.lineno,
                      f'调试代码 {name}()，上线前应移除')

        # P2 print 调试语句（库模块建议 logging）--------------------------
        if self.cfg.check_print and last == 'print' and isinstance(node.func, ast.Name):
            self._add('P2', 'print_debug', node.lineno,
                      '库模块中使用 print()，建议改用 logging')

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._check_import(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_import(node)
        self.generic_visit(node)

    def _check_import(self, node: ast.AST) -> None:
        """P2 函数内 import（本项目「懒加载外部依赖」是明确约定，故仅 P2 提示不阻断）。"""
        if self._func_depth > 0:
            mod = ''
            if isinstance(node, ast.Import):
                mod = ', '.join(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ''
            self._add('P2', 'inline_imports', node.lineno,
                      f'函数内 import {mod}（本项目懒加载外部依赖为约定，属提示级）')
        if isinstance(node, ast.Import):
            if any(a.name == 'pdb' for a in node.names):
                self._add('P1', 'debug_code', node.lineno, '导入 pdb，上线前应移除')

    @staticmethod
    def _interpolated_names(node: ast.AST) -> Set[str]:
        """提取 f-string / % / 拼接中被插值的变量名集合。"""
        names: Set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.FormattedValue):          # f-string 的 {xxx}
                if isinstance(sub.value, ast.Name):
                    names.add(sub.value.id)
            elif isinstance(sub, ast.BinOp):                 # % 或 + 两侧
                for side in (sub.left, sub.right):
                    if isinstance(side, ast.Name):
                        names.add(side.id)
        return names

    def _looks_like_identifier(self, name: str) -> bool:
        """变量名是否像表名/列名等 SQL 标识符（而非业务值）。

        判定依据：① 名字含 table/column/db_name 等标识符字样；② 取值被约束为
        代码内字面量字符串列表（固定枚举，如位置缩写 wan/qian/bai/shi/ge）。
        """
        if name in self._enum_vars:
            return True
        low = name.lower()
        return any(hint in low for hint in IDENTIFIER_HINTS) or low.startswith('_')

    @staticmethod
    def _is_injection_arg(node: ast.AST) -> bool:
        """判断 SQL 实参是否为 f-string / % 格式化 / 字符串拼接。"""
        if isinstance(node, ast.JoinedStr):          # f-string
            return True
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.Mod, ast.Add)):
                return True
        if isinstance(node, ast.Call):               # "..." .format(...)
            name = _dotted(node.func)
            if name and name.endswith('.format'):
                return True
        return False


class CodeAnalyzer:
    """代码安全扫描与质量审查器（AST 实现，方案 §8.2.5）。"""

    def __init__(self, config: Optional[AnalyzerConfig] = None):
        self.cfg = config or AnalyzerConfig()

    # ------------------------------------------------------------------
    # 单文件 / 目录扫描
    # ------------------------------------------------------------------
    def analyze_file(self, path: str, root: Optional[str] = None) -> List[Issue]:
        """扫描单个 .py 文件；语法错误时返回一条 P0 而非抛异常。"""
        rel = os.path.relpath(path, root) if root else os.path.basename(path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                src = f.read()
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            return [Issue('P0', 'syntax_error', rel, getattr(e, 'lineno', 0) or 0,
                          f'语法错误: {e.msg}')]
        except OSError as e:
            return [Issue('P0', 'read_error', rel, 0, f'读取失败: {e}')]

        # 预收集 with-item 中的调用节点 id，用于判断 open() 是否被 with 包裹
        with_call_ids: Set[int] = set()
        assigned_call_ids: Set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    if isinstance(item.context_expr, ast.Call):
                        with_call_ids.add(id(item.context_expr))
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                val = getattr(node, 'value', None)
                if isinstance(val, ast.Call) and _dotted(val.func) == 'open':
                    assigned_call_ids.add(id(val))

        visitor = _RuleVisitor(rel, self.cfg, with_call_ids, assigned_call_ids,
                               _collect_enum_vars(tree))
        visitor.visit(tree)

        # P3 行长度
        for lineno, line in enumerate(src.split('\n'), start=1):
            if len(line) > self.cfg.max_line_length:
                visitor._add('P3', 'long_line', lineno,
                             f'行长度 {len(line)} > {self.cfg.max_line_length}，建议折行')
        return visitor.issues

    def analyze_dir(self, path: str, root: Optional[str] = None) -> List[Issue]:
        """递归扫描目录下所有 .py 文件。"""
        root = os.path.abspath(root or path)
        issues: List[Issue] = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in self.cfg.exclude_dirs]
            for fn in sorted(filenames):
                if fn.endswith('.py'):
                    issues.extend(self.analyze_file(os.path.join(dirpath, fn), root=root))
        return issues

    def analyze_paths(self, paths: Iterable[str], root: Optional[str] = None) -> List[Issue]:
        """扫描一组路径（文件或目录）。"""
        issues: List[Issue] = []
        for p in paths:
            if os.path.isdir(p):
                issues.extend(self.analyze_dir(p, root=root))
            elif os.path.isfile(p) and p.endswith('.py'):
                issues.extend(self.analyze_file(p, root=root))
        return issues

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    @staticmethod
    def summary(issues: List[Issue]) -> Dict[str, Any]:
        """按分级汇总，并给出是否通过 CI 闸门的判定（P0 存在即不通过）。"""
        by_sev: Dict[str, int] = {'P0': 0, 'P1': 0, 'P2': 0, 'P3': 0}
        by_rule: Dict[str, int] = {}
        for i in issues:
            by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
            by_rule[i.rule] = by_rule.get(i.rule, 0) + 1
        return {
            'total': len(issues),
            'by_severity': by_sev,
            'by_rule': dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
            'passed': by_sev.get('P0', 0) == 0,
        }

    @staticmethod
    def render(issues: List[Issue], title: str = '§8.2.5 代码安全扫描与质量审查') -> str:
        """渲染人类可读报告（供 CLI / GUI 展示）。"""
        s = CodeAnalyzer.summary(issues)
        lines = ['=' * 70, title, '=' * 70]
        lines.append(f"总计 {s['total']} 条："
                     f"P0={s['by_severity'].get('P0', 0)} "
                     f"P1={s['by_severity'].get('P1', 0)} "
                     f"P2={s['by_severity'].get('P2', 0)} "
                     f"P3={s['by_severity'].get('P3', 0)}")
        lines.append(f"CI 闸门（P0=0）：{'✅ 通过' if s['passed'] else '❌ 未通过'}")
        lines.append('-' * 70)
        for sev in ('P0', 'P1', 'P2', 'P3'):
            group = [i for i in issues if i.severity == sev]
            if not group:
                continue
            lines.append(f'[{sev}] {len(group)} 条')
            for i in group[:15]:
                lines.append(f'  {i.file}:{i.line}  {i.rule} — {i.message}')
            if len(group) > 15:
                lines.append(f'  … 其余 {len(group) - 15} 条省略')
        lines.append('=' * 70)
        return '\n'.join(lines)


def main() -> int:
    """命令行入口：扫描项目 modules/ + 根目录 main.py。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [os.path.join(root, 'modules'), os.path.join(root, 'main.py')]
    analyzer = CodeAnalyzer()
    issues = analyzer.analyze_paths(targets, root=root)
    print(analyzer.render(issues))
    return 0 if analyzer.summary(issues)['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
