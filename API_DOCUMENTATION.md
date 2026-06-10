# 七星彩数据分析系统 API 接口文档

## 概述

本文档详细描述了七星彩数据分析系统的所有API接口，包括接口路径、HTTP方法、请求参数、响应格式，以及与数据库表的映射关系。

---

## 数据库表结构概览

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `qxc_history_data` | 七星彩历史开奖数据表 | issue(期号)、draw_date(开奖日期)、num1-num6(前6位号码)、special_num(特别号)、hezhi(和值)、span(跨度) |
| `qxc_trend_data` | 七星彩走势图数据表 | issue(期号)、trend_values(走势图JSON数据) |
| `qxc_detailed_report` | 七星彩详细分析报告表 | report_uuid(唯一标识)、frequency_analysis(频率分析)、probability_analysis(概率分析)、total_samples(样本数) |
| `qxc_final_report` | 七星彩最终最优报告表 | detailed_report_id(关联详细报告)、recommended_numbers(推荐号码)、confidence_score(置信分数)、status(状态) |

---

## 一、数据采集与管理 API

### 1.1 爬取开奖数据

- **路径**: `POST /api/data/crawl`
- **功能**: 从官方网站爬取七星彩历史开奖数据
- **请求体**:
```json
{
  "qishu": 100,
  "trend": true
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| qishu | int | 否 | 获取期数（1-500），默认100 |
| trend | bool | 否 | 是否获取走势图数据，默认true |

- **响应示例**:
```json
{
  "success": true,
  "code": 200,
  "message": "数据爬取成功",
  "data": {
    "crawled_count": 100,
    "cleaned_count": 98,
    "stored_count": 98,
    "total_in_db": 120,
    "trend_data_count": 100,
    "crawl_time": "2024-01-15T10:30:00"
  }
}
```

### 1.2 获取开奖数据列表

- **路径**: `GET /api/data/list`
- **功能**: 分页获取七星彩历史开奖数据
- **映射表**: `qxc_history_data`
- **请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 否 | 1 | 页码 |
| page_size | int | 否 | 20 | 每页数量（1-100） |
| sort_by | string | 否 | issue | 排序字段（issue/draw_date） |
| sort_order | string | 否 | desc | 排序顺序（asc/desc） |

- **响应示例**:
```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "items": [
      {
        "id": 120,
        "issue": "2024010",
        "draw_date": "2024-01-15",
        "num1": 1,
        "num2": 5,
        "num3": 8,
        "num4": 12,
        "num5": 18,
        "num6": 25,
        "special_num": 7,
        "hezhi": "76",
        "hezhi_type": "even",
        "odd_even_ratio": "4:3",
        "odd_even_pattern": "OEOOEEO",
        "span": "24",
        "created_at": "2024-01-15T10:00:00",
        "updated_at": "2024-01-15T10:00:00"
      }
    ],
    "total": 120,
    "page": 1,
    "page_size": 20,
    "pages": 6
  }
}
```

### 1.3 获取单期数据

- **路径**: `GET /api/data/{issue}`
- **功能**: 根据期号获取单期开奖数据
- **映射表**: `qxc_history_data`
- **路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| issue | string | 期号 |

### 1.4 新增开奖数据

- **路径**: `POST /api/data/`
- **功能**: 新增一条七星彩开奖数据
- **映射表**: `qxc_history_data`
- **请求体**:
```json
{
  "issue": "2024011",
  "draw_date": "2024-01-17",
  "num1": 3,
  "num2": 7,
  "num3": 11,
  "num4": 15,
  "num5": 22,
  "num6": 28,
  "special_num": 9,
  "hezhi": "95",
  "hezhi_type": "odd",
  "odd_even_ratio": "4:3",
  "odd_even_pattern": "OOOEOEE",
  "span": "25"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| issue | string | 是 | 期号（唯一标识） |
| draw_date | string | 是 | 开奖日期 |
| num1-6 | int | 是 | 第1-6位号码（0-9） |
| special_num | int | 是 | 特别号码（0-9） |
| hezhi | string | 否 | 和值 |
| hezhi_type | string | 否 | 和值类型（odd/even） |
| odd_even_ratio | string | 否 | 奇偶比例 |
| odd_even_pattern | string | 否 | 奇偶模式 |
| span | string | 否 | 跨度 |

### 1.5 更新开奖数据

- **路径**: `PUT /api/data/{issue}`
- **功能**: 根据期号更新开奖数据
- **映射表**: `qxc_history_data`
- **路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| issue | string | 期号 |

- **请求体**: 同新增接口，但所有字段均为可选

### 1.6 删除单期数据

- **路径**: `DELETE /api/data/{issue}`
- **功能**: 根据期号删除单期开奖数据
- **映射表**: `qxc_history_data`

### 1.7 获取数据概览

- **路径**: `GET /api/data/summary`
- **功能**: 获取数据库中数据统计概览

### 1.8 走势图数据接口

| 方法 | 路径 | 功能 | 映射表 |
|------|------|------|--------|
| GET | `/api/data/trend/list` | 获取走势图数据列表 | qxc_trend_data |
| GET | `/api/data/trend/{issue}` | 获取单期走势图数据 | qxc_trend_data |
| POST | `/api/data/trend` | 新增走势图数据 | qxc_trend_data |
| DELETE | `/api/data/trend/{issue}` | 删除走势图数据 | qxc_trend_data |

---

## 二、概率分析 API

### 2.1 号码频率分析

- **路径**: `GET /api/analysis/frequency`
- **功能**: 分析各号码在每个位置的出现频率
- **响应示例**:
```json
{
  "success": true,
  "code": 200,
  "message": "分析成功",
  "data": {
    "total_samples": 120,
    "frequency_analysis": [
      {
        "number": 5,
        "frequency": 45,
        "probability": [0.35, 0.38, 0.42, 0.39, 0.41, 0.37, 0.40]
      }
    ],
    "analysis_time": "2024-01-15T11:00:00"
  }
}
```

### 2.2 间隔周期分析

- **路径**: `GET /api/analysis/interval`
- **功能**: 分析号码的间隔周期分布

### 2.3 和值分析

- **路径**: `GET /api/analysis/hezhi`
- **功能**: 分析和值分布情况

### 2.4 奇偶分析

- **路径**: `GET /api/analysis/odd_even`
- **功能**: 分析奇偶比例分布

### 2.5 跨度分析

- **路径**: `GET /api/analysis/span`
- **功能**: 分析跨度分布情况

### 2.6 重号分析

- **路径**: `GET /api/analysis/repeats`
- **功能**: 分析重号规律

### 2.7 连号分析

- **路径**: `GET /api/analysis/consecutive`
- **功能**: 分析连号规律

### 2.8 号码概率预测

- **路径**: `GET /api/analysis/predict`
- **功能**: 基于历史数据进行号码概率预测
- **请求参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| use_trend | bool | true | 是否使用走势图数据 |

### 2.9 综合分析

- **路径**: `GET /api/analysis/comprehensive`
- **功能**: 执行所有维度的综合分析

---

## 三、报告管理 API

### 3.1 生成分析报告

- **路径**: `POST /api/report/generate`
- **功能**: 生成概率分析报告
- **请求参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| report_types | list | ["detailed", "optimal"] | 报告类型列表 |
| use_trend | bool | true | 是否使用走势图数据 |

### 3.2 获取报告列表

- **路径**: `GET /api/report/list`
- **功能**: 分页获取报告列表
- **请求参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| report_type | string | 过滤类型（detailed/optimal） |
| page | int | 页码 |
| page_size | int | 每页数量 |

### 3.3 获取报告详情

- **路径**: `GET /api/report/{report_id}`
- **功能**: 根据报告ID获取报告详情

### 3.4 新增详细报告

- **路径**: `POST /api/report/detailed`
- **功能**: 手动新增详细分析报告
- **映射表**: `qxc_detailed_report`
- **请求体**:
```json
{
  "report_date": "2024-01-15",
  "report_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "raw_data_snapshot": "{...}",
  "calculation_steps": "[...]",
  "analysis_params": "{...}",
  "frequency_analysis": "{...}",
  "probability_analysis": "{...}",
  "interval_analysis": "{...}",
  "hezhi_analysis": "{...}",
  "odd_even_analysis": "{...}",
  "span_analysis": "{...}",
  "total_samples": 120,
  "confidence_level": 0.95,
  "report_content": "..."
}
```

### 3.5 新增最终报告

- **路径**: `POST /api/report/final`
- **功能**: 手动新增最终最优报告
- **映射表**: `qxc_final_report`
- **请求体**:
```json
{
  "detailed_report_id": 1,
  "report_date": "2024-01-15",
  "report_uuid": "550e8400-e29b-41d4-a716-446655441111",
  "recommended_numbers": "03 07 11 15 22 28 + 09",
  "confidence_score": 0.85,
  "analysis_summary": "...",
  "key_conclusions": "...",
  "core_metrics": "{...}",
  "decision_recommendations": "...",
  "report_content": "...",
  "status": "validated"
}
```

### 3.6 更新报告

- **路径**: `PUT /api/report/{report_id}`
- **功能**: 根据报告ID更新报告
- **映射表**: `qxc_final_report`

### 3.7 删除报告

- **路径**: `DELETE /api/report/{report_id}`
- **功能**: 根据报告ID删除报告

### 3.8 报告统计

- **路径**: `GET /api/report/summary`
- **功能**: 获取报告统计信息

---

## 四、系统管理 API

### 4.1 系统状态

- **路径**: `GET /api/system/status`
- **功能**: 获取系统状态信息

### 4.2 获取配置

- **路径**: `GET /api/system/config`
- **功能**: 获取系统配置信息

### 4.3 初始化数据库

- **路径**: `POST /api/system/init`
- **功能**: 初始化数据库表结构

### 4.4 清理数据

- **路径**: `POST /api/system/clean?confirm=true`
- **功能**: 清理所有数据（谨慎使用）

### 4.5 获取日志

- **路径**: `GET /api/system/logs`
- **请求参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| log_type | string | database | 日志类型（database/analyzer） |
| lines | int | 50 | 获取行数（1-200） |

---

## 五、API-数据库映射关系

### 5.1 数据采集与管理

| API接口 | 数据库表 | 操作类型 |
|---------|----------|----------|
| GET /api/data/list | qxc_history_data | SELECT |
| GET /api/data/{issue} | qxc_history_data | SELECT |
| POST /api/data/ | qxc_history_data | INSERT |
| PUT /api/data/{issue} | qxc_history_data | UPDATE |
| DELETE /api/data/{issue} | qxc_history_data | DELETE |
| GET /api/data/trend/list | qxc_trend_data | SELECT |
| POST /api/data/trend | qxc_trend_data | INSERT |
| DELETE /api/data/trend/{issue} | qxc_trend_data | DELETE |

### 5.2 报告管理

| API接口 | 数据库表 | 操作类型 |
|---------|----------|----------|
| POST /api/report/generate | qxc_detailed_report, qxc_final_report | INSERT |
| GET /api/report/list | qxc_detailed_report, qxc_final_report | SELECT |
| GET /api/report/{id} | qxc_detailed_report, qxc_final_report | SELECT |
| POST /api/report/detailed | qxc_detailed_report | INSERT |
| POST /api/report/final | qxc_final_report | INSERT |
| PUT /api/report/{id} | qxc_final_report | UPDATE |
| DELETE /api/report/{id} | qxc_detailed_report, qxc_final_report | DELETE |

---

## 六、数据完整性约束

### 6.1 主键约束

| 表名 | 主键字段 |
|------|----------|
| qxc_history_data | id |
| qxc_trend_data | id |
| qxc_detailed_report | id |
| qxc_final_report | id |

### 6.2 唯一约束

| 表名 | 唯一字段 |
|------|----------|
| qxc_history_data | issue |
| qxc_trend_data | issue |
| qxc_detailed_report | report_uuid |
| qxc_final_report | report_uuid |

### 6.3 外键约束

| 表名 | 外键字段 | 关联表 | 关联字段 | 删除行为 | 更新行为 |
|------|----------|--------|----------|----------|----------|
| qxc_final_report | detailed_report_id | qxc_detailed_report | id | CASCADE | RESTRICT |

### 6.4 索引

| 表名 | 索引字段 | 索引类型 |
|------|----------|----------|
| qxc_history_data | issue | UNIQUE |
| qxc_history_data | idx_issue | INDEX |
| qxc_history_data | idx_draw_date | INDEX |
| qxc_trend_data | issue | UNIQUE |
| qxc_trend_data | idx_issue | INDEX |
| qxc_detailed_report | report_uuid | UNIQUE |
| qxc_detailed_report | idx_report_date | INDEX |
| qxc_detailed_report | idx_report_uuid | INDEX |
| qxc_final_report | report_uuid | UNIQUE |
| qxc_final_report | idx_final_report_date | INDEX |
| qxc_final_report | idx_final_report_uuid | INDEX |
| qxc_final_report | idx_detailed_report_id | INDEX |

---

## 七、错误响应格式

所有API接口的错误响应统一格式：

```json
{
  "success": false,
  "code": 400,
  "message": "错误描述信息",
  "data": null
}
```

### 常见错误码

| 错误码 | 说明 |
|--------|------|
| 400 | 请求参数错误 |
| 404 | 资源未找到 |
| 409 | 资源冲突（如期号已存在） |
| 500 | 服务器内部错误 |

---

## 八、响应成功格式

所有API接口的成功响应统一格式：

```json
{
  "success": true,
  "code": 200,
  "message": "操作成功描述",
  "data": {...}
}
```

### 常见成功码

| 成功码 | 说明 |
|--------|------|
| 200 | 查询/更新/删除成功 |
| 201 | 创建成功 |

---

## 九、数据验证规则

### 9.1 开奖数据验证

| 字段 | 验证规则 |
|------|----------|
| issue | 字符串，最大20字符，唯一 |
| draw_date | 日期格式，如 YYYY-MM-DD |
| num1-num6 | 整数，0-9 |
| special_num | 整数，0-9 |
| hezhi | 字符串，最大10字符 |
| span | 字符串，最大10字符 |

### 9.2 报告数据验证

| 字段 | 验证规则 |
|------|----------|
| report_uuid | UUID格式，36字符，唯一 |
| confidence_score | 小数，0-1 |
| status | 枚举值：draft/validated/published |

---

## 十、附录

### 10.1 数据库表字段类型说明

| 类型 | MySQL类型 | 说明 |
|------|-----------|------|
| VARCHAR(n) | VARCHAR(n) | 可变长度字符串 |
| INT | INT | 整数 |
| TEXT | TEXT | 文本内容 |
| LONGTEXT | LONGTEXT | 长文本内容 |
| LONGBLOB | LONGBLOB | 二进制大对象 |
| DECIMAL(m,n) | DECIMAL(m,n) | 定点数 |
| TIMESTAMP | TIMESTAMP | 时间戳 |
| ENUM | ENUM | 枚举类型 |

### 10.2 报告状态说明

| 状态 | 说明 |
|------|------|
| draft | 草稿状态 |
| validated | 已验证状态 |
| published | 已发布状态 |

---

**文档版本**: v1.0  
**生成日期**: 2024年1月  
**适用系统**: 七星彩数据分析系统