#!/usr/bin/env python3
"""G0 基线清单生成工具。

遍历核心代码资产（main.py/config.py/version.py + modules/scripts/tests + data/sql），
计算每个文件的 sha256 与行数，附加 git 状态快照，产出
``reports/prep/manifest.json`` 作为 v5.2 实施前的版本追溯基线。

用法：
    python scripts/tools/gen_manifest.py

说明：
    - 排除 __pycache__、_gui_v5_test_home 等运行期产物目录；
    - git 工作区非干净时如实记录 git_status_porcelain，不做任何入库动作。
"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 项目根目录（本脚本位于 scripts/tools/ 下，上溯两级）
ROOT = Path(__file__).resolve().parents[2]

# 纳入基线的扫描目标：(类型, 相对路径)
SCAN_TARGETS = (
    ('file', 'main.py'),
    ('file', 'config.py'),
    ('file', 'version.py'),
    ('dir', 'modules'),
    ('dir', 'scripts'),
    ('dir', 'tests'),
    ('dir', 'data/sql'),
)

# 排除的运行期产物/缓存目录名
EXCLUDE_DIRS = {'__pycache__', '_gui_v5_test_home', '.pytest_cache', '.mypy_cache'}

# 纳入基线的文件后缀（代码 + DDL 资产）
INCLUDE_SUFFIXES = {'.py', '.sql'}


def _git(args: list) -> tuple:
    """执行 git 命令，返回 (退出码, stdout 文本)。"""
    try:
        proc = subprocess.run(
            ['git'] + args, cwd=str(ROOT),
            capture_output=True, text=True,
        )
        return proc.returncode, (proc.stdout or '').strip()
    except OSError:
        return 1, ''


def _sha256(path: Path) -> str:
    """分块计算文件 sha256，避免大文件占用过多内存。"""
    digest = hashlib.sha256()
    with open(str(path), 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(path: Path) -> int:
    """统计文件行数（utf-8 容错解码，兼容历史遗留编码）。"""
    with open(str(path), 'r', encoding='utf-8', errors='replace') as f:
        return sum(1 for _ in f)


def collect_files() -> list:
    """按 SCAN_TARGETS 收集基线文件，返回去重排序后的 Path 列表。"""
    files = []
    for kind, rel in SCAN_TARGETS:
        base = ROOT / rel
        if kind == 'file':
            if base.is_file():
                files.append(base)
            continue
        if not base.is_dir():
            continue
        for path in base.rglob('*'):
            if not path.is_file() or path.suffix not in INCLUDE_SUFFIXES:
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            files.append(path)
    return sorted(set(files), key=lambda p: str(p.relative_to(ROOT)).lower())


def main() -> int:
    """生成 manifest.json 并返回进程退出码。"""
    entries = []
    for path in collect_files():
        rel = str(path.relative_to(ROOT)).replace('\\', '/')
        entries.append({
            'path': rel,
            'sha256': _sha256(path),
            'lines': _count_lines(path),
        })

    _, head = _git(['rev-parse', 'HEAD'])
    _, porcelain = _git(['status', '--porcelain'])
    _, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'])
    manifest = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'project': str(ROOT),
        'git_head': head,
        'git_branch': branch,
        'git_clean': porcelain == '',
        'git_status_porcelain': porcelain.splitlines(),
        'baseline_note': ('v5.1 在建资产未入库（工作区非干净）；'
                          '本清单以现状为基线，入库动作待用户确认后执行'),
        'file_count': len(entries),
        'total_lines': sum(entry['lines'] for entry in entries),
        'files': entries,
    }

    out_path = ROOT / 'reports' / 'prep' / 'manifest.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out_path), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print('manifest: {} files / {} lines -> {}'.format(
        len(entries), manifest['total_lines'], out_path))
    print('git_head: {} branch: {} clean: {}'.format(
        head, branch, manifest['git_clean']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
