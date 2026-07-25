# -*- coding: utf-8 -*-
"""
version.py — 程序版本与变更日志（唯一版本信息源）

为什么存在这个文件：
    旧代码里版本号散落在 gui.py(APP_VERSION)、predictor.py 文档、pipeline.py 产物
    model_version 字符串、online_learner.py 的 'version' 字段等多处，彼此冲突
    （v2.0 / v3.0 / v3.2 / v3.3 / v3.8 并存），且没有统一的「更新日志 / 新增功能 /
    已知问题」数据源。GUI 也无法在版本变更时自动刷新。

本模块作为「单一事实来源（Single Source of Truth）」：
    - APP_VERSION / APP_NAME：对外展示的版本号与程序名（GUI、报告、产物统一引用）。
    - CHANGELOG：从新到旧的版本变更记录（含 date / summary / features / fixes）。
    - KNOWN_ISSUES：已知问题 / 待修复清单。
    - load_version_info()：聚合返回给 GUI 渲染；支持外部 data/changelog.json 覆盖，
      便于非开发者更新日志而无需改代码。

GUI 版本展示：
    本模块作为版本单一来源；gui.py 在顶部版本标签与欢迎页读取 APP_VERSION /
    CHANGELOG 进行展示。「版本与更新」独立标签页已移除（避免冗余 UI 与轮询开销）。
"""

import os
import json
import importlib

# ---------------------------------------------------------------------------
# 基础版本信息
# ---------------------------------------------------------------------------
APP_NAME = "排列5 AI智能分析系统"
APP_VERSION = "v3.16"
APP_RELEASE_DATE = "2026-07-24"

# ---------------------------------------------------------------------------
# 变更日志（从新到旧）
# 每条记录字段：
#   version  : 版本号（字符串，与 APP_VERSION 对齐）
#   date     : 发布日期（YYYY-MM-DD）
#   summary  : 一句话概述
#   features : 新增功能列表
#   fixes    : 已知问题修复列表
# ---------------------------------------------------------------------------
CHANGELOG = [
    {
        "version": "v3.16",
        "date": "2026-07-24",
        "summary": "项目结构优化：移除专家文章工具，文件归类整理，右侧面板全面优化。",
        "features": [
            "移除 GUI 中「专家文章工具」卡片及相关执行方法（批量保存/处理文章）。",
            "移除 CLI 命令 save-articles / process-article / process-articles。",
            "删除冗余模块：article_handler.py、web_scraper.py、html_utils.py。",
            "文件归类整理：opt_* / build_* 脚本移至 scripts/，test_* 文件移至 tests/。",
            "右侧面板信息架构重组：概览卡片区、结果仪表盘区、日志文本区三层结构。",
            "视觉设计优化：增强色彩方案（亮色变体）、卡片化设计、优化字体大小与行高。",
            "交互体验增强：按钮悬停效果、加载动画（动态圆点）、鼠标滚轮平滑滚动。",
            "工具栏按钮重构：支持 primary/secondary/danger 三种样式类型。",
            "文本高亮标签增强：新增 number、section_divider 标签，优化颜色对比度。",
            "概览栏卡片化：最新预测、验证统计、历史数据量分区展示，带颜色区分。",
            "GUI功能模块完整集成：新增在线学习引擎卡片（学习报告、重置权重、手动验证）。",
            "GUI功能模块完整集成：新增分析工具卡片（预测验证、命中率报告、性能报告、历史回测、特征分析）。",
            "复制预测号码功能优化：确保复制算法预测结果，包含号码段范围、算法流程、逐位来源、置信度信息。",
            "智能分析预测结果展示完善：新增算法分析依据折叠区，展示算法模型、聚合策略、各位置来源。",
            "视觉交互增强：号码芯片悬停效果、卡片边框与间距优化、字体大小统一调整。",
            "预测结果仪表盘优化：标题栏改进、期号信息增强、置信度徽章优化、备选号码区域美化。",
        ],
        "fixes": [
            "更新 AGENTS.md / README.md 文档，移除对已删除文件的引用。",
            "修复 test_double_reports.py 中 report_type 参数位置断言错误。",
            "修复复制预测结果功能：确保复制内容包含各位置所有5个候选号码，格式为\"万位: 5 1 0 3 4\"。",
        ],
    },
    {
        "version": "v3.14",
        "date": "2026-07-19",
        "summary": "自适应权重双信号架构：Top-1 精准度通道 + 覆盖率通道并存，默认关闭。",
        "features": [
            "AdaptiveWeightManager 新增 Top-1 精准度 EWMA 通道（ewma_t1、t1_total/t1_hits），与原覆盖率通道 (ewma) 并行独立累积互不污染。",
            "record_verification(algo, hit_rate, top1_hit=None) 支持可缺省的第三参数，兼容旧调用方；旧记录回放只需走旧通道。",
            "get_adaptive_weights(metric='top1_hit'|'hit_rate'|'hybrid') 三通道可读，metric 自动降级（top1 无数据→hit_rate）。",
            "load_from_records 双格式兼容：旧 records 只含 algo_evaluations 时走 hit_rate，含 algo_evaluations_t1 时双通道并累积。",
            "P5PredictorConfig 支持 adaptive_metric 全局开关（默认 'top1_hit'）；get_algorithm_weights 按此开关选 ewma 字段。",
            "pipeline._calculate_algorithm_hits 同时计算 hit_rate + top1_hit_rate（每位置查 pred_nums[0] 是否命中），双信号一起写入 weight_updates。",
            "pipeline._update_weight_manager 双信号调用 record_algo_hit(hit_rate, top1_hit)。",
            "OnlineLearner.record_algo_hit 第三参数 top1_hit（可选）已支持。",
        ],
        "fixes": [
            "诊断实验：opt_top1_weighted_signal.py 60 期 walk-forward 验证 Top-1 精准度 CV=0.47（覆盖率 CV=0.13，3.7x 提升），EWMA 能正确选出 frequency_weighted 为最优。",
            "验证回测：opt_v314_dual_signal.py 30 期学习 + 50 期评测对 A 静态基线 Top-1=9.6% / Top-6=59.2%；B 自适应 Top-1=8.8%（退化 -0.8%）。",
            "决策：默认仍 disable 自适应，等真生产 500+ 期再验证；同时保留双信号能力与切换接口。",
            "回退 smoke test 通过，确保所有调用契约与默认值与上述一致。",
        ],
    },
    {
        "version": "v3.13",
        "date": "2026-07-18",
        "summary": "实验关闭自适应权重（验证后放弃 hit_rate 信号），保留 v3.12 静态默认作基线。",
        "features": [],
        "fixes": [
            "AdaptiveWeightManager 关闭（实验证明 7 算法在覆盖率指标上无区分信号，自适应把权重稀释至 9.5% Top-1）。",
            "保留 v3.12 静态默认作 Top-1=11.25-11.75% 的已知最优。",
        ],
    },
    {
        "version": "v3.12",
        "date": "2026-07-18",
        "summary": "四层优化（算法/策略/流程/自学习），命中率全面超过随机基线。",
        "features": [
            "算法层：频率加权 0.35→0.54、遗漏回归 0.25→0.34、贝叶斯 0.15→0.10；次要算法权重压至极小（实测其稀释主信号）。",
            "策略层：AdaptiveWeightManager 默认值对齐 v3.12 并补齐 feature_engineering 项；EWMA 融合 w=0.7*w+0.3*ewma。",
            "流程层：移除破坏性的切比雪夫距离 + 位置方差边界保护（将概率压平趋向均匀、抑制信号），默认关闭边界保护，保留相邻差惩罚。",
            "自学习层：修复断链——P5Predictor 改为 _load_adaptive_weight_history() 读取 p5_artifact(type='weight_history')，EWMA 跨进程生效。",
            "移除外部每日定时任务（scripts/daily_job.py 与对应自动化）及冗余调度依赖，数据爬取/验证改为按需触发。",
        ],
        "fixes": [
            "回测验证（walk-forward，AI 关闭）：80 期综合得分 9.35→10.95、Top-1 9.0%→11.4%；150 期 8.99→10.45。",
            "三项命中指标（Top-1/3/5）均首次超过随机基线（T1=10% / T3=30% / T5=50%）。",
        ],
    },
    {
        "version": "v3.11",
        "date": "2026-07-17",
        "summary": "新增「综合验证与分析」一键按钮，串联验证→统计→回测→特征→报告。",
        "features": [
            "智能分析与验证卡片下新增综合按钮，单次点击完成全流程分析与验证。",
        ],
        "fixes": [],
    },
    {
        "version": "v3.10",
        "date": "2026-07-16",
        "summary": "修复 GUI 四类运行时异常。",
        "features": [],
        "fixes": [
            "手动验证兼容旧格式 predicted_numbers（修复 'invalid literal for int()'）。",
            "历史回测移除 task_mgr.root 无效调用（修复 'TaskManager' object has no attribute 'root'）。",
            "特征分析移除重复 import 导致的局部变量遮蔽（'cannot access local variable P5Database'）。",
            "命中率报告增加除零保护。",
        ],
    },
    {
        "version": "v3.9",
        "date": "2026-07-15",
        "summary": "修复点击「四步流水线分析」后 GUI 卡死无响应。",
        "features": [],
        "fixes": [
            "轮询间隔 50ms→200ms，每次最多处理 10 条消息。",
            "减少 see(tk.END) 调用；日志行数上限 500 并自动清理早期日志。",
            "步骤校验信息改为仅在失败/警告时输出。",
        ],
    },
    {
        "version": "v3.8",
        "date": "2026-07-13",
        "summary": "GUI 信息面板重构 + 统一版本源，引入实时刷新与富文本/搜索能力。",
        "features": [
            "GUI 右侧面板拆分为「运行日志」与「版本与更新」双标签页（ttk.Notebook）。",
            "「版本与更新」面板支持：当前版本号、更新日志、新增功能、已知问题四类内容。",
            "版本/日志内容支持实时刷新：版本号变更时无需重启界面自动重绘。",
            "引入轻量 Markdown 渲染（标题/列表/加粗/代码/分隔线）提升可读性。",
            "新增内容搜索过滤框，按关键词过滤更新日志与功能列表。",
            "新增分类折叠（Treeview）与标签页切换，信息层级更清晰。",
            "新增 version.py 作为唯一版本信息源，消除散落的版本字符串冲突。",
        ],
        "fixes": [
            "统一 gui.py / predictor.py / pipeline.py / online_learner.py 中的版本字符串来源。",
            "修复 GUI 欢迎页误称「六算法融合」（实际为 7 算法 + AI 再包装）。",
            "在线学习权重自适应此前为「只打印不回写」的空操作，已明确标注为示意逻辑。",
        ],
    },
    {
        "version": "v3.3",
        "date": "2026-07-09",
        "summary": "增量爬取数据完整性修复 + 四步流水线预测报告/显示修复。",
        "features": [
            "走势图预测融合多源（历史/走势/独立位表）各 30 期，加权打分。",
            "号码段由 Top-6 压缩到 Top-4，提升预测聚焦度。",
            "拆分独立报告：专家文章预测报告 + 走势图数据预测报告，互不依赖。",
            "预测统计产物缓存复用（同最新期号且数据量未变时跳过 AI 调用）。",
        ],
        "fixes": [
            "修复增量爬取只爬历史数据、未同步爬走势数据（万千百十个位）的问题。",
            "修复专家文章报告预测为空（step1 未回写 ai_analysis）。",
            "修复最终/40 期报告预测为空（step4 写入键缺失）。",
            "修复 GUI 缺专家文章预测（step1 写入 state 供 step4 填充）。",
        ],
    },
    {
        "version": "v3.1",
        "date": "2026-07-06",
        "summary": "命中率优化：扩展预测覆盖 + 容错匹配 + 独立报告拆分。",
        "features": [
            "预测覆盖扩展：position_top_n 3→5（覆盖率 30%→50%）。",
            "容错匹配机制：允许号码偏差 ±1 也算命中。",
            "独立报告生成：专家文章预测报告 + 走势图数据预测报告。",
            "流水线集成：预测验证、在线学习、回测、特征分析统一接入。",
        ],
        "fixes": [
            "修复严格匹配命中率偏低的问题（实测案例 +100% 容错命中提升）。",
        ],
    },
    {
        "version": "v3.0",
        "date": "2026-07-04",
        "summary": "预测算法权重优化：新增贝叶斯推断 + 自适应权重管理器。",
        "features": [
            "新增贝叶斯推断算法（10% 权重）。",
            "自适应权重管理器（AdaptiveWeightManager）+ EWMA 平滑。",
            "组合生成约束：SSD 惩罚、跨度约束(3-8)、切比雪夫距离检查。",
            "参数优化：遗漏陡度 0.025→0.020，动量系数 1.0→0.9，马尔可夫衰减 0.95→0.93。",
        ],
        "fixes": [],
    },
    {
        "version": "v2.1",
        "date": "2026-07-01",
        "summary": "预测器配置优化（方案A）：降低 AI 权重、提升频率/遗漏权重。",
        "features": [
            "频率加权 25%→35%，遗漏回归 20%→25%，AI 模型 40%→10%。",
            "趋势窗口 10→30 期，回归陡度 0.08→0.025，动量系数 1.2→1.0。",
        ],
        "fixes": [
            "修复四步流水线数据库表名不匹配（p5_history→p5_history_data 等）。",
            "AI 接口从百度千帆迁移至 AGNES API（agnes-2.0-flash）。",
        ],
    },
]

# ---------------------------------------------------------------------------
# 已知问题 / 待修复清单
# ---------------------------------------------------------------------------
KNOWN_ISSUES = [
    "在线学习引擎的权重自适应目前仅打印调整建议，未真正回写预测器配置（示意逻辑）。",
    "predict --model old/optimized 两条路径等价（use_optimized 形参被忽略），对比模式无差异。",
    "贝叶斯推断的似然为无界累乘，随验证记录增多会失真（经验加权而非严格贝叶斯）。",
    "组合生成的和值/跨度/SSD/方差等阈值硬编码，未读取统一配置。",
    "prediction_stat 缓存以「期号+数据量」为复用条件，数据被修正但条数不变时会返回旧结果。",
]

# 外部更新日志覆盖文件路径（可选）：若存在则优先使用，便于非开发者维护
_CHANGELOG_OVERRIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "changelog.json")


def get_current_version():
    """返回当前对外版本号字符串，例如 'v3.8'。"""
    return APP_VERSION


def get_app_name():
    """返回程序名称。"""
    return APP_NAME


def get_changelog():
    """
    返回变更日志列表（从新到旧）。
    若存在 data/changelog.json 则优先返回其中的内容（支持外部覆盖）。
    """
    if os.path.isfile(_CHANGELOG_OVERRIDE_PATH):
        try:
            with open(_CHANGELOG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, OSError):
            # 解析失败时回退到内置 CHANGELOG
            pass
    return CHANGELOG


def get_known_issues():
    """返回已知问题列表。"""
    return KNOWN_ISSUES


def load_version_info():
    """
    聚合返回版本信息字典，供 GUI 渲染。
    结构：
        {
            'app_name', 'version', 'release_date',
            'changelog': [...], 'known_issues': [...]
        }
    """
    return {
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "release_date": APP_RELEASE_DATE,
        "changelog": get_changelog(),
        "known_issues": get_known_issues(),
    }


def reload_module():
    """
    重新加载本模块（用于 GUI 实时检测版本变更）。
    返回重新加载后的模块对象。
    """
    return importlib.reload(sys_modules_version())


def sys_modules_version():
    """获取本模块在 sys.modules 中的引用（供 reload 使用）。"""
    import sys
    return sys.modules[__name__]


if __name__ == "__main__":
    info = load_version_info()
    print(f"{info['app_name']} {info['version']} ({info['release_date']})")
    print(f"变更记录条数: {len(info['changelog'])}")
    print(f"已知问题条数: {len(info['known_issues'])}")
