# -*- coding: utf-8 -*-
"""
tuning_config.py — 命中率调优实验配置加载器 + schema 校验器 (v3.16)

职责:
  1. 加载 tuning_config.yaml (结构镜像 P5PredictorConfig.DEFAULT_CONFIG 嵌套)
  2. 校验 schema: 7算法权重齐全且 sum≈1.0、窗口/阈值边界、类型正确
  3. 输出可直接喂给 P5PredictorConfig(custom_config) 的覆盖字典
  4. CLI: --validate / --emit-template / --to-custom

设计原则 (与排列5_命中率优化方案.md 一致):
  - 调优配置「禁止直接改」冻结 DEFAULT_CONFIG, 始终从冻结清单派生
  - 每次实验 = 本控制组模板的副本 + 单组参数改动 (控制变量)
  - 有效提升判定: 95%CI 下界 > 随机基线 且 跨≥3窗口稳健 (由回测脚本负责)

依赖: 仅 PyYAML (已装入托管 venv) + 标准库。不 import 预测器/matplotlib, 保证无重依赖可独立校验。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any, Dict, List, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("ERROR: PyYAML 未安装 (pip install pyyaml)\n")
    raise

EXPECTED_ALGOS = [
    "frequency_weighted",
    "omission_regression",
    "bayesian_inference",
    "trend_momentum",
    "markov_transition",
    "pattern_continuation",
    "feature_engineering",
]

WEIGHT_SUM_TOL = 1e-3
LOOKBACK_MIN, LOOKBACK_MAX = 30, 90
TOP_N_MIN, TOP_N_MAX = 1, 6


class TuningConfigError(Exception):
    """配置校验失败"""


def _control_template() -> Dict[str, Any]:
    """返回控制组(=冻结态)配置模板 — 派生自 v3.15 参数冻结清单"""
    return {
        "meta": {
            "experiment_id": "exp_freeze_control",
            "based_on": "v3.15_freeze",
            "author": "auto",
            "created": "2026-07-21",
            "note": "控制组; 派生自 排列5_v3.15参数冻结清单.md; AI已禁用以隔离统计信号",
        },
        "algorithms": {
            "frequency_weighted": {
                "enabled": True, "weight": 0.54,
                "params": {"lookback_periods": 60, "smoothing_factor": 0.1,
                           "recency_weight": False, "recency_decay": 0.03},
            },
            "omission_regression": {
                "enabled": True, "weight": 0.34,
                "params": {"max_omission_cap": 50, "regression_steepness": 0.018, "linear_bonus": True},
            },
            "bayesian_inference": {
                "enabled": True, "weight": 0.10,
                "params": {"prior_smooth": 0.10, "posterior_weight": 0.92, "verification_window": 60,
                           "penalize_miss": 0.68, "reward_hit": 1.40, "decay_half_life": 10,
                           "beta_alpha": 0.8, "prior_temporal_scale": 50, "min_verification_samples": 50},
            },
            "trend_momentum": {
                "enabled": True, "weight": 0.01,
                "params": {"trend_window": 30, "momentum_factor": 0.88},
            },
            "markov_transition": {
                "enabled": True, "weight": 0.005,
                "params": {"order": 1, "decay_factor": 0.92, "min_transition_prob": 0.02},
            },
            "pattern_continuation": {
                "enabled": True, "weight": 0.003,
                "params": {"pattern_window": 7, "continuation_boost": 1.12},
            },
            "feature_engineering": {
                "enabled": True, "weight": 0.002,
                "params": {"freq_weight": 0.30, "omission_weight": 0.25, "road_weight": 0.15,
                           "repeat_weight": 0.15, "consecutive_weight": 0.15},
            },
        },
        "global": {
            "position_top_n": 6,
            "enable_boundary_protection": False,
            "enable_adaptive_weights": False,
            "adaptive_metric": "top1_hit",
            "ewma_alpha": 0.3,
            "ewma_blend": 0.1,
            "minor_max_weight": 0.10,
            "enable_ai_model": False,
            "ai_model_weight": 0.1,
        },
        "quality_gates": {
            "predicted_numbers_format": "flat",
            "min_verification_samples": 50,
            "max_missing_rate": 0.02,
        },
    }


class TuningConfig:
    """调优实验配置: 加载 + 校验 + 转换"""

    def __init__(self, data: Dict[str, Any]):
        self.data = data

    # ---------------- 加载 ----------------
    @classmethod
    def load(cls, path: str) -> "TuningConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise TuningConfigError(f"配置文件根须为映射, 实为 {type(data).__name__}")
        return cls(data)

    # ---------------- 校验 ----------------
    def validate(self) -> Tuple[bool, List[str]]:
        """返回 (ok, errors)。ok=False 时 errors 含所有问题。"""
        errors: List[str] = []
        d = self.data

        # meta
        meta = d.get("meta")
        if not isinstance(meta, dict):
            errors.append("meta 缺失或非映射")
        else:
            eid = meta.get("experiment_id")
            if not isinstance(eid, str) or not eid.strip():
                errors.append("meta.experiment_id 须为非空字符串")

        # algorithms
        algos = d.get("algorithms")
        if not isinstance(algos, dict):
            errors.append("algorithms 缺失或非映射")
        else:
            missing = [a for a in EXPECTED_ALGOS if a not in algos]
            if missing:
                errors.append(f"algorithms 缺算法: {missing}")
            extra = [a for a in algos if a not in EXPECTED_ALGOS]
            if extra:
                errors.append(f"algorithms 含未知算法: {extra}")
            total_w = 0.0
            for name in EXPECTED_ALGOS:
                blk = algos.get(name)
                if not isinstance(blk, dict):
                    errors.append(f"algorithms.{name} 非映射")
                    continue
                w = blk.get("weight")
                if not isinstance(w, (int, float)) or isinstance(w, bool):
                    errors.append(f"algorithms.{name}.weight 须为数值, 实为 {w!r}")
                elif not (0.0 <= float(w) <= 1.0):
                    errors.append(f"algorithms.{name}.weight={w} 超出 [0,1]")
                else:
                    total_w += float(w)
            if abs(total_w - 1.0) > WEIGHT_SUM_TOL:
                errors.append(f"七算法权重和={total_w:.6f}, 须≈1.0 (容差 {WEIGHT_SUM_TOL})")

            # lookback 边界
            lb = algos.get("frequency_weighted", {}).get("params", {}).get("lookback_periods")
            if lb is not None:
                if not isinstance(lb, (int, float)) or isinstance(lb, bool):
                    errors.append(f"frequency_weighted.params.lookback_periods 须为整数, 实为 {lb!r}")
                elif not (LOOKBACK_MIN <= int(lb) <= LOOKBACK_MAX):
                    errors.append(f"lookback_periods={lb} 超出 [{LOOKBACK_MIN},{LOOKBACK_MAX}]")

        # global
        g = d.get("global")
        if not isinstance(g, dict):
            errors.append("global 缺失或非映射")
        else:
            for flag in ("enable_boundary_protection", "enable_adaptive_weights", "enable_ai_model"):
                v = g.get(flag)
                if v is not None and not isinstance(v, bool):
                    errors.append(f"global.{flag} 须为布尔, 实为 {v!r}")
            topn = g.get("position_top_n")
            if topn is not None:
                if not isinstance(topn, int) or isinstance(topn, bool):
                    errors.append(f"global.position_top_n 须为整数, 实为 {topn!r}")
                elif not (TOP_N_MIN <= topn <= TOP_N_MAX):
                    errors.append(f"global.position_top_n={topn} 超出 [{TOP_N_MIN},{TOP_N_MAX}]")

        # quality_gates
        qg = d.get("quality_gates")
        if qg is not None:
            if not isinstance(qg, dict):
                errors.append("quality_gates 须为映射")
            else:
                mvs = qg.get("min_verification_samples")
                if mvs is not None and (not isinstance(mvs, int) or isinstance(mvs, bool) or mvs < 0):
                    errors.append(f"quality_gates.min_verification_samples 须为非负整数, 实为 {mvs!r}")
                mmr = qg.get("max_missing_rate")
                if mmr is not None and (not isinstance(mmr, (int, float)) or isinstance(mmr, bool)
                                        or not (0.0 <= float(mmr) <= 1.0)):
                    errors.append(f"quality_gates.max_missing_rate 须∈[0,1], 实为 {mmr!r}")

        return (len(errors) == 0, errors)

    # ---------------- 转换 ----------------
    def to_predictor_custom_config(self) -> Dict[str, Any]:
        """导出 P5PredictorConfig 兼容的覆盖字典 (仅 algorithms + global)"""
        out: Dict[str, Any] = {}
        if "algorithms" in self.data:
            out["algorithms"] = copy.deepcopy(self.data["algorithms"])
        if "global" in self.data:
            out["global"] = copy.deepcopy(self.data["global"])
        return out

    def fingerprint(self) -> Dict[str, Any]:
        algos = self.data.get("algorithms", {})
        return {
            "experiment_id": self.data.get("meta", {}).get("experiment_id"),
            "based_on": self.data.get("meta", {}).get("based_on"),
            "algo_weights": {k: algos.get(k, {}).get("weight") for k in EXPECTED_ALGOS},
            "freq_lookback": algos.get("frequency_weighted", {}).get("params", {}).get("lookback_periods"),
            "enable_adaptive_weights": self.data.get("global", {}).get("enable_adaptive_weights"),
            "enable_ai_model": self.data.get("global", {}).get("enable_ai_model"),
        }


# ---------------- CLI ----------------
def _cmd_validate(args) -> int:
    try:
        tc = TuningConfig.load(args.path)
    except (TuningConfigError, OSError, yaml.YAMLError) as e:
        print(f"[FAIL] 加载失败: {e}")
        return 1
    ok, errors = tc.validate()
    if ok:
        print(f"[OK] {args.path} 校验通过")
        print("  指纹:", json.dumps(tc.fingerprint(), ensure_ascii=False))
        return 0
    print(f"[FAIL] {args.path} 校验未通过 ({len(errors)} 项):")
    for e in errors:
        print(f"  - {e}")
    return 1


def _cmd_emit_template(args) -> int:
    tpl = _control_template()
    with open(args.path, "w", encoding="utf-8") as f:
        yaml.safe_dump(tpl, f, allow_unicode=True, sort_keys=False)
    print(f"[OK] 控制组模板已写出: {args.path}")
    return 0


def _cmd_to_custom(args) -> int:
    tc = TuningConfig.load(args.path)
    ok, errors = tc.validate()
    if not ok:
        print("[FAIL] 校验未通过, 拒绝导出:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(json.dumps(tc.to_predictor_custom_config(), ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="tuning_config 校验/模板工具 (v3.16)")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="校验 tuning_config.yaml 是否符合 schema")
    v.add_argument("path", help="配置文件路径")
    v.set_defaults(func=_cmd_validate)

    e = sub.add_parser("emit-template", help="写出控制组模板(=冻结态)")
    e.add_argument("path", help="输出路径")
    e.set_defaults(func=_cmd_emit_template)

    c = sub.add_parser("to-custom", help="导出 P5PredictorConfig 兼容覆盖字典(仅校验通过后)")
    c.add_argument("path", help="配置文件路径")
    c.set_defaults(func=_cmd_to_custom)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
