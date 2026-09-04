"""
排列5 系统健康诊断模块（v1.0）

【目标】
通过离线采集历史预测记录 + 开奖结果，构建一套自动化的「系统性健康诊断」引擎：
  - 命中率基线校准（诚实对照：Top-1≈10%, Top-3≈30%, Top-5≈50%）
  - 七算法 per-algo 命中率时序追踪
  - 权重漂移检测（是否已偏离 v3.60 冻结权重）
  - 深度调优历史版本对比（active vs trial）
  - 在线学习闭环归因覆盖率监控
  - 输出结构化诊断报告 + 可执行的优化建议清单

【对外主入口】
  run_diagnostic(days=60, limit=200) -> Dict (JSON 诊断报告)
  render_markdown(report) -> str        # 可读 Markdown
"""

import os
import sys
import json
import logging
import datetime
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# v3.60 冻结基线权重（仅统计类，ml_supervised 在 tune 中被冻结）
FROZEN_WEIGHTS = {
    'frequency_weighted': 0.68,
    'bayesian_inference': 0.10,
    'omission_regression': 0.06,
    'trend_momentum': 0.01,
    'markov_transition': 0.005,
    'pattern_continuation': 0.003,
    'feature_engineering': 0.002,
}

# 诚实随机基线 (%)
BASELINE_TOP1 = 10.0
BASELINE_TOP3 = 30.0
BASELINE_TOP5 = 50.0

# 与 online_learner 对齐：仅统计 v3.44 修复后创建的记录
ATTRIBUTION_DEPLOY_DATE = '2026-08-05'


def _connect_db():
    """按需连接数据库，返回 P5Database 实例；失败返回 None。"""
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from modules.database import P5Database
        db = P5Database()
        if not db.connect():
            return None
        return db
    except Exception as e:
        logger.warning('[system_health] DB 连接失败: %s', e)
        return None


def _health_level(score: float) -> str:
    if score >= 80:
        return 'healthy'
    if score >= 50:
        return 'degraded'
    return 'critical'


def _pct_delta(actual: float, baseline: float) -> float:
    return round(actual - baseline, 3)


def collect_verification_samples(db, days: int = 60, limit: int = 200) -> List[Dict[str, Any]]:
    """拉取近期已验证预测记录（含实际开奖回填）。"""
    try:
        rows = db.get_verified_predictions(
            days=days, limit=limit, min_created_at=ATTRIBUTION_DEPLOY_DATE)
        return rows or []
    except Exception as e:
        logger.warning('[system_health] 拉取验证记录失败: %s', e)
        return []


def _safe_pct(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except Exception:
        return default


def build_diagnostic_report(db, days: int = 60, limit: int = 200) -> Dict[str, Any]:
    """生成「系统健康诊断报告」。

    返回 JSON 结构：
    {
        'ts': str,
        'status': 'healthy'|'degraded'|'critical',
        'summary': str,
        'metrics': {...},
        'suggestions': List[str],
        'versions': List[Dict],
        'attribution_coverage': Dict,
        '_paths': {'json': ..., 'markdown': ...},
    }
    """
    from modules.online_learner import OnlineLearner

    report: Dict[str, Any] = {
        'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'status': 'unknown',
        'summary': '',
        'metrics': {},
        'suggestions': [],
        'versions': [],
        'attribution_coverage': {},
    }

    # ================================================================
    # 1. 命中率评估（诚实对照）
    # ================================================================
    samples = collect_verification_samples(db, days=days, limit=limit)
    total = len(samples)
    if not total:
        report['status'] = 'critical'
        report['summary'] = '无可用验证样本，无法评估系统健康度'
        report['suggestions'].append('请先等待近期开奖回填（需至少 10 期以上验证记录）')
        return report

    top1_hits = top3_hits = top5_hits = 0
    verified = 0
    per_algo_accum: Dict[str, Dict[str, int]] = {
        a: {'hits': 0, 'total': 0} for a in FROZEN_WEIGHTS
    }
    pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']

    def _as_numbers(raw) -> List[int]:
        """把 DB 行的 actual_numbers 列（可能是 JSON 字符串 / 原生列表 / None）
        规范化为 [int, ...] 5 元组。失败则返回空列表，保持下游 if not actual 的跳过语义。"""
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            out = []
            for x in raw:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, (list, tuple)) and len(parsed) >= 5:
                    return [_as_numbers(parsed)[i] for i in range(5)]
            except Exception:
                pass
            # 兼容纯数字字符串 "12345"
            stripped = raw.strip()
            if len(stripped) == 5 and stripped.isdigit():
                return [int(c) for c in stripped]
        return []

    def _as_int_list(raw) -> List[int]:
        """把某位置的实际号码字段（int / str / list[1]）统一为 [int]，失败返回 []。"""
        if raw is None:
            return []
        if isinstance(raw, list):
            out = []
            for x in raw:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out
        try:
            return [int(raw)]
        except Exception:
            return []

    for row in samples:
        actual = _as_numbers(row.get('actual_numbers'))
        if len(actual) < 5:
            continue
        verified += 1

        # fused 口径命中：依赖 pipeline._register_prediction_for_verification
        # 在 p5_prediction_record.predicted_numbers 写入扁平化后的每位置 Top-N 列表。
        pred_nums = row.get('predicted_numbers')
        flat_fused: Dict[str, List[int]] = {}
        if isinstance(pred_nums, str):
            try:
                flat_fused = json.loads(pred_nums) or {}
            except Exception:
                flat_fused = {}
        elif isinstance(pred_nums, dict):
            flat_fused = pred_nums

        # 若 DB 中该列未写入或格式不对，退化为通过 p5_ai_report 的 recommended_numbers
        if not flat_fused:
            ru = row.get('report_uuid')
            if ru:
                try:
                    db.cursor.execute(
                        "SELECT recommended_numbers FROM p5_ai_report "
                        "WHERE report_uuid=%s LIMIT 1", (ru,))
                    rec = db.cursor.fetchone() or {}
                    rn = rec.get('recommended_numbers')
                    if isinstance(rn, str):
                        try:
                            rn = json.loads(rn) or {}
                        except Exception:
                            rn = {}
                    if isinstance(rn, dict):
                        flat_fused = rn
                except Exception:
                    pass

        if isinstance(flat_fused, dict):
            for i, pk in enumerate(pos_keys):
                n = actual[i]
                picks = flat_fused.get(pk) or []
                if not isinstance(picks, (list, tuple)):
                    continue
                cand3 = [int(x) for x in picks[:3]]
                cand5 = [int(x) for x in picks[:5]]
                if not cand3:
                    continue
                if n == cand3[0]:
                    top1_hits += 1
                if n in cand3:
                    top3_hits += 1
                if n in cand5:
                    top5_hits += 1

        # per-algo 归因口径命中
        per_algo = row.get('per_algo_top_predictions') or {}
        if not isinstance(per_algo, dict):
            per_algo = {}
        for algo, pos_preds in per_algo.items():
            if algo not in per_algo_accum:
                continue
            if not isinstance(pos_preds, dict):
                continue
            for i, pk in enumerate(pos_keys):
                preds = pos_preds.get(pk) or pos_preds.get(i) or pos_preds.get(str(i))
                if not isinstance(preds, (list, tuple)) or not preds:
                    continue
                int_preds = []
                for x in preds:
                    try:
                        int_preds.append(int(x))
                    except Exception:
                        pass
                if not int_preds:
                    continue
                per_algo_accum[algo]['total'] += 1
                if i < len(actual) and actual[i] in int_preds[:3]:
                    per_algo_accum[algo]['hits'] += 1

    denom = verified * 5 or 1
    metrics = {
        'verified_samples': verified,
        'top1_rate_pct': round(top1_hits / denom * 100, 3),
        'top3_rate_pct': round(top3_hits / denom * 100, 3),
        'top5_rate_pct': round(top5_hits / denom * 100, 3),
        'vs_baseline': {
            'top1_delta': _pct_delta(top1_hits / denom * 100, BASELINE_TOP1),
            'top3_delta': _pct_delta(top3_hits / denom * 100, BASELINE_TOP3),
            'top5_delta': _pct_delta(top5_hits / denom * 100, BASELINE_TOP5),
        },
        'per_algo_top3_hitrate': {},
    }
    for algo, acc in per_algo_accum.items():
        if acc['total']:
            metrics['per_algo_top3_hitrate'][algo] = round(acc['hits'] / acc['total'], 4)

    report['metrics'] = metrics

    # ================================================================
    # 2. 权重漂移检测
    # ================================================================
    drifts: List[Dict[str, Any]] = []
    try:
        from modules.predictor import P5Predictor
        p = P5Predictor()
        cfg = getattr(p, 'config', None)
        algo_weights = {}
        if cfg is not None:
            if isinstance(cfg, dict):
                algo_weights = cfg.get('algorithms', {}) or {}
            else:
                algo_weights = getattr(cfg, 'algorithms', {}) or {}
        for algo, details in algo_weights.items():
            if not isinstance(details, dict) or 'weight' not in details:
                continue
            cur = float(details['weight'])
            ref = FROZEN_WEIGHTS.get(algo)
            if ref is None:
                continue
            delta = cur - ref
            if abs(delta) > 1e-4:
                drifts.append({
                    'algo': algo,
                    'current': cur,
                    'baseline': ref,
                    'delta': round(delta, 5),
                })
    except Exception as e:
        logger.warning('[system_health] 权重漂移检测异常: %s', e)
        drifts = [{'error': str(e)}]

    report['metrics']['weight_drifts'] = drifts

    # ================================================================
    # 3. 版本对比（active / 最优 trial）
    # ================================================================
    try:
        from modules.self_evolution import SelfEvolutionEngine
        evo = SelfEvolutionEngine(auto=False, auto_full=False)
        versions = evo.get_versions(limit=20)
        active = [v for v in versions if v.get('status') == 'active']
        trials = [v for v in versions if v.get('status') == 'trial']
        best_trial = None
        best_score = -1.0
        from modules.evolution_tuner import _score
        for v in trials:
            m = v.get('metrics') or {}
            s = _score(m)
            if s > best_score:
                best_score = s
                best_trial = v
        report['metrics']['version_summary'] = {
            'total_tracked': len(versions),
            'active_count': len(active),
            'trial_count': len(trials),
            'best_trial_score': round(best_score, 4) if best_trial else None,
            'best_trial_metrics': best_trial.get('metrics') if best_trial else None,
        }
        report['versions'] = versions[:10]
    except Exception as e:
        logger.warning('[system_health] 版本读取异常: %s', e)

    # ================================================================
    # 4. 在线学习闭环归因覆盖率
    # ================================================================
    try:
        learner = OnlineLearner(db)
        cov = learner.estimate_attribution_coverage(
            db, days=days, limit=max(limit, 200))
        report['attribution_coverage'] = cov
    except Exception as e:
        logger.warning('[system_health] 归因覆盖率异常: %s', e)

    # ================================================================
    # 5. 健康评分 & 建议聚合
    # ================================================================
    score = 0.0
    suggestions: List[str] = []

    t1d = metrics['vs_baseline']['top1_delta']
    t3d = metrics['vs_baseline']['top3_delta']
    if -5 <= t1d <= 5 and -5 <= t3d <= 5:
        score += 40
        suggestions.append(
            '[命中基线] Top1/Top3 均在 ±5pp 随机噪声窗口内，符合诚实边界')
    elif t1d < -5 or t3d < -5:
        score += 10
        suggestions.append(
            '[严重警告] 命中率显著低于随机基线，请排查数据完整性与验证回填逻辑')
    else:
        score += 25
        suggestions.append(
            '[注意] 命中率小幅超越随机基线，继续观察趋势。'
            '排列5为公平摇号，长期不应稳定超越基线')

    bad_drifts = [d for d in drifts if 'error' not in d and abs(d.get('delta', 0)) > 0.01]
    if bad_drifts:
        score -= len(bad_drifts) * 10
        for d in bad_drifts:
            suggestions.append(
                f"[权重漂移] {d['algo']}: "
                f"当前 {d['current']:.4f} vs 冻结 {d['baseline']:.4f} "
                f"(δ={d['delta']:.4f})，建议核查 AdaptiveWeightManager 更新通道")
    else:
        score += 20

    cov = report.get('attribution_coverage') or {}
    cover = cov.get('coverage')
    if cover is not None and cover >= 0.8:
        score += 20
        suggestions.append(
            f'[学习闭环] 归因覆盖率 {cover*100:.1f}%，验证→学习通道正常')
    elif cover is not None:
        score += 5
        suggestions.append(
            f'[学习闭环] 归因覆盖率偏低 {cover*100:.1f}%，请检查 '
            'per_algo_predictions 字段是否写入')
    else:
        score += 10
        suggestions.append('[学习闭环] 归因覆盖率未初始化，仅统计近期样本')

    vs = metrics.get('version_summary', {})
    if vs.get('active_count', 0) == 0:
        score -= 10
        suggestions.append(
            '[版本] 当前无 active 进化版本，生产预测使用默认冻结权重')
    elif vs.get('best_trial_score') and vs['best_trial_score'] > 0:
        score += 10
        suggestions.append(
            f'[进化版本] 最优 trial 综合评分 {vs["best_trial_score"]:.4f}，'
            '尚未超越 active 基线')

    score = max(0, min(100, score))
    report['status'] = _health_level(score)
    report['summary'] = (
        f'样本量={verified} 期 | '
        f'Top1={metrics["top1_rate_pct"]:.2f}% (Δ{t1d:+.2f}pp) | '
        f'Top3={metrics["top3_rate_pct"]:.2f}% (Δ{t3d:+.2f}pp) | '
        f'权重漂移={len(bad_drifts)} 项 | '
        f'健康分={score}'
    )
    report['suggestions'] = suggestions
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        '# 排列5 系统健康诊断报告',
        f'**生成时间**：{report.get("ts", "")}',
        f'**健康状态**：{report.get("status", "")}',
        f'**摘要**：{report.get("summary", "")}',
        '',
        '## 命中率指标（诚实对照）',
        '| 指标 | 实测 | 随机基线 | 偏差 |',
        '|---|---|---|---|',
    ]
    m = report.get('metrics', {})
    base = m.get('vs_baseline', {})
    lines.append(f"| Top1 | {m.get('top1_rate_pct')}% | {BASELINE_TOP1}% | {base.get('top1_delta', 0):+.2f}pp |")
    lines.append(f"| Top3 | {m.get('top3_rate_pct')}% | {BASELINE_TOP3}% | {base.get('top3_delta', 0):+.2f}pp |")
    lines.append(f"| Top5 | {m.get('top5_rate_pct')}% | {BASELINE_TOP5}% | {base.get('top5_delta', 0):+.2f}pp |")
    lines.append('')

    lines.append('## 各算法 Top-3 命中率')
    lines.append('| 算法 | Top-3 命中率 |')
    lines.append('|---|---|')
    for algo, hr in (m.get('per_algo_top3_hitrate') or {}).items():
        lines.append(f'| {algo} | {hr:.4f} |')
    lines.append('')

    drifts = m.get('weight_drifts', [])
    if drifts:
        lines.append('## 权重漂移')
        for d in drifts:
            if 'error' in d:
                lines.append(f'- ⚠️ {d["error"]}')
            else:
                lines.append(
                    f'- {d["algo"]}: '
                    f'{d["current"]:.4f} → {d["baseline"]:.4f} '
                    f'(δ={d["delta"]:.4f})')
        lines.append('')

    cov = report.get('attribution_coverage', {})
    lines.append('## 在线学习闭环')
    lines.append(f'- 归因覆盖率: {cov.get("coverage")}')
    lines.append(
        f'- 有效采样期数: {cov.get("sampled")} '
        f'(排除历史 NULL 归因 {cov.get("excluded_pre_fix")} 期)')
    lines.append('')

    lines.append('## 优化建议')
    for s in report.get('suggestions', []):
        lines.append(f'- {s}')
    lines.append('')
    lines.append('---')
    lines.append(
        '> 【风险提示】排列五开奖为完全随机的概率事件，历史数据不影响未来开奖结果，'
        '本系统所有统计分析与模拟号码仅供娱乐与学术研究，不构成任何购彩建议。'
        '请理性购彩，量力而行。')
    return '\n'.join(lines)


def run_diagnostic(days: int = 60, limit: int = 200,
                   data_dir: Optional[str] = None) -> Dict[str, Any]:
    """对外主入口：生成 JSON + Markdown 双格式诊断报告。

    参数:
        days:     统计最近 N 天的验证样本
        limit:    最大样本条数
        data_dir: 报告落盘目录（None 时回退到 paths.PROJECT_ROOT/data）

    返回:
        诊断报告字典（含 _paths 指向 JSON/Markdown 落盘路径）
    """
    db = _connect_db()
    if db is None:
        logger.error('[system_health] DB 不可用，诊断失败')
        return {'status': 'error', 'error': 'database_unavailable'}
    try:
        report = build_diagnostic_report(db, days=days, limit=limit)
        if data_dir is None:
            try:
                from paths import PROJECT_ROOT
                data_dir = os.path.join(PROJECT_ROOT, 'data')
            except Exception:
                data_dir = os.path.join(os.getcwd(), 'data')
        os.makedirs(data_dir, exist_ok=True)
        tag = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        json_path = os.path.join(data_dir, f'system_health_{tag}.json')
        md_path = os.path.join(data_dir, f'system_health_{tag}.md')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(render_markdown(report))
        logger.info('[system_health] 报告已保存: %s / %s', json_path, md_path)
        report['_paths'] = {'json': json_path, 'markdown': md_path}
        return report
    finally:
        try:
            db.disconnect()
        except Exception:
            pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='排列5 系统健康诊断')
    parser.add_argument('--days', type=int, default=60)
    parser.add_argument('--limit', type=int, default=200)
    args = parser.parse_args()
    r = run_diagnostic(days=args.days, limit=args.limit)
    print(json.dumps(r, ensure_ascii=False, indent=2))
