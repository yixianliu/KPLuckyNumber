# -*- coding: utf-8 -*-
"""进化引擎「分析 → 验证 → 触发」联动链路端到端核验（v5.0 §7.5）。

计划文档写作 `scripts/verify_evolution_linkage.py`；实际落在 `scripts/verify/`
以与同目录既有 `verify_*.py` 命名惯例一致。

核验内容（全部离线、不连 DB、不触 sklearn、不写真实 data/）：
  1. 分析→联动      sync_analysis_result 正确登记期号/预测/待回填标志
  2. 验证→命中评估  sync_verification 的 Top1/3/5 命中百分比可复算
  3. 触发→事件投递  开奖回填向 GUI 队列投递结构化 evolve_trigger 事件
  4. 触发→去重      同一期号第二次回填不得重复触发进化
  5. 去重→持久化    重启后去重凭据仍在，不会重复触发
  6. 样本量闸门     history_count < ML_EVAL_MIN 时拒绝触发（指标不可比）
  7. 建议只读       get_latest_advisory 不写盘、不改状态
  8. 诚实边界       无候选超基线时不注入生产权重、建议文本显式声明公平摇号
  9. 检查点中断安全 保存被中断时上一份检查点完整、无 .tmp 残留
 10. 状态文件完好性 中断后 schedule / db_fail / checkpoint 均为可解析 JSON

用法：
    python scripts/verify/verify_evolution_linkage.py
    python scripts/verify/verify_evolution_linkage.py -v

退出码：0 = 全部通过；1 = 存在失败（可直接作 CI 闸门）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback

# 让脚本可从任意工作目录启动
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from modules.self_evolution import (  # noqa: E402
    ML_EVAL_MIN,
    AutoEvoScheduler,
    SelfEvolutionEngine,
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class FakePredictorConfig:
    """最小 P5PredictorConfig 替身：只暴露 .config dict。"""

    def __init__(self, config_dict):
        self.config = config_dict


class FakePredictor:
    """最小预测器替身：只暴露 .config。"""

    def __init__(self, config_dict):
        self.config = FakePredictorConfig(config_dict)


class Env:
    """一次核验所需的隔离环境（独立 data 目录 + 已登记的调度器）。"""

    def __init__(self):
        # 不做清理：本机 %TEMP% 上 rmtree 会被沙箱/杀软介入后无限阻塞（已实测）
        self.dir = tempfile.mkdtemp(prefix='kpl_evo_link_')
        self.eng = SelfEvolutionEngine(data_dir=self.dir)
        self.sched = AutoEvoScheduler(self.eng, data_dir=self.dir, interval_hours=0)
        self.eng._scheduler = self.sched
        self.sched.mark_run()          # 满足「调度已到期」前提
        self.eng._running = False
        self.eng._checkpoint = {
            'history_count': 900,
            'baseline': {'metrics': {'top1': 10.0, 'top3': 30.0, 'top5': 50.0}},
        }

    # -- 工具 ---------------------------------------------------------------
    @property
    def ckpt_path(self):
        return os.path.join(self.dir, 'self_evolution_state.json')

    @property
    def link_state_path(self):
        return os.path.join(self.dir, 'evolution_link_state.json')

    def drain(self, type_name):
        """清空队列并返回指定 type 的结构化事件（_send 会平铺 data 字段）。"""
        out = []
        while not self.eng.queue.empty():
            out.append(self.eng.queue.get_nowait())
        return [m for m in out if m.get('type') == type_name]


def _read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 各检查项
# ---------------------------------------------------------------------------
def check_analysis_linkage(env, verbose):
    """1. 分析→联动：预测记录正确登记并落盘。"""
    env.eng.sync_analysis_result({
        'target_issue': '2026901',
        'top_numbers': [[3], [7], [1], [5], [9]],
        'fused_probabilities': {'wan': {3: 0.2}},
    })
    ls = env.eng._link_state
    assert ls['last_analysis_issue'] == '2026901', '期号未登记'
    assert ls['pending_verification'] is True, '待回填标志未置位'
    assert ls['last_prediction']['target_issue'] == '2026901', '预测未落库'
    on_disk = _read_json(env.link_state_path)
    assert on_disk['last_analysis_issue'] == '2026901', '联动状态未持久化'
    return 'sync_analysis_result 期号/预测/待回填标志已登记并持久化'


def check_verification_metrics(env, verbose):
    """2. 验证→命中评估：Top1/3/5 命中百分比可复算。"""
    env.eng._link_state['last_prediction'] = {
        'target_issue': '2026901',
        'top_numbers': [[3, 8, 1], [7, 2, 5], [1, 0, 4], [5, 9, 6], [9, 3, 7]],
    }
    # 实际开奖 3,2,1,9,8，逐位核对：
    #   万: 3 ∈ [3,8,1]  → Top1✓ Top3✓ Top5✓
    #   千: 2 ∈ [7,2,5]  → Top1✗ Top3✓ Top5✓
    #   百: 1 ∈ [1,0,4]  → Top1✓ Top3✓ Top5✓
    #   十: 9 ∈ [5,9,6]  → Top1✗ Top3✓ Top5✓
    #   个: 8 ∉ [9,3,7]  → Top1✗ Top3✗ Top5✗
    #  Top1=2/5=40.0  Top3=4/5=80.0  Top5=4/5=80.0
    env.eng.sync_verification('2026901', [3, 2, 1, 9, 8])
    lv = env.eng._link_state['last_verification']
    assert lv['issue'] == '2026901', '回填期号不符'
    assert abs(lv['top1'] - 40.0) < 1e-9, f'Top1 计算错误: {lv["top1"]}'
    assert abs(lv['top3'] - 80.0) < 1e-9, f'Top3 计算错误: {lv["top3"]}'
    assert abs(lv['top5'] - 80.0) < 1e-9, f'Top5 计算错误: {lv["top5"]}'
    return 'Top1=40.0 / Top3=80.0 / Top5=80.0，与逐位手算一致'


def check_trigger_event(env, verbose):
    """3. 触发→事件投递：回填必须投递结构化 evolve_trigger 事件（不静默）。"""
    env.eng.sync_analysis_result({
        'target_issue': '2026901', 'top_numbers': [[3]]})
    env.eng.sync_verification('2026901', [3, 0, 0, 0, 0])
    hits = env.drain('evolve_trigger')
    assert len(hits) == 1, f'evolve_trigger 事件数={len(hits)}，期望 1'
    assert hits[0]['issue'] == '2026901', '事件缺少期号'
    assert hits[0]['triggered'] is True, f'未触发: {hits[0]["reason_code"]}'
    assert hits[0]['reason_code'] == 'scheduled_due', hits[0]['reason_code']
    return f"投递 evolve_trigger(triggered=True, reason_code='{hits[0]['reason_code']}')"


def check_trigger_dedup(env, verbose):
    """4. 触发→去重：同一期号第二次回填不得重复触发。"""
    env.eng.sync_analysis_result({
        'target_issue': '2026902', 'top_numbers': [[3]]})
    env.eng.sync_verification('2026902', [3, 0, 0, 0, 0])
    env.drain('evolve_trigger')

    env.eng.sync_verification('2026902', [3, 0, 0, 0, 0])
    hits = env.drain('evolve_trigger')
    assert len(hits) == 1, f'事件数={len(hits)}，期望 1'
    assert hits[0]['triggered'] is False, '重复触发未被拦下'
    assert hits[0]['reason_code'] == 'already_triggered_for_issue', hits[0]['reason_code']

    # 另一期号仍应正常触发
    env.eng.sync_verification('2026903', [3, 0, 0, 0, 0])
    hits2 = env.drain('evolve_trigger')
    assert hits2 and hits2[0]['triggered'] is True, '不同期号被误拦'
    return '同期号去重生效；不同期号正常触发'


def check_dedup_persistence(env, verbose):
    """5. 去重→持久化：重启后去重凭据仍在。"""
    env.eng.sync_analysis_result({
        'target_issue': '2026904', 'top_numbers': [[3]]})
    env.eng.sync_verification('2026904', [3, 0, 0, 0, 0])
    env.drain('evolve_trigger')

    fresh = SelfEvolutionEngine(data_dir=env.dir)
    fresh._checkpoint = dict(env.eng._checkpoint)
    fs = AutoEvoScheduler(fresh, data_dir=env.dir, interval_hours=0)
    fresh._scheduler = fs
    fs.mark_run()
    r = fresh._maybe_trigger_evolution('2026904')
    assert r['triggered'] is False, '重启后重复触发'
    assert r['reason_code'] == 'already_triggered_for_issue', r['reason_code']
    return '去重凭据跨进程重启保留'


def check_sample_gate(env, verbose):
    """6. 样本量闸门：history_count < ML_EVAL_MIN 时拒绝触发。"""
    env.eng._link_state['last_evolve_trigger_issue'] = None
    env.eng._checkpoint['history_count'] = ML_EVAL_MIN - 1
    r = env.eng._maybe_trigger_evolution('2026910')
    assert r['triggered'] is False, '样本不足仍触发'
    assert r['reason_code'] == 'insufficient_samples', r['reason_code']

    env.eng._checkpoint['history_count'] = ML_EVAL_MIN
    r2 = env.eng._maybe_trigger_evolution('2026910')
    assert r2['triggered'] is True, '样本恰为阈值时不应触发'
    assert r2['reason_code'] == 'scheduled_due', r2['reason_code']
    env.eng._checkpoint['history_count'] = 900
    return f'n<{ML_EVAL_MIN} 拒绝；n={ML_EVAL_MIN} 放行'


def check_advisory_readonly(env, verbose):
    """7. 建议只读：get_latest_advisory 不写盘、不改状态。"""
    env.eng._link_state['last_verification'] = {
        'issue': '2026901', 'top1': 40.0, 'top3': 80.0, 'top5': 80.0}
    before_files = sorted(os.listdir(env.dir))
    state_before = json.loads(json.dumps(env.eng._link_state))
    ckpt_before = json.loads(json.dumps(env.eng._checkpoint))

    advisory = env.eng.get_latest_advisory()

    assert sorted(os.listdir(env.dir)) == before_files, '建议过程改动了文件'
    assert env.eng._link_state == state_before, '建议过程改动了联动状态'
    assert env.eng._checkpoint == ckpt_before, '建议过程改动了检查点'
    assert advisory['verification']['top3'] == 80.0, '建议未带出命中数据'
    return '文件数 / 联动状态 / 检查点全部不变'


def check_honest_boundary(env, verbose):
    """8. 诚实边界：无候选超基线时不注入生产权重，且建议显式声明公平摇号。"""
    env.eng._versions_loaded = True
    env.eng._versions = [{
        'version_tag': 'trial-001',
        'status': 'trial',
        'params_json': {'weights': {'frequency_weighted': 0.68}, 'lookback': 60},
        'metrics_json': {'top1': 10.0, 'top3': 30.0, 'top5': 50.0},
    }]
    env.eng._checkpoint = {'history_count': 900,
                           'baseline': {'metrics': {
                               'top1': 10.5, 'top3': 30.5, 'top5': 50.5}}}
    env.eng._link_state['best_candidate'] = {
        'weights': {'frequency_weighted': 0.68}, 'lookback': 60,
        'metrics': {'top1': 10.0, 'top3': 30.0, 'top5': 50.0}}

    advisory = env.eng.get_latest_advisory()
    assert advisory['has_better_candidate'] is False, '误判为有提升候选'
    assert '公平摇号' in advisory['honest_note'], '缺少公平摇号声明'

    config_dict = {'algorithms': {'frequency_weighted': {'enabled': True, 'weight': 0.55}}}
    predictor = FakePredictor(config_dict)
    applied = env.eng.apply_active_config_to_predictor(predictor)
    assert applied is False, '未超基线却注入了生产权重'
    assert config_dict['algorithms']['frequency_weighted']['weight'] == 0.55, \
        '生产权重被污染'
    return '无提升候选 → 未注入；建议文本含「公平摇号」声明'


def check_checkpoint_interrupt_safety(env, verbose):
    """9. 检查点中断安全：保存被中断时上一份完整、无 .tmp 残留。"""
    import unittest.mock as _mock

    env.eng._checkpoint = {'phase': 'baseline', 'tuned_lookback': 60}
    env.eng._save_checkpoint()

    def _raise_replace(*a, **k):
        raise OSError('模拟进程被杀')

    with _mock.patch('os.replace', _raise_replace):
        env.eng._checkpoint = {'phase': 'evaluate'}
        env.eng._save_checkpoint()          # 内部吞掉异常

    with open(env.ckpt_path, encoding='utf-8') as f:
        kept = json.load(f)                 # 不允许 JSONDecodeError
    assert kept['phase'] == 'baseline', '上一份检查点被破坏'
    assert kept['tuned_lookback'] == 60, '检查点字段丢失'

    leftovers = [f for f in os.listdir(env.dir) if f.endswith('.tmp')]
    assert leftovers == [], f'残留临时文件: {leftovers}'

    fresh = SelfEvolutionEngine(data_dir=env.dir)
    fresh._load_checkpoint()
    assert fresh._checkpoint.get('phase') == 'baseline', '续跑位置未保留'
    return '中断后旧检查点完整；无 .tmp 残留；续跑位置正确'


def check_state_files_integrity(env, verbose):
    """10. 状态文件完好性：中断后所有小状态文件仍为可解析 JSON。"""
    import unittest.mock as _mock

    def _raise_replace(*a, **k):
        raise OSError('模拟进程被杀')

    # 让三类状态文件都各写一次
    env.sched.mark_run()
    env.sched.report_db_failure()
    env.eng._checkpoint = {'phase': 'collect'}
    env.eng._save_checkpoint()

    # 再次写入并模拟中断
    with _mock.patch('os.replace', _raise_replace):
        env.sched.mark_run()
        env.sched.report_db_failure()
        env.eng._checkpoint = {'phase': 'done'}
        env.eng._save_checkpoint()

    files = [
        os.path.join(env.dir, 'self_evolution_schedule.json'),
        env.sched.db_fail_state_path,
        env.ckpt_path,
    ]
    broken = []
    for p in files:
        if not os.path.isfile(p):
            broken.append((os.path.basename(p), '缺失'))
            continue
        try:
            _read_json(p)
        except Exception as e:  # noqa: BLE001
            broken.append((os.path.basename(p), str(e)[:60]))
    assert not broken, f'状态文件损坏: {broken}'
    return 'schedule / db_fail / checkpoint 三份状态文件均完整可解析'


CHECKS = [
    check_analysis_linkage,
    check_verification_metrics,
    check_trigger_event,
    check_trigger_dedup,
    check_dedup_persistence,
    check_sample_gate,
    check_advisory_readonly,
    check_honest_boundary,
    check_checkpoint_interrupt_safety,
    check_state_files_integrity,
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='进化引擎联动链路端到端核验')
    parser.add_argument('-v', '--verbose', action='store_true', help='打印每项明细')
    args = parser.parse_args(argv)

    print('=' * 78)
    print('排列5 自我进化 · 分析 → 验证 → 触发 联动链路核验 (v5.0 §7.5)')
    print('=' * 78)

    failures = []
    for idx, check in enumerate(CHECKS, 1):
        env = Env()
        try:
            detail = check(env, args.verbose)
            status = 'PASS'
            failures.append(None)
        except Exception as exc:  # noqa: BLE001
            status = 'FAIL'
            detail = f'{type(exc).__name__}: {exc}'
            failures.append((check.__name__, detail))
            if args.verbose:
                traceback.print_exc()

        # 每项单独一个 Env，避免状态互相污染
        print(f'[{status}] {idx:>2}. {check.__name__}')
        if args.verbose:
            print(f'        → {detail}')
        elif status == 'FAIL':
            print(f'        → {detail}')

    print('-' * 78)
    total = len(CHECKS)
    ok = total - len([f for f in failures if f])
    print(f'结果: {ok}/{total} 通过', end='  ')
    if ok == total:
        print('✓ 全部通过')
        print()
        print('诚实边界：排列5 为公平摇号，任何算法均不能声称提升命中率；')
        print('本脚本只验证「联动链路的正确性与可观测性」，不验证预测准确性。')
        return 0
    print('✗ 存在失败')
    for name, detail in [f for f in failures if f]:
        print(f'  - {name}: {detail}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
