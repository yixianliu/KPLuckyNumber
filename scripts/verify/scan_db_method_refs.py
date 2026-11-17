# -*- coding: utf-8 -*-
"""P5Database 公开方法全仓引用扫描（审计工具）。

用途：database.py 死方法收敛批次（docs/db_method_consolidation_plan_2026-11-16.md）
执行前重跑，确认候选清单无新增引用；亦可日常监控公开方法的引用面。

口径：
- 「存活引用」= gui/、main.py、modules/（除 database.py 自身）、tests/、scripts/、
  reports/、maint/、version.py、根目录 *.py 中的代码引用（排除 .venv/.workbuddy 等）
- database.py 内部命中单独标注（需人工核对是 docstring/注释/__main__ 还是真实内调）
- 动态方法名构造（getattr / f-string）单独列出——静态正则无法命中
  getattr(db, f'get_{pos}_trend_data') 一类动态调用，判定死方法时必须人工核查

用法：python scripts/verify/scan_db_method_refs.py
"""
import ast
import os
import re

ROOT = '.'
SKIP_DIRS = {'__pycache__', '.git', '.pytest_cache', 'node_modules', 'recycle', 'models', 'logs',
             '.venv', '.workbuddy', 'backup_pre_cleanup', '.trae', 'dist', 'build'}

# 1) 枚举 P5Database 公开方法
db_src = open('modules/database.py', encoding='utf-8').read()
tree = ast.parse(db_src)
cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'P5Database')
methods = [n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and not n.name.startswith('_')]
print(f'P5Database 公开方法共 {len(methods)} 个')

# 2) 收集全部 py 文件（按顶层目录分组）
files = []
for root, dirs, fns in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in fns:
        if fn.endswith('.py'):
            p = os.path.join(root, fn).replace('\\', '/')
            files.append(p)

def group(p):
    if p.startswith('gui/'):
        return 'gui'
    if p == 'main.py':
        return 'main'
    if p.startswith('modules/'):
        return 'modules'
    if p.startswith('tests/'):
        return 'tests'
    if p.startswith('scripts/'):
        return 'scripts'
    if p.startswith('reports/'):
        return 'reports'
    return 'root'

cache = {}
def lines_of(p):
    if p not in cache:
        cache[p] = open(p, encoding='utf-8', errors='ignore').read()
    return cache[p]

# 3) 逐方法统计引用（排除定义行）
report = {}
for m in methods:
    refs = []
    pat = re.compile(r'\b' + re.escape(m) + r'\b')
    for p in files:
        if p.endswith('modules/database.py'):
            # 定义行除外；同模块内自调用单独标注（需人工核对）
            src = lines_of(p)
            hits = [(i + 1, ln.strip()[:80]) for i, ln in enumerate(src.split('\n'))
                    if pat.search(ln) and f'def {m}(' not in ln]
            if hits:
                refs.append(('SELF', hits[:4]))
            continue
        src = lines_of(p)
        hits = [(i + 1) for i, ln in enumerate(src.split('\n')) if pat.search(ln)]
        if hits:
            refs.append((group(p) + ':' + p, hits[:3]))
    report[m] = refs

# 4) 输出：仅按存活引用分类（SELF 之外的引用才算「有调用」）
zero, referenced = [], []
for m, refs in report.items():
    live = [r for r in refs if r[0] != 'SELF']
    if not live:
        zero.append((m, [h for g, hs in refs if g == 'SELF' for h in hs]))
    else:
        referenced.append((m, live, [h for g, hs in refs if g == 'SELF' for h in hs]))

print(f'\n=== 存活零引用 {len(zero)} 个（候选收敛对象）===')
for m, selfrefs in zero:
    sr = f' | 自调用: {selfrefs}' if selfrefs else ''
    print(f' - {m}{sr}')
print(f'\n=== 存活有引用 {len(referenced)} 个 ===')
for m, refs, selfrefs in referenced:
    loc = '; '.join(f'{g} L{hs}' for g, hs in refs[:4])
    print(f' - {m}: {loc}' + (f' | 自调用: {selfrefs}' if selfrefs else ''))

# 5) 动态方法名构造模式（getattr / f-string）
print('\n=== 动态方法名构造模式 ===')
dyn_pat = re.compile(r"getattr\(db[^)]*f'|getattr\(self\.db[^)]*f'|f'(get|insert|save|load|update|count|delete)_[^']*'|\"(get|insert|save|load|update|count|delete)_[^\"]*\"")
for p in files:
    if p.endswith('modules/database.py'):
        continue
    src = lines_of(p)
    for i, ln in enumerate(src.split('\n')):
        if dyn_pat.search(ln):
            print(f' {p}:{i+1}: {ln.strip()[:110]}')
