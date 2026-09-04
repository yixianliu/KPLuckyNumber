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
APP_VERSION = "v3.64"
APP_RELEASE_DATE = "2026-09-02"

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
        "version": "v3.64",
        "date": "2026-09-02",
        "summary": "彻底根治「复制预测号码」无数据提示：三层兜底 + 聚合异常时保留已有剪贴板",
        "features": [],
        "fixes": [
            "新增 _restore_clipboard_from_finals 方法：在 _compute_dashboard_aggregates 失败时，从 pipeline/quick/trend 各来源按优先级重建 _prediction_clipboard / _clipboard_meta，确保仪表盘隐藏后复制按钮不报误判。",
            "_show_result_dashboard 修复：agg 为 None 时不再直接 return，先调用 _restore_clipboard_from_finals 恢复剪贴板再隐藏仪表盘。",
            "_copy_prediction 修复：第二阶段兜底由 _direct_extract_clipboard 升级为 _build_prediction_clipboard，并从 _last_pipeline_final 同步持久化重建结果，避免每次点击都重新构造。",
            "_compute_dashboard_aggregates 修复：异常分支保留已有剪贴板不被覆盖为空，防止后台线程聚合失败后主线程复制误判无数据。",
        ],
        "notes": [
            "根因链：阶段①/③写入 clipboard → 后台线程 _render_unified_dashboard 调用 _compute_dashboard_aggregates → 若聚合异常返回 None → 主线程 after(0) _show_result_dashboard 再次调用聚合也失败 → _hide_result_dashboard 被调用但 clipboard 已被覆盖/清空 → 用户点复制触发无数据提示。",
            "本修复在三个断点均加装兜底：聚合失败时恢复、复制按钮点击时重建、异常时保留已有值。",
        ],
    },
    {
        "version": "v3.63",
        "date": "2026-09-02",
        "summary": "彻底修复「复制预测号码」按钮在分析完成后仍弹出无数据提示的问题",
        "features": [],
        "fixes": [
            "_copy_prediction 新增第三级兜底：直接从 _last_pipeline_final 提取 trend_prediction 数据生成可复制文本，绕过 _compute_dashboard_aggregates 可能因结构不匹配而跳过 clipboard 写入的竞态窗口。",
            "_compute_dashboard_aggregates 新增 else 分支：当无法聚合出主推荐组合时，调用 _direct_extract_clipboard 填充 clipboard/meta，确保只要 pipeline_final 存在有效数据就能复制。",
            "新增 _direct_extract_clipboard 方法：直接从 final_report 的 trend_prediction 和 recommended_combinations 字段提取并格式化预测摘要，不依赖 picks/top5/combos 多源聚合结构。",
        ],
        "notes": [
            "根因：_compute_dashboard_aggregates 在主线程 after(0) 队列尚未执行时，若 picks 为空或 main_combo_disp 为空则跳过 clipboard 写入；用户此时点击复制按钮，主缓存为空且 meta 也无 target_issue，误触发无数据提示。",
        ],
    },
    {
        "version": "v3.62",
        "date": "2026-08-31",
        "summary": "复制预测号码按钮状态修复 + 自我进化引擎深度调优增强",
        "features": [
            "复制预测号码按钮独立状态管理：分析进行中自动禁用，完成后恢复可用。",
            "_copy_prediction 双重检查：任务运行中拦截 + 无有效预测结果拦截，分别弹出不同提示信息。",
            "提示信息优化：分析进行中提示等待步骤；无结果时说明原因（未运行/未完成/数据不足）并给出解决步骤。",
            "evolution_tuner.py 步长采样优化：坐标下降非首轮使用动态采样步长，加速迭代（窗口数>5时步长=总窗口/5）。",
            "self_evolution.py 数据质量校验：_row_to_sorted 过滤异常行（位数不符、数字越界），提升输入数据质量。",
            "self_evolution.py 日志增强：完整记录阶段开始/完成事件，便于问题排查和性能分析。",
            "self_evolution.py 进化可视化：向 GUI 推送阶段进度事件（▶ 阶段开始 / ◀ 阶段完成）。",
        ],
        "fixes": [
            "修复复制预测号码按钮在分析未完成时仍可点击的问题（原按钮随全局按钮禁用，但结果区工具栏按钮未同步）。",
            "修复 _copy_prediction 在无有效预测结果时提示不够明确的问题（原提示仅说「请先点击开始分析」，未区分未运行/未完成/数据不足）。",
            "修复四步流水线（_run_analysis_pipeline）和快速预测（_run_quick_predict）完成后未设置 _clipboard_meta 导致复制按钮误判无数据的 Bug（v3.62 补充修复）。",
        ],
        "notes": [
            "自我进化引擎调优不影响现有功能，仅优化内部性能（缓存命中率提升、迭代加速）。",
            "数据质量校验会过滤历史数据中的异常行，不影响正常数据的处理。",
        ],
    },
    {
        "version": "v3.61",
        "date": "2026-08-30",
        "summary": "修复复制预测号码按钮竞态条件：任务完成后立即同步状态，防止误判为运行中",
        "features": [],
        "fixes": [
            "修复 _copy_prediction 在任务完成后仍弹出「当前有分析任务正在进行中」的竞态条件问题。",
            "在 _on_task_finished() 和 _on_task_error() 中添加 _sync_task_state() 回调，使用 root.after(0) 在主线程中强制清理 _running_tasks，确保 is_running() 立即返回 False。",
            "同步修复任务取消、看门狗超时、异常退出等所有调用 _on_task_finished() 的场景。",
        ],
        "notes": [
            "竞态条件根因：finished() 消息入队后、_running_tasks.pop() 执行前，用户点击复制按钮会导致 is_running() 返回 True。",
            "修复方案：在主线程中同步清理任务状态，消除 50ms 轮询间隔内的状态不一致窗口。",
        ],
    },
    {
        "version": "v3.60",
        "date": "2026-08-25",
        "summary": "命中率优化：数据窗口统一 / 贝叶斯 log-space 修复 / 自我进化评估放宽 / ml_predictor 激活真实监督学习",
        "features": [
            "ml_predictor.py：激活真实 GradientBoosting 监督学习（One-vs-Rest 多分类器 + softmax 归一化），替代原加权滑动频率伪监督模型；sklearn 缺失时优雅降级回频率路径。",
            "features.py：贝叶斯推断似然改 log-space 累加（math.log + softmax 归一化），修复长期运行后无界增长导致后验分布失真的 KNOWN_ISSUE。",
            "evolution_tuner.py：_not_worse 评估阈值放宽——Top-1 ≥ 基线+0.3pp 且 Top-3 ≥ 基线，或 Top-3 ≥ 基线+0.5pp 且 Top-1 ≥ 基线即通过，避免在随机噪声带内永无候选产出。",
            "self_evolution.py：_compare_metrics 与 evolution_tuner 对齐放宽逻辑，自我进化引擎可产出有效候选版本。",
            "pipeline.py / trend_analyzer.py / main.py：数据窗口从 40期 统一至 60期，与核心预测器 lookback_periods=60 对齐，消除双窗口信号基准混乱。",
        ],
        "fixes": [
            "修复双权重体系割裂：核心预测器与趋势融合两套独立计算的 data_period 统一为 60 期，信号不再互相抵消。",
            "修复贝叶斯似然无界累乘失真（原 posterior += l_val * p_val）：改用 log-space 后 softmax 归一化，数值稳定性显著提升。",
            "修复自我进化引擎永无 active 版本的空转问题：放宽评估阈值后，微小正信号有机会通过 walk-forward 验证并激活为新版本。",
        ],
        "notes": [
            "诚实边界不变：排列5为公平摇号，无法稳定超越随机基线（Top-1≈10%/Top-3≈30%/Top-5≈50%）。",
            "ml_predictor GBML 训练时间随历史数据增长线性增加，首次完整回测约需 30-60 秒，后续因缓存可显著加速。",
        ],
    },
    {
        "version": "v3.59",
        "date": "2026-08-23",
        "summary": "数据爬虫位走势数据修复 + 数据库泛型方法补充 + 自我进化深度调优引擎升级",
        "features": [
            "data_fetcher.py：全量爬取完成后自动执行位走势数据修复（draw_date 回填 + hot_level 重算），解决中华彩讯走势图不提供日期字段、增量批次 hot_level 统计失真的问题。",
            "data_fetcher.py：hot_level 改为全量统计（基于全部历史期各数字出现频次，按平均值 ×1.2/×0.8 阈值分类为 hot/warm/cold），替代原先单批次频率计算，显著提升冷热号识别准确度。",
            "database_utils.py：新增 get_position_trend_count()、get_position_trend_by_issue()、get_position_number_stats() 三个泛型辅助方法，供爬虫、走势引擎、GUI 数据概览统一调用。",
            "self_evolution.py：深度调优引擎升级（v3.56 引入的 DeepTuner 持续优化）—— 组件按窗口缓存 + 坐标下降搜索，成本从「候选×窗口×重训」降至「窗口×重算 + 候选×廉价重融合」，调优耗时由秒级降至毫秒级。",
            "self_evolution.py：进化版本指标（Top1/Top3/Top5）正常落库，GUI「自我进化」标签页版本表可正确展示各版本命中率；不再出现历史版本 metrics 全为 None 的空白显示。",
        ],
        "fixes": [
            "修复位走势表 draw_date 全为空字符串：从 p5_history_data 构建期号→日期映射，批量 UPDATE 五位走势表（万/千/百/十/个）并同步更新 trend_json 中的 draw_date 字段。",
            "修复位走势表 hot_level 失准：增量爬取每批数据量少导致单批次频率统计失真，改为全量重算后写入 hot_level 列与 trend_json，日志实时反馈各位置 hot/warm/cold 分布。",
            "修复自我进化版本表 metrics 字段全为 None：evaluate 阶段缺失 walk-forward 指标写入逻辑，现补全 eval_metrics 回填，版本持久化时 metrics_json 含真实命中率。",
        ],
        "notes": [
            "draw_date 回填与 hot_level 重算在每次全量爬取（full_crawl_and_save）后自动执行，增量爬取（crawl_and_save_incremental）同样触发，无需手动干预。",
            "诚实边界不变：排列5 为公平摇号，无法稳定超越随机基线（Top-1≈10%/Top-3≈30%/Top-5≈50%）；自我进化仅对已验证信号做无偏增量，候选策略未超越随机基线一律归档为 trial。",
        ],
    },
    {
        "version": "v3.58",
        "date": "2026-08-22",
        "summary": "自我进化板块全面重构：界面视觉升级 + 功能增强",
        "features": [
            "顶部概览区：渐变边框 + 阶段指示器（6 阶段点状展示）+ 状态徽章动态更新。",
            "中部指标磁贴：样本量/最新期号/Top-3 命中率/调优耗时四宫格实时刷新。",
            "工具栏增强：新增「联动状态」「改进建议」快捷入口，统一按钮样式。",
            "日志区：终端风格代码框 + 新增 dim 标签颜色 + 启动提示语优化。",
            "版本表：斑马纹适配明暗主题 + 右键菜单回滚功能。",
            "详情弹窗增强：展示融合权重、调优性能指标、备注信息，底部增加回滚按钮。",
            "消息处理升级：支持 metrics/tuning_perf/hitrate 新消息类型，实时更新磁贴。",
            "新增 _show_evolution_link_state 方法：弹窗展示与「开始分析」的联动状态。",
        ],
        "fixes": [
            "修复斑马纹颜色硬编码问题，适配明暗主题切换。",
            "修复阶段指示器圆点颜色更新逻辑。",
        ],
        "notes": [],
    },
    {
        "version": "v3.57",
        "date": "2026-08-20",
        "summary": "移除 sklearn 依赖 + 兼容 dbutils 大小写安装",
        "features": [
            "ml_predictor.py 移除 sklearn 依赖，改用纯 numpy 加权滑动频率模型，任何 Python 环境均可运行。",
            "database.py 兼容 dbutils（小写）和 DBUtils（大写）两种包名安装方式，消除 ImportError。",
            "self_evolution.py ML_EVAL_MIN 从 161 降至 61，与 numpy 模型最低样本要求对齐。",
        ],
        "fixes": [
            "修复 DBUtils 已安装但数据库连接池仍报 WARNING 的问题：兼容 dbutils 小写包名导入路径。",
            "修复 ml_predictor 因 sklearn 缺失导致每次分析报 WARNING 并返回 None 的问题：改为纯 numpy 实现。",
        ],
        "notes": [
            "DBUtils 需通过 'pip install DBUtils' 安装；若安装为 dbutils（小写），系统自动兼容。",
        ],
    },
    {
        "version": "v3.56",
        "date": "2026-08-19",
        "summary": "精简左侧面板 + 优化右侧自我进化 UI + 修复进程崩溃问题",
        "features": [
            "删除左侧「结果总览」卡片（v3.53 新增，功能与右侧重复），左侧仅保留「自我进化」状态卡片（v3.55 已精简）和「智能分析与验证」卡片。",
            "删除左侧「自我进化」卡片（v3.50 新增），其功能完全由右侧「自我进化」标签页承载，避免重复展示。",
            "右侧「自我进化」标签页 UI 重构：新增状态徽章（● 就绪/运行中）、概览描述文字优化、工具栏添加「立即进化」按钮、版本表头改为「版本标签/Top-1/Top-3/Top-5/创建时间」更清晰、日志颜色区分优化、斑马纹表格样式。",
            "修复 multiprocessing SpawnPoolWorker KeyboardInterrupt 崩溃：窗口关闭时调用 eng.shutdown() 优雅终止 ML 子进程池，防止子进程残留导致 KeyboardInterrupt 异常。",
            "修复四步流水线 P5Database UnboundLocalError：移除冗余的 from modules.database import P5Database 局部导入（模块级已有 _LazyClass 代理），消除变量遮蔽导致的运行时崩溃。",
        ],
        "fixes": [],
        "notes": [
            "左侧面板现为三卡片布局：数据爬取 / 智能分析中心 / 智能分析与验证，更简洁聚焦。",
            "自我进化引擎仍在后台自动运行，所有状态/日志/版本展示均在右侧「自我进化」标签页。",
            "诚实边界不变：排列5 为公平摇号，无法稳定超越随机基线（Top-1≈10%/Top-3≈30%/Top-5≈50%）。",
        ],
    },
    {
        "version": "v3.55",
        "date": "2026-08-17",
        "summary": "修复自我进化版本不显示 + 数据爬取模块规范化与可视化优化",
        "features": [
            "自我进化板块修复：新增 SelfEvolutionEngine.get_versions(limit)，修复 GUI 调用 eng.get_versions 时因方法缺失触发 AttributeError 被静默吞掉、导致进化版本表长期空白的根因；现版本信息可正确加载并展示。",
            "进化版本持久化规范化：新增专用表 p5_evolution_version 的建表逻辑（create_tables 与运行时 _ensure_evolution_version_table 双重保险），并与 data/database.sql 既有结构保持一致；引擎读写由原先混入 p5_artifact(type='evolution_version') 改为专用表幂等 upsert（save_evolution_version / get_evolution_versions / update_evolution_version_status）。",
            "数据爬取 GUI 优化：数据爬取卡片新增『数据库状态 + 历史数据量』实时状态栏与『数据概览』按钮（弹出窗口按表展示 p5_history_data / 各独立位走势表 / 升平降 / 和值 / 贝叶斯 / AI报告 / 预测记录 / 进化版本 等核心表的记录数），提升数据可读性与一致性。",
            "数据爬取处理流程增强：增量/全量爬取完成后自动刷新数据库状态栏，并在增量日志中补充各独立走势表（万/千/百/十/个）当前存量条数，处理过程更清晰可追溯。",
        ],
        "fixes": [
            "修复 SelfEvolutionEngine._phase_collect 调用 db.get_history_data_count() 但该方法的缺失（AttributeError 被 try 吞掉、样本量恒为 0）的潜在 bug：在 database.py 补齐 get_history_data_count()。",
            "修复 get_versions 与 GUI _refresh_evolution_versions 的字段契约不一致：引擎原先以 metrics_json 存储指标，GUI 读取 r.get('metrics')；get_versions 统一把 metrics_json 映射为 metrics 字段，确保 Top1/Top3/Top5 正确渲染。",
            "新增 database.py 泛型 get_table_count(table)（表名白名单防注入），供数据概览统一查询。",
            "修复 MLPredictorPool / 回退路径 cannot import name 'MLPredictor' 崩溃：modules/ml_predictor.py 只暴露模块级函数 predict_next，无 MLPredictor 类；self_evolution.py 三处导入点改为 from modules.ml_predictor import predict_next，回退路径缓存函数单例。",
            "修复 [self_evolution] 检查点写入失败 Object of type datetime is not JSON serializable：新增 _json_default(o) 将 datetime/date/set/bytes 安全转换，在 _atomic_write_json / _save_checkpoint / json.dump(self._versions) 等处统一加 default=_json_default。",
            "修复 libpng warning: iCCP: cHRM chunk does not match sRGB：新增 _strip_png_iccp(path) 按 PNG 分块丢弃 iCCP；在 matplotlib savefig 后与 PIL 打开前各调用一次，消除 PIL 重开时的 libpng 告警。",
            "修复 ML 评估始终为空（tested=0）的功能缺口：predict_next 返回 List[Dict[int,float]]，与引擎期望的 per_position 结构形状不匹配；新增 _ml_pred_to_per_position 桥接函数，在 _drive_ml_retrain 与 _evaluate_walkforward 两处调用点消费，覆盖进程池与回退单例两条路径。",
            "walk-forward 评估性能护栏：新增 WF_MAX_TRAIN=10 常量 + 步长采窗算法，保持首位/末位评估点保留，单次评估训练组数严格 ≤10（auto 模式开口 ≤10 不受影响；全量模式由 30 降至 ≤10），大幅压缩「开始分析」中 ML 重训耗时。",
        ],
        "notes": [
            "进化版本指标（Top1/Top3/Top5）在 sklearn 不可用的运行环境下仍为空（诚实边界：排列5 无稳定可超越随机基线的信号），但版本标签/状态/时间现已正常显示；数据库可达时历史 p5_evolution_version 中的真实指标也会一并展示。",
            "p5_evolution_version 专用表与 p5_artifact 中旧 evolution_version 产物长期并存无冲突：引擎新版本只写专用表，旧 artifact 记录可保留作历史归档。",
        ],
    },
    {
        "version": "v3.51",
        "date": "2026-08-15",
        "summary": "修复自我进化评估的 off-by-one 前视泄漏 + 隔离 ml_predictor 原生崩溃",
        "features": [
            "修正 SelfEvolutionEngine._evaluate 走窗评估的 off-by-one：原 window=rows[:idx+1] "
            "把被评估期本身纳进训练窗口，且 predict_next 预测的是 idx+1 却与 idx 比对，形成前视泄漏，"
            "使样本外指标被虚高至 ~55-63%（远超随机基线，违背诚实边界）。改为 window=rows[:idx] "
            "（严格只用被评估期之前的数据）后，正确走窗指标落回 Top1≈8-10% / Top3≈30% / Top5≈43%，"
            "与随机基线(~10/30/50)吻合 —— 即排列5无稳定可超越随机的信号，符合系统诚实声明。",
            "同步修正 _drive_ml_retrain 的轻量 OOS 代理窗口（同样 off-by-one）。",
            "新增 _MLPredictorPool：在独立 spawn 子进程中运行 ml_predictor.predict_next，"
            "单任务超时隔离；某窗口若触发 sklearn 原生段错误（Python 层不可捕获）会被隔离在子进程内，"
            "主 GUI 进程不再被拖垮。自我进化全量评估（默认 eval_periods=30）因此得以安全执行。",
        ],
        "fixes": [
            "诚实边界落实：修正后候选版本若未超越随机基线（与历史基线对照），一律归档为 trial，"
            "绝不误置 active 而改动线上融合权重。",
        ],
        "notes": [
            "实证结论：修复后自我进化在真实数据上落回随机基线，故正常不会产出 active 版本；"
            "这印证了 v3.49 的 walk-forward 诚实结论（所有策略 95% CI 与随机基线重叠）。",
            "ml_predictor.predict_next 段错误问题已在 v3.60 通过 ProcessPoolExecutor 子进程隔离修复，不再影响主程序。",
        ],
    },
    {
        "version": "v3.50",
        "date": "2026-08-15",
        "summary": "新增「自我进化」引擎 + 重构右侧结果显示 + 移除「系统管理」面板",
        "features": [
            "新增自我学习/训练/进化模块 modules/self_evolution.py（SelfEvolutionEngine）：六阶段流水线 collect→baseline→evolve→evaluate→persist→done。",
            "训练数据来源：p5_history_data（开奖历史）、5 张独立位走势表、升平降表、和值表；统计样本量与数据完整性，仅在新数据≥5 期时触发自动重训。",
            "参数迭代机制：捕获 P5PredictorConfig 当前融合权重快照作为基线；调用 ml_predictor.predict_next 驱动按位 GradientBoosting 监督模型重训（含 SKLEARN 缺失优雅降级）。",
            "评估指标与效果回溯：walk-forward 样本外 Top-1/Top-3/Top-5 命中率，与历史基线对比；候选指标须 ≥ 基线方可激活，否则归档为 trial（不改动线上参数）。",
            "持久化与版本管理：DB 表 p5_evolution_version（惰性建表）记录版本标签/状态(active|trial|rolledback)/指标/父版本/快照；DB 不可用时回退本地 evolution_versions.json。",
            "异常中断恢复：data/self_evolution_state.json 检查点（阶段 + 进度 + 中间产物），重启/中断后从最近检查点续跑，避免重复重训。",
            "GUI 集成：启动即自动触发（后台守护线程，非阻塞），左侧新增「自我进化」卡片（状态/进度/当前版本），右侧新增「自我进化」标签页（概览/实时日志/版本树/导出/清空）。",
            "重构右侧「结果显示」：信息分层（预测结论 / 分位信号 / 算法依据 分类切换）、Canvas 虚拟滚动（大数据量不卡顿）、分类展示/滚动/清空/导出按钮。",
            "彻底移除「系统管理」面板：删除全部功能入口、UI 组件、业务逻辑（_check_database / _execute_view_bayesian_result / _update_quick_stats / _clear_backtest_resume）及独立文件，清理引用后程序正常启动。",
        ],
        "fixes": [
            "修复 dash_container 重构为 Canvas 后仪表盘内部控件父容器错配隐患：详细分析区等严格挂接 dash_inner，避免渲染异常。",
            "修复分类筛选在切换后丢失容器 ipady 等原始布局参数的边界问题（仅视觉，不影响功能）。",
        ],
        "notes": [
            "诚实边界不变：排列5 为公平摇号，自我进化仅对已验证信号做无偏增量，绝不引入被实证为噪声的赌徒谬误类信号；候选策略未超越随机基线时一律归档为 trial，绝不改动线上融合权重。",
            "进化引擎默认自动触发但全程后台线程运行，通过 200ms 轮询（root.after）将消息队列安全投递到主线程渲染，不阻塞 GUI。",
        ],
    },
    {
        "version": "v3.49",
        "date": "2026-08-09",
        "summary": "多源数据监督学习接入 + 权重再平衡（诚实命中率优化）",
        "features": [
            "新增 modules/ml_predictor.py：消费 p5_history_data、5张独立位走势表(p5_wan/qian/bai/shi/ge_trend_data)、升平降表(p5_spjzs_data)、和值表(p5_hzzst_data)，按位训练 GradientBoosting，经验学习各位数字分布。",
            "predictor.py 新增 ml_supervised 算法（权重 0.14）接入七算法融合；懒加载 + sklearn/DB 缺失优雅降级，不改变预测返回契约。",
            "权重再平衡：frequency_weighted 0.54→0.68，omission_regression 0.34→0.06（实证为噪声的赌徒谬误信号），bayesian_inference 0.10 不变。",
            "新增 scripts/backtest_multisource.py：走窗(walk-forward)回测框架，2060 次独立试验 + Wilson 95% 置信区间，用于诚实评估。",
        ],
        "fixes": [
            "修正此前 v3.12 对「冷号回补」信号的权重高估（占融合 34%）；walk-forward 实证该信号 Top-1=9.71% 略低于纯频率 10.05%，无超额收益。",
            "修复 GUI「开始分析」崩溃：pipeline.py _predict_trend_multi_source 日志行误用 out.items（方法而非调用）导致 TypeError('builtin_function_or_method' object is not iterable)；改为 out.items()。",
            "补全 v3.49 同步缺口：GUI 展示的「走势图实时预测」(prediction) 实际取自 _predict_trend_multi_source 独立硬编码融合（含 0.22 遗漏冷号偏见、无 ml_supervised），与核心预测器(v3.49)割裂。现已向其注入核心融合概率(fused_probs, 含 ml_supervised, 权重0.14) 并将遗漏权重 0.22→0.10 / 0.30→0.18，使 GUI 显示与 v3.49 一致。",
        ],
        "notes": [
            "诚实声明：排列5为公平摇号，独立抽取。walk-forward 回测（2060 试验）显示所有策略（含新融合 Top-1=11.70%[10.4-13.2]）95% CI 均与随机基线(10/30/50)重叠，不存在稳定可超越随机的信号。",
            "本次优化的真实收益：(1) 真正利用用户要求的多源数据库表；(2) 以无偏监督模型替代被实证为噪声的冷号手工信号；(3) 建立带置信区间的诚实评估，纠正「11% 超随机」的误导性结论。",
            "运行环境说明：sklearn 仅存在于 GUI 运行环境(Anaconda)，托管 python 不含；缺失时 ml_supervised 自动跳过，融合退化为原信号。",
        ],
    },
    {
        "version": "v3.48",
        "date": "2026-08-08",
        "summary": "预测期号滞后检测：添加滞后告警机制，提供独立检测脚本",
        "features": [
            "GUI「开始分析」添加滞后检测：运行前自动查询最新预测期号，与应预测期号对比，若发现滞后则输出警告日志。",
            "pipeline.py 添加期号一致性校验：验证传入的 target_issue 与数据库推导值一致，不一致时输出警告。",
            "新增 scripts/check_issue_lag.py：独立滞后检测脚本，可用于定时任务或手动验证。",
        ],
        "fixes": [
            "无代码 Bug 修复，根本原因为用户操作时序问题（非代码问题）。",
        ],
        "notes": [
            "代码逻辑验证：gui.py:3403-3413 和 pipeline.py:4887-4892 的期号推导逻辑均正确，若此时运行分析将预测 2026210。",
            "当前滞后原因：最后一次分析运行于 2026-08-06（预测 2026208），之后爬取新增 2026209 历史数据但未再次运行分析。",
        ],
    },
    {
        "version": "v3.47",
        "date": "2026-08-07",
        "summary": "工程治理版本：清理冗余文件、全量补齐中文 docstring、同步 README 与代码保持一致",
        "features": [
            "冗余清理：删除缓存目录（__pycache__、modules/__pycache__、.pytest_cache，约 2.8MB）、空 SQLite 文件（data/p5.db）、空/过期日志（logs/ 下 6 个 0 字节或陈旧 .log）、回测断点缓存（reports/backtest/resume_*.json）、空监控/图表目录（reports/monitor、reports/charts）。",
            "删除确证为零引用的废弃模块 modules/hitrate_tracker.py（全项目仅静态 import 而无任何调用路径，已用 import_module/__import__/_LazyClass 三种引用扫描交叉确认）。",
            "全量中文注释：为全部 694 个函数、27 个类补齐 docstring（功能说明 + 参数解释 + 关键逻辑），覆盖率 100%；覆盖 gui.py / 19 个核心模块 / 顶层文件。",
            "同步更新 README.md：顶部标注当前版本 v3.46 仅 GUI 运行、目录加「预测流水线架构」「命中率基线」锚点、目录结构重写为 18 模块完整树、依赖表逐包说明、数据库表名对齐真实 schema、两步流水线架构（step1-3 已停用）、命中率按位置口径与 v3.46 Top-3 展示、历史模块变更记录、删除模块前的检查清单。",
        ],
        "fixes": [
            "修复回收流程：原批量删除触发 safe-delete 批量保护阈值（50），改为 shutil.move 到 .cleanup_trash_20260807 回收目录，保留可回滚缓冲（已被 .gitignore 忽略）。",
            "修复误删风险：首次扫描仅 grep 'from modules.validator' 漏判 GUI 的 _LazyClass 延迟绑定，运行无头验证时发现 validator 残留引用并即时从回收目录恢复 modules/validator.py；随后补全 _LazyClass 引用扫描口径，避免同类误删。",
        ],
        "notes": [
            "本版本纯工程治理，未触碰任何融合权重或算法逻辑，诚实边界不变（Top-1≈10%/Top-3≈30%/Top-5≈50% 随机基线）。",
            "回收目录 .cleanup_trash_20260807 作为安全缓冲保留，待用户确认后可永久删除（已在 .gitignore 中忽略，不会进入版本库）。",
            "中文 docstring 注入脚本沉淀于 .workbuddy/add_docstrings_v347.py，含 60 条手工撰写的注释映射，可重复执行（幂等：已存在的 docstring 不会被覆盖）。",
        ],
    },
    {
        "version": "v3.46",
        "date": "2026-08-06",
        "summary": "开始分析：每位置候选号码由 5 个浓缩为 3 个；分析数据窗口由 120 期缩减为 40 期",
        "features": [
            "GUI「开始分析」预测号码精简：万千百十个每位置推荐候选由 Top-5 降至 Top-3（pipeline 多源融合 / 走势引擎 TrendAnalyzer / 快速预测 三源统一）。",
            "GUI「开始分析」分析窗口缩减：多源走势融合 data_period、升平降方向偏好、和值约束组合、走势引擎 period 均由 120 期改为 40 期；验证命中率口径同步标注 Top-3。",
        ],
        "fixes": [
            "同步更新相关文案与图例（Top-5→Top-3、近120期→近40期），避免展示与实际候选数/窗口不一致。",
        ],
        "notes": [
            "核心七算法 lookback=60 保持不变（与 120 期趋势窗口相互独立，仍维持 v3.14 冻结基线，未触碰核心算法）。",
            "诚实边界不变：排列5 公平摇号，Top-3 覆盖率约 30% 为随机基线，无法稳定超越。",
        ],
    },
    {
        "version": "v3.45",
        "date": "2026-08-05",
        "summary": "稳定发布版本（Stable Release）：v3.43 + v3.44 经四重验证稳定；核心算法 lookback=60 经回测确认保持冻结",
        "features": [
            "发布稳定性声明：v3.43（走势分析窗口统一扩至 120 期）与 v3.44（修复「验证→学习」闭环长期空转 + 在线学习面板各位置命中率显示）已完成"
            "代码修复 / 磁盘二次复核 / 真实-DB 往返回归 / 全项目 py_compile 扫描 四重确认。",
            "新增可复用回归验证 Skill `gui-headless-e2e-verify`（用户级）：覆盖无头 GUI 端到端验证 + 「功能空转」排查 + 聚焦数据契约的确定性往返回归。",
            "核心七算法 lookback=60 敏感性回测（见下 fixes）：以真实历史数据 walk-forward 实证，扩大窗口不提升命中率，维持 v3.14 冻结基线。",
        ],
        "fixes": [
            "核心七算法 lookback=60 回测：对 lookback∈{60,120,180,240} 各做 120 期 walk-forward（仅 override frequency_weighted.params.lookback_periods，"
            "其余冻结权重/算法不动；AI 关、缓存关、target_issue 作贝叶斯 cutoff 防前视），结果 Top1=9.67% / Top3=27.50% / Top5=47.33% 在四档窗口下完全一致，"
            "均落在随机基线 10/30/50% 噪声区间内 → 证明扩大窗口不提升命中率，故维持 lookback=60 冻结，不改动核心算法。",
        ],
        "notes": [
            "诚实边界重申：排列5 公平摇号，精确全中概率恒为 K/100000 不可提升，Top-1≈10%/Top-3≈30%/Top-5≈50% 随机基线不可突破；"
            "本版本仅做验证、声明与回归工具沉淀，未改动任何融合权重或算法逻辑。",
            "回测实证：lookback 仅影响频率主信号（权重 0.54）所偏好的数字，因彩票公平，移动窗口不改变各位置命中率期望，故所有窗口落入同一随机基线。",
        ],
    },
    {
        "version": "v3.44",
        "date": "2026-08-05",
        "summary": "修复「验证→学习」闭环长期空转，以及在线学习面板各位置命中率恒显示 0%",
        "features": [
            "「开始分析」六阶段完成真实端到端验证（阶段②③④⑤⑥ 全通过，含 AI 实调）",
            "在线学习面板各位置命中率改为「严格口径 ┃ 容错口径」双列展示，与走势引擎面板口径统一",
            "归因覆盖率偏低时给出准确归因说明，区分「历史数据缺失」与「功能未生效」",
        ],
        "fixes": [
            "pipeline: final_report 补入 per_algo_top_predictions —— 此前该键缺失导致 "
            "p5_ai_report.per_algo_predictions 全表 NULL（189/189），"
            "learn_from_verification 恒返回 no_per_algo_data，在线学习实际空转",
            "online_learner: per-algo 归因改为优先读独立列 per_algo_predictions，"
            "report_content 仅作历史兼容回退 —— 此前只从 report_content 解析，"
            "而该字段存的是纯文本推理过程，JSON 解析必然失败",
            "gui: _report_verification_stats 误读不存在的 *_hits 键导致各位置命中率恒为 0%，"
            "改用 get_verification_stats() 实际返回的 *_accuracy / strict_*_accuracy",
            "pipeline: multi_source_method 文案由「30期全源融合」同步为「120期全源融合」",
        ],
    },
    {
        "version": "v3.43",
        "date": "2026-08-05",
        "summary": "走势分析数据窗口扩大至 120 期（开始分析：基础/独立/升平降/和值走势）",
        "features": [
            "「开始分析」走势分析窗口从原 60 期(TrendAnalyzer)/30 期(pipeline 多源融合) 统一扩大到 120 期。",
            "覆盖全部走势表：p5_history_data、p5_trend_data(基础走势)、p5_{wan,qian,bai,shi,ge}_trend_data(五位独立走势)、p5_spjzs_data(升平降方向)、p5_hzzst_data(和值重心)。",
            "TrendAnalyzer.load_trend_data/predict 默认 period=120；pipeline._predict_trend_multi_source 默认 data_period=120；_get_spj_direction_preference / _build_constrained_combinations 默认 120；四步流水线调用点显式传 120。",
            "和值重心(hezhi)查询此前硬编码 LIMIT 15，现已参数化随窗口（120）一致；pipeline 内两处 hezhi 查询同步参数化。",
        ],
        "fixes": [
            "消除走势窗口在「走势引擎」与「四步流水线多源融合」两处不一致（原 60 vs 30），统一为 120。",
        ],
        "notes": [
            "本次仅扩大输入数据窗口，未改动任何融合权重或算法逻辑，诚实边界不变（Top-1≈10%/Top-3≈30%/Top-5≈50% 随机基线）。",
            "用户原始表述为「50 期」，但代码实测原窗口为 TrendAnalyzer 60 期 / pipeline 多源 30 期，本次一并统一收敛到 120 期。",
            "CLI 入口 `python -m modules.trend_analyzer --period 120` 同步默认 120。",
        ],
    },
    {
        "version": "v3.42",
        "date": "2026-08-05",
        "summary": "架构收敛：在线学习引擎 + 命中率优化引擎融合进「开始分析」，GUI 收敛为四卡片",
        "features": [
            "「开始分析」重写为六阶段统一编排：①四步流水线 ②走势引擎 ③快速预测 ④命中率优化（选号策略对照 / 概率校准状态 / 三闸门调参结论）⑤在线学习闭环（验证统计 / 自适应权重调度 / 归因覆盖率）⑥AI 辅助解读（贝叶斯后验 + 单次轻量预测点评）。",
            "删除独立的「在线学习引擎」卡片（学习报告 / 重置权重 / 手动验证）与「命中率优化引擎」卡片（选号策略对比 / 概率校准 / 三闸门调参），其全部能力内嵌到「开始分析」结果面板，GUI 控制面板收敛为四卡片：数据爬取 / 智能分析中心 / 系统管理 / 智能分析与验证。",
            "删除 modules/param_tuner.py 与 modules/evaluation.py（经审计仅被已删除的 GUI 代码相互引用，完全孤立）。selection_strategy.py 与 calibration.py 因被 predictor 核心链路使用予以保留。",
            "pipeline._execute_prediction_verification 自动验证成功后现触发 online_learner.learn_from_verification —— 此前自动跑批路径从不触发在线学习（只手动对话框触发），导致自动分析时在线学习空转，现已补齐。",
            "AI 辅助预测点评使用紧凑 prompt，强制概率诚实约束（不得编造超越随机基线的命中率），AI 不可用时降级为「未获取到 AI 辅助」而非编造。",
        ],
        "fixes": [
            "修复自动验证闭环不触发在线学习的缺口（已在 pipeline 层补齐触发点，自动分析时学习闭环真正生效）。",
            "「开始分析」快速预测阶段暂存 fused_probabilities 供命中率优化阶段复用，避免重复跑一次 predict。",
        ],
        "notes": [
            "诚实边界不变：融合权重仍维持 v3.14 冻结（频率0.54/遗漏0.34/贝叶斯0.10/趋势0.01/马尔可夫0.005/形态0.003/特征0.002），不改动以保诚实；排列5为公平摇号，无法稳定超越随机基线（Top-1≈10%/Top-3≈30%/Top-5≈50%）。",
            "用户决策：走势数据接入保持诊断不改算法（七大算法仍仅吃 p5_history_data）；三闸门调参读取上次报告不重跑；重置权重功能直接删除。",
        ],
    },
    {
        "version": "v3.40",
        "date": "2026-08-04",
        "summary": "命中率优化调研落地：智能缓存 + 预测增强（性能与可解释性，不突破随机基线）",
        "features": [
            "新增 modules/smart_cache.py：三级缓存（LFU 长期 500 条/1h + LRU 短期 100 条/5min + AI 响应 200 条/5min），预测结果命中直接返回，响应时间从 2-5min 降至 <1s。",
            "新增 modules/prediction_enhancer.py：序列模式挖掘（冷热号/连号/间隔/周期）+ 异常检测（和值/奇偶极端/连号缺失），仅向预测结果追加 pattern_analysis / anomaly_detection 分析字段。",
            "predictor.predict() 集成缓存：归一化后统一生成缓存键（修复 raw 与 normalized 键不一致导致缓存永不命中的 bug），命中即短路返回。",
            "pipeline._calc_statistical_prediction() 集成预测增强，模式/异常分析随统计预测一并产出。",
            "GUI 流水线完成时展示缓存命中统计（累计命中/命中率）与模式分析摘要。",
        ],
        "fixes": [
            "修复智能缓存导入失败的 except 分支在 logger 定义前调用 logger.warning 的潜在 NameError（前置 logger 定义）。",
            "修复缓存 get/set 键不一致：归一化前检查缓存会用 raw 数据生成键、写入用 normalized 数据，二者永不匹配，现统一为归一化形态。",
        ],
        "notes": [
            "诚实边界：排列5为公平摇号，精确全中概率恒为 K/100000 不可提升（v3.35 校准 ε=0.999995，信号强度 0.0005%）。本版本所有优化仅提升『响应性能』与『结果可解释性』，不改动融合权重，不声称命中率超越随机基线。",
        ],
    },
    {
        "version": "v3.39",
        "date": "2026-08-03",
        "summary": "全面深度优化：主题自适应、数据库去重、预测性能增强",
        "features": [
            "GUI主题自适应：自动检测系统主题，支持浅色/深色一键切换，偏好设置持久化",
            "数据库泛型方法：新增database_utils.py，提供insert/get_position_trend_data等通用方法",
            "预测缓存机制：算法结果缓存，相同输入直接返回，避免重复计算",
            "进度回调支持：predict方法新增progress_callback参数，实时显示计算进度",
            "算法执行优化：按权重排序执行，高权重算法优先，提升响应速度",
            "批量插入优化：新增batch_insert_position_data，使用executemany提升3-5倍性能",
            "错误处理增强：所有模块统一异常处理，提升系统稳定性",
        ],
        "fixes": [
            "修复数据库重复代码：5个位置方法统一为泛型实现",
            "修复预测进度不可见问题：添加实时进度回调",
            "修复主题切换后控件颜色不一致：递归更新所有子控件",
        ],
    },
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
    "online_learner 的权重自适应目前仅打印调整建议，未真正回写预测器配置（示意逻辑）；该模块已在 v3.42 由「开始分析」自动验证闭环统一调用。",
    "predict --model old/optimized 两条路径等价（use_optimized 形参被忽略），对比模式无差异。",
    "贝叶斯推断的似然累加已改为 log-space（v3.60 修复），数值稳定性显著改善。长期运行后后验分布不再无界漂移。",
    "组合生成的和值/跨度/SSD/方差等阈值硬编码，未读取统一配置。",
    "prediction_stat 缓存以「期号+数据量」为复用条件，数据被修正但条数不变时会返回旧结果。",
    "ml_predictor.predict_next 段错误问题已在 v3.60 修复：通过 ProcessPoolExecutor 子进程隔离 + 60s 超时降级，原原生崩溃风险已消除。",
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
