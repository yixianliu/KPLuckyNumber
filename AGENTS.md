# AGENTS: KPLuckyNumber 项目速查（给 AI 编码代理）

简短说明：本文件为仓库中 AI 编码代理提供可直接使用的、与代码实现紧密相关的操作性提示。目标是让代理在不依赖外部说明的情况下，快速完成常见任务（抓取、入库、预测、AI 分析、文章处理）。

主要入口
- CLI：`D:\PythonProject\KPLuckyNumber\main.py`。常用子命令示例：
  - 更新并入库最新开奖：python D:\PythonProject\KPLuckyNumber\main.py update
  - 运行预测（原始/优化模型）：python D:\PythonProject\KPLuckyNumber\main.py predict --model optimized
  - 后测对比：python D:\PythonProject\KPLuckyNumber\main.py backtest --mode compare --start 50 --count 50
  - 调用 AI 深度分析（需配置 API Key）：python D:\PythonProject\KPLuckyNumber\main.py ernie --limit 30
  - 处理单篇文章：python D:\PythonProject\KPLuckyNumber\main.py process-article --url "..." --title "..."
  - 分析历史特征并输出报告：python D:\PythonProject\KPLuckyNumber\main.py analyze
  - 运行综合（二次）深度分析：python D:\PythonProject\KPLuckyNumber\main.py comprehensive --limit 30
  - 批量爬取并保存文章到Redis（可选择不提取预测）：python D:\PythonProject\KPLuckyNumber\main.py save-articles --max 100
  - 批量处理文章（爬取→AI→预处理→Redis存储）：python D:\PythonProject\KPLuckyNumber\main.py process-articles --max 10
  - 四步流水线分析（新架构）：python D:\PythonProject\KPLuckyNumber\main.py pipeline --issue 2026166 --limit 50

关键配置点
- 全局配置文件：`D:\PythonProject\KPLuckyNumber\config.py`。
  - 数据库连接与 `DB_CONFIG`（host/user/password/database）——`modules/database_p5.py` 的 `connect()` 会尝试在缺少数据库时自动创建。
  - AI 接口：`AGNES_API_CONFIG`（api_url、api_key、model_name、timeout...）。若 `api_key` 为空，AI 路径会优雅跳过并记录警告。

核心模块与职责（可修改点）
- 抓取：`D:\PythonProject\KPLuckyNumber\modules\spider_p5.py`（增量抓取、页面解析、JS 混淆变量提取）
- 存储/模式：`D:\PythonProject\KPLuckyNumber\modules\database_p5.py`（自动建库建表、插入/查询历史、趋势、AI 报告、预测记录）
- Redis：`D:\PythonProject\KPLuckyNumber\modules\redis_client.py`（命名空间、备份/恢复、文章与原始数据缓存）
- 预测：`D:\PythonProject\KPLuckyNumber\modules\optimized_p5_predictor.py`（修正与 AI 融合）
- AI 分析器：`D:\PythonProject\KPLuckyNumber\modules\ernie_ai_analyzer.py`（构造 prompt、解析 JSON、写入 DB/文件）
- 文章处理（旧版兼容）：`D:\PythonProject\KPLuckyNumber\modules\article_processor.py`、`modules/article_analyzer.py`
- 四步流水线（★ 推荐）：`D:\PythonProject\KPLuckyNumber\modules\four_step_pipeline.py`

项目约定与实现细节（务必遵守）
- 日志：每个模块写入 `D:\PythonProject\KPLuckyNumber\logs\*.log`，查看日志可快速定位错误。不要在变更时移除或改名现有日志文件路径。
- 输出目录：预测文件写入 `D:\PythonProject\KPLuckyNumber\predictions\`，AI 报告写入 `D:\PythonProject\KPLuckyNumber\reports\`。
- Redis key 前缀：`kpluckynumber:pl5:`（见 `D:\PythonProject\KPLuckyNumber\modules\redis_client.py::get_key_prefix`）。示例键：`kpluckynumber:pl5:raw:20260101`、`kpluckynumber:pl5:article:list`。
- 预测器返回结构要求（保持兼容）: 字典必须包含键 `fused_probabilities`, `top_combinations`, `predict_time`, `predict_uuid`, `risk_warning`（多处代码与测试依赖这些字段）。

AI 调用与结果解析要点
- 两个模块（`optimized_p5_predictor.py` 与 `ernie_ai_analyzer.py`）通过 HTTP POST 调用 AI 模型。请求体中使用 `model` 与 `messages`（system/user）；参见 `modules/optimized_p5_predictor.py::_call_ai_model`。
- 模型回复解析策略：从返回文本中定位并提取第一个 JSON 对象（从第一个 '{' 到匹配的 '}'），然后解析为 JSON；不要假设返回纯 JSON。若你修改解析逻辑，必须同步修改两个模块。

编码/变更注意事项
- 保持“延迟/懒加载”模式：数据库、Redis、AI 客户端通常在函数内部导入以避免导入时失败；修改时尽量保留此模式。
- 若在 `database_p5.create_tables()` 调整 schema，务必兼容已有列与历史数据，且保持 `connect()` 的自动创建行为。
- 抓取器（`spider_p5.py`）依赖站点特定解析规则，改动时增加稳健性（重试、延迟、断言网页结构匹配）并保留原始抓取样本以便回溯。

运行与测试
- 安装依赖：在项目根目录运行：
  ```powershell
  pip install -r D:\PythonProject\KPLuckyNumber\requirements.txt
  ```
- 运行测试：
  ```powershell
  python -m pytest -q
  ```

重要文件列表（绝对路径，便于导航）
- D:\PythonProject\KPLuckyNumber\main.py
- D:\PythonProject\KPLuckyNumber\config.py
- D:\PythonProject\KPLuckyNumber\modules\database_p5.py
- D:\PythonProject\KPLuckyNumber\modules\optimized_p5_predictor.py
- D:\PythonProject\KPLuckyNumber\modules\ernie_ai_analyzer.py
- D:\PythonProject\KPLuckyNumber\modules\spider_p5.py
- D:\PythonProject\KPLuckyNumber\modules\redis_client.py
- D:\PythonProject\KPLuckyNumber\modules\four_step_pipeline.py       # ★ 推荐：四步串行流水线（v2.0）
- D:\PythonProject\KPLuckyNumber\modules\feature_engineering.py
- D:\PythonProject\KPLuckyNumber\modules\article_processor.py      # 旧版兼容
- D:\PythonProject\KPLuckyNumber\modules\article_analyzer.py       # 旧版兼容
- D:\PythonProject\KPLuckyNumber\modules\backtest_engine.py
- D:\PythonProject\KPLuckyNumber\modules\prediction_validator.py   # GUI 专用

仅凭此文件可安全开始修改：优先从 `optimized_p5_predictor.py` 做小幅改进并在本地用 `python main.py predict --model optimized` 验证输出格式与 `predictions/` 写入。其他常用验证：

- 运行特征提取并生成报告：`python main.py analyze`
- 运行 AI/综合 分析：`python main.py ernie --limit 30` 或 `python main.py comprehensive --limit 30`（已弃用）
- 处理单篇/批量文章工作流：`python main.py process-article --url "..."` / `python main.py process-articles --max 10`（旧版兼容）
- 四步流水线分析（推荐，v2.0）：`python main.py pipeline --issue 2026166`

—— 结束 ——


