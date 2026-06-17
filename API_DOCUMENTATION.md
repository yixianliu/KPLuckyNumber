# 七星彩数字概率分析系统 - API 文档

## 目录

- [概述](#概述)
- [通用响应格式](#通用响应格式)
- [错误码](#错误码)
- [数据采集与管理](#数据采集与管理)
- [概率分析](#概率分析)
- [报告管理](#报告管理)
- [系统管理](#系统管理)
- [用户认证与付费](#用户认证与付费)
- [数据模型](#数据模型)
- [快速开始](#快速开始)

---

## 概述

七星彩数字概率分析系统 API，提供数据采集、概率分析、报告生成等功能。

- **Base URL**: `http://localhost:8000`
- **Content-Type**: `application/json`
- **在线文档**: `http://localhost:8000/docs` (Swagger UI)

---

## 通用响应格式

所有 API 返回统一的响应结构：

```json
{
  "success": true,      // 请求是否成功
  "code": 200,          // 状态码
  "message": "查询成功", // 响应消息
  "data": { ... }       // 响应数据（具体结构因接口而异）
}
```

**HTTP 状态码**:

| 状态码 | 说明 |
|--------|------|
| 200 | 请求成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未授权（未登录或token过期） |
| 404 | 资源未找到 |
| 500 | 服务器内部错误 |

---

## 错误码

| 错误码 | 说明 | 常见场景 |
|--------|------|----------|
| 200 | 成功 | 所有成功请求 |
| 201 | 创建成功 | 新增数据成功 |
| 400 | 参数错误 | 缺少必填参数或参数格式错误 |
| 401 | 未授权 | 未登录或token过期 |
| 404 | 未找到 | 查询的数据不存在 |
| 500 | 服务器错误 | 数据库连接失败或内部异常 |

---

## 数据采集与管理

> **路由前缀**: `/api/data`

### 1. 爬取数据

```
POST /api/data/crawl
```

从数据源爬取七星彩历史开奖数据。

**请求体** (`application/json`):

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| qishu | int | 否 | 200 | 爬取期数（1-500） |
| trend | bool | 否 | true | 是否爬取走势图数据 |

**请求示例**:

```json
{
  "qishu": 200,
  "trend": true
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "数据爬取成功",
  "data": {
    "crawled_count": 200,
    "trend_count": 200,
    "period": "2026001-2026200",
    "crawled_at": "2026-06-15T10:00:00"
  }
}
```

---

### 2. 获取开奖数据列表

```
GET /api/data/list
```

分页获取七星彩历史开奖数据。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 否 | 1 | 页码 |
| page_size | int | 否 | 20 | 每页数量（1-100） |

**请求示例**:

```
GET /api/data/list?page=1&page_size=10
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "items": [
      {
        "id": 1,
        "issue": "2026001",
        "draw_date": "2026-01-01",
        "num1": 3,
        "num2": 7,
        "num3": 1,
        "num4": 9,
        "num5": 5,
        "num6": 2,
        "special_num": 8,
        "hezhi": "27",
        "hezhi_type": "奇",
        "odd_even_ratio": "4:3",
        "odd_even_pattern": "OEOOEEO",
        "span": "8",
        "created_at": "2026-01-01T20:30:00",
        "updated_at": "2026-01-01T20:30:00"
      }
    ],
    "total": 500,
    "page": 1,
    "page_size": 10,
    "pages": 50
  }
}
```

---

### 3. 获取单期数据

```
GET /api/data/{issue}
```

根据期号获取单期开奖数据详情。

**路径参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| issue | string | 是 | 期号 |

**请求示例**:

```
GET /api/data/2026001
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "id": 1,
    "issue": "2026001",
    "draw_date": "2026-01-01",
    "num1": 3,
    "num2": 7,
    "num3": 1,
    "num4": 9,
    "num5": 5,
    "num6": 2,
    "special_num": 8,
    "hezhi": "27",
    "created_at": "2026-01-01T20:30:00",
    "updated_at": "2026-01-01T20:30:00"
  }
}
```

---

### 4. 新增开奖数据

```
POST /api/data/
```

手动新增一条七星彩开奖数据。

**请求体** (`application/json`):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| issue | string | 是 | 期号（唯一标识，最长20字符） |
| draw_date | string | 是 | 开奖日期（最长20字符） |
| num1 | int | 是 | 第一位号码（0-9） |
| num2 | int | 是 | 第二位号码（0-9） |
| num3 | int | 是 | 第三位号码（0-9） |
| num4 | int | 是 | 第四位号码（0-9） |
| num5 | int | 是 | 第五位号码（0-9） |
| num6 | int | 是 | 第六位号码（0-9） |
| special_num | int | 是 | 特别号码（0-9） |
| hezhi | string | 否 | 和值 |
| hezhi_type | string | 否 | 和值类型（奇/偶） |
| odd_even_ratio | string | 否 | 奇偶比例 |
| odd_even_pattern | string | 否 | 奇偶模式 |
| span | string | 否 | 跨度 |

**请求示例**:

```json
{
  "issue": "2026099",
  "draw_date": "2026-06-15",
  "num1": 3,
  "num2": 7,
  "num3": 1,
  "num4": 9,
  "num5": 5,
  "num6": 2,
  "special_num": 8
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 201,
  "message": "新增成功",
  "data": {
    "issue": "2026099",
    "draw_date": "2026-06-15",
    "num1": 3,
    "num2": 7,
    "num3": 1,
    "num4": 9,
    "num5": 5,
    "num6": 2,
    "special_num": 8,
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 5. 更新开奖数据

```
PUT /api/data/{issue}
```

根据期号更新开奖数据（仅更新请求体中非 null 的字段）。

**路径参数**: issue (string) - 期号

**请求体**: 与新增接口相同，所有字段均为可选。

**请求示例**:

```json
{
  "num1": 5,
  "hezhi": "30"
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "更新成功",
  "data": {
    "issue": "2026099",
    "affected_rows": 1,
    "updated_fields": ["num1", "hezhi"]
  }
}
```

---

### 6. 删除单期数据

```
DELETE /api/data/{issue}
```

根据期号删除单期开奖数据。

**路径参数**: issue (string) - 期号

**请求示例**:

```
DELETE /api/data/2026099
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "删除成功",
  "data": {
    "deleted_issue": "2026099",
    "affected_rows": 1
  }
}
```

---

### 7. 获取数据概览

```
GET /api/data/summary
```

获取数据库中数据统计概览。

**请求示例**:

```
GET /api/data/summary
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "history_data_count": 500,
    "trend_data_count": 120
  }
}
```

---

### 8. 获取走势图数据列表

```
GET /api/data/trend/list
```

分页获取走势图数据。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 否 | 1 | 页码 |
| page_size | int | 否 | 20 | 每页数量（1-100） |

**请求示例**:

```
GET /api/data/trend/list?page=1&page_size=20
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "items": [
      {
        "id": 1,
        "issue": "2026001",
        "trend_values": "{\"num1\":3,\"num2\":7,...}",
        "created_at": "2026-01-01T20:30:00",
        "updated_at": "2026-01-01T20:30:00"
      }
    ],
    "total": 120,
    "page": 1,
    "page_size": 20,
    "pages": 6
  }
}
```

---

### 9. 获取单期走势图数据

```
GET /api/data/trend/{issue}
```

根据期号获取走势图数据。

**路径参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| issue | string | 是 | 期号 |

**请求示例**:

```
GET /api/data/trend/2026001
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "id": 1,
    "issue": "2026001",
    "trend_values": "{\"num1\":3,\"num2\":7,\"num3\":1,\"num4\":9,\"num5\":5,\"num6\":2,\"special_num\":8}",
    "created_at": "2026-01-01T20:30:00",
    "updated_at": "2026-01-01T20:30:00"
  }
}
```

---

### 10. 新增走势图数据

```
POST /api/data/trend
```

**请求体** (`application/json`):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| issue | string | 是 | 期号（关联开奖数据） |
| trend_values | string | 是 | 走势图数据 JSON 字符串 |

**请求示例**:

```json
{
  "issue": "2026099",
  "trend_values": "{\"num1\":3,\"num2\":7,\"num3\":1,\"num4\":9,\"num5\":5,\"num6\":2,\"special_num\":8}"
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 201,
  "message": "新增成功",
  "data": {
    "id": 121,
    "issue": "2026099",
    "trend_values": "{\"num1\":3,...}",
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 11. 删除走势图数据

```
DELETE /api/data/trend/{issue}
```

根据期号删除走势图数据。

**路径参数**: issue (string) - 期号

**请求示例**:

```
DELETE /api/data/trend/2026099
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "删除成功",
  "data": {
    "deleted_issue": "2026099",
    "affected_rows": 1
  }
}
```

---

## 概率分析

> **路由前缀**: `/api/analysis`  
> **重要声明**: 所有分析接口仅提供历史数据的统计描述，不构成任何投注建议。彩票开奖为独立随机事件，所有号码的理论出现概率均等。

### 1. 号码频率分析

```
GET /api/analysis/frequency
```

分析各号码在每个位置的出现频率，以及与理论概率的偏离程度。

**请求示例**:

```
GET /api/analysis/frequency
```

**响应 data 字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| total_samples | int | 分析样本总数（期数） |
| frequency_analysis | object | 各位置频率分析结果 |
| analysis_time | string | 分析时间 |

**frequency_analysis 结构**（按位置索引 0-6）:

```json
{
  "0": {
    "position_name": "第一位",
    "number_stats": {
      "0": {
        "frequency": 48,
        "observed_probability": 0.096,
        "theoretical_probability": 0.1,
        "deviation_rate": -0.04,
        "expected_count": 50.0
      }
    },
    "most_frequent": [[3, 62], [7, 58], [1, 55]],
    "least_frequent": [[0, 38], [9, 40], [5, 42]]
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "frequency_analysis": {
      "0": {
        "position_name": "第一位",
        "number_stats": {
          "0": {"frequency": 48, "observed_probability": 0.096, "theoretical_probability": 0.1, "deviation_rate": -0.04, "expected_count": 50.0},
          "1": {"frequency": 55, "observed_probability": 0.11, "theoretical_probability": 0.1, "deviation_rate": 0.1, "expected_count": 50.0}
        },
        "most_frequent": [[3, 62], [7, 58], [1, 55]],
        "least_frequent": [[0, 38], [9, 40], [5, 42]]
      }
    },
    "analysis_time": "2026-06-15T10:00:00"
  }
}
```

---

### 2. 遗漏值分析

```
GET /api/analysis/omission
```

分析各号码的遗漏值（自上次出现以来的期数），是彩票分析的核心指标。

**请求示例**:

```
GET /api/analysis/omission
```

**响应 data 字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| total_samples | int | 分析样本总数 |
| omission_analysis | object | 各位置遗漏分析结果 |

**omission_analysis 结构**（按位置索引 0-6）:

```json
{
  "0": {
    "position_name": "第一位",
    "total_periods": 500,
    "number_stats": {
      "0": {
        "current_omission": 12,
        "max_omission": 35,
        "avg_omission": 9.8,
        "omission_ratio": 1.22,
        "total_occurrences": 48,
        "occurrence_rate": 0.096
      }
    }
  }
}
```

**遗漏指标说明**:

| 指标 | 说明 |
|------|------|
| current_omission | 当前遗漏：自上次出现以来的期数 |
| max_omission | 最大遗漏：历史最大遗漏期数 |
| avg_omission | 平均遗漏：历史平均遗漏期数 |
| omission_ratio | 遗漏比率：当前遗漏 / 平均遗漏（>1.5 为冷号，<0.5 为热号） |

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "omission_analysis": {
      "0": {
        "position_name": "第一位",
        "total_periods": 500,
        "number_stats": {
          "0": {"current_omission": 12, "max_omission": 35, "avg_omission": 9.8, "omission_ratio": 1.22, "total_occurrences": 48, "occurrence_rate": 0.096},
          "1": {"current_omission": 2, "max_omission": 28, "avg_omission": 8.1, "omission_ratio": 0.25, "total_occurrences": 55, "occurrence_rate": 0.11}
        }
      }
    }
  }
}
```

---

### 3. 冷热号分析

```
GET /api/analysis/hot_cold
```

基于遗漏值和近期频率进行冷热号分级。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| recent_n | int | 否 | 30 | 近期统计期数 |

**请求示例**:

```
GET /api/analysis/hot_cold?recent_n=50
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "recent_periods": 50,
  "hot_cold_analysis": {
    "0": {
      "position_name": "第一位",
      "hot_numbers": [
        {"number": 3, "heat_score": 1.35, "current_omission": 2, "recent_count": 8}
      ],
      "warm_numbers": [...],
      "cold_numbers": [
        {"number": 0, "heat_score": 0.42, "current_omission": 15, "recent_count": 1}
      ],
      "theory_recent_count": 5.0
    }
  }
}
```

**分级标准**:

| 级别 | 条件 |
|------|------|
| 热号 (hot) | 遗漏比率 <= 0.5，或热度评分 >= 1.2 |
| 温号 (warm) | 介于热号和冷号之间 |
| 冷号 (cold) | 遗漏比率 >= 1.5，或热度评分 <= 0.6 |

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "recent_periods": 50,
    "hot_cold_analysis": {
      "0": {
        "position_name": "第一位",
        "hot_numbers": [
          {"number": 3, "heat_score": 1.35, "current_omission": 2, "recent_count": 8},
          {"number": 7, "heat_score": 1.28, "current_omission": 1, "recent_count": 7}
        ],
        "warm_numbers": [
          {"number": 1, "heat_score": 0.95, "current_omission": 5, "recent_count": 5}
        ],
        "cold_numbers": [
          {"number": 0, "heat_score": 0.42, "current_omission": 15, "recent_count": 1}
        ],
        "theory_recent_count": 5.0
      }
    }
  }
}
```

---

### 4. 012路分析

```
GET /api/analysis/path_012
```

分析012路（除3余数）分布。

**012路定义**:

| 路数 | 余数 | 包含号码 |
|------|------|----------|
| 0路 | num % 3 == 0 | 0, 3, 6, 9 |
| 1路 | num % 3 == 1 | 1, 4, 7 |
| 2路 | num % 3 == 2 | 2, 5, 8 |

**请求示例**:

```
GET /api/analysis/path_012
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "path_012_analysis": {
    "0": {
      "position_name": "第一位",
      "path_stats": {
        "0": {"count": 180, "probability": 0.36, "numbers": [0, 3, 6, 9]},
        "1": {"count": 165, "probability": 0.33, "numbers": [1, 4, 7]},
        "2": {"count": 155, "probability": 0.31, "numbers": [2, 5, 8]}
      },
      "total_samples": 500
    }
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "path_012_analysis": {
      "0": {
        "position_name": "第一位",
        "path_stats": {
          "0": {"count": 180, "probability": 0.36, "numbers": [0, 3, 6, 9]},
          "1": {"count": 165, "probability": 0.33, "numbers": [1, 4, 7]},
          "2": {"count": 155, "probability": 0.31, "numbers": [2, 5, 8]}
        },
        "total_samples": 500
      }
    }
  }
}
```

---

### 5. 大小比分析

```
GET /api/analysis/big_small
```

分析大小号分布。

**大小号定义**:

| 位置 | 小号范围 | 大号范围 |
|------|----------|----------|
| 前6位 (0-5) | 0-4 | 5-9 |
| 特别号 (6) | 0-6 | 7-14 |

**请求示例**:

```
GET /api/analysis/big_small
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "big_small_analysis": {
    "0": {
      "position_name": "第一位",
      "big_small_threshold": 5,
      "big_count": 245,
      "small_count": 255,
      "big_probability": 0.49,
      "small_probability": 0.51,
      "big_numbers": {"5": 52, "6": 50, "7": 48, "8": 47, "9": 48},
      "small_numbers": {"0": 50, "1": 51, "2": 52, "3": 51, "4": 51},
      "total_samples": 500
    }
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "big_small_analysis": {
      "0": {
        "position_name": "第一位",
        "big_small_threshold": 5,
        "big_count": 245,
        "small_count": 255,
        "big_probability": 0.49,
        "small_probability": 0.51,
        "big_numbers": {"5": 52, "6": 50, "7": 48, "8": 47, "9": 48},
        "small_numbers": {"0": 50, "1": 51, "2": 52, "3": 51, "4": 51},
        "total_samples": 500
      }
    }
  }
}
```

---

### 6. 奇偶分析

```
GET /api/analysis/odd_even
```

分析奇偶比例分布，包含各位置奇偶分布及整体模式统计。

**请求示例**:

```
GET /api/analysis/odd_even
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "odd_even_analysis": {
    "0": {
      "position_name": "第一位",
      "odd_count": 252,
      "even_count": 248,
      "odd_probability": 0.504,
      "even_probability": 0.496,
      "odd_numbers": {"1": 55, "3": 52, "5": 50, "7": 48, "9": 47},
      "even_numbers": {"0": 50, "2": 52, "4": 51, "6": 48, "8": 47}
    },
    "overall": {
      "pattern_distribution": {
        "OOEOEE": 25,
        "EOEOOE": 22
      },
      "total_samples": 500
    }
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "odd_even_analysis": {
      "0": {
        "position_name": "第一位",
        "odd_count": 252,
        "even_count": 248,
        "odd_probability": 0.504,
        "even_probability": 0.496,
        "odd_numbers": {"1": 55, "3": 52, "5": 50, "7": 48, "9": 47},
        "even_numbers": {"0": 50, "2": 52, "4": 51, "6": 48, "8": 47}
      },
      "overall": {
        "pattern_distribution": {
          "OOEOEE": 25,
          "EOEOOE": 22
        },
        "total_samples": 500
      }
    }
  }
}
```

---

### 7. 和值分析

```
GET /api/analysis/hezhi
```

分析前6位号码和值分布情况，包含与理论期望值的对比。

**请求示例**:

```
GET /api/analysis/hezhi
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "hezhi_analysis": {
    "total_samples": 500,
    "avg_hezhi": 27.15,
    "max_hezhi": 52,
    "min_hezhi": 3,
    "theory_avg": 27.0,
    "deviation_from_theory": 0.15,
    "range_distribution": {
      "0-9": {"count": 15, "probability": 0.03},
      "10-19": {"count": 85, "probability": 0.17},
      "20-29": {"count": 200, "probability": 0.40},
      "30-39": {"count": 150, "probability": 0.30},
      "40-49": {"count": 45, "probability": 0.09},
      "50-54": {"count": 5, "probability": 0.01}
    }
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "hezhi_analysis": {
      "total_samples": 500,
      "avg_hezhi": 27.15,
      "max_hezhi": 52,
      "min_hezhi": 3,
      "theory_avg": 27.0,
      "deviation_from_theory": 0.15,
      "range_distribution": {
        "0-9": {"count": 15, "probability": 0.03},
        "10-19": {"count": 85, "probability": 0.17},
        "20-29": {"count": 200, "probability": 0.40},
        "30-39": {"count": 150, "probability": 0.30},
        "40-49": {"count": 45, "probability": 0.09},
        "50-54": {"count": 5, "probability": 0.01}
      }
    }
  }
}
```

---

### 8. 跨度分析

```
GET /api/analysis/span
```

分析前6位号码的跨度（最大值 - 最小值）分布。

**请求示例**:

```
GET /api/analysis/span
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "span_analysis": {
    "total_samples": 500,
    "avg_span": 7.8,
    "max_span": 9,
    "min_span": 2,
    "span_distribution": {
      "2": {"count": 5, "probability": 0.01},
      "3": {"count": 12, "probability": 0.024},
      "9": {"count": 85, "probability": 0.17}
    }
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "span_analysis": {
      "total_samples": 500,
      "avg_span": 7.8,
      "max_span": 9,
      "min_span": 2,
      "span_distribution": {
        "2": {"count": 5, "probability": 0.01},
        "3": {"count": 12, "probability": 0.024},
        "9": {"count": 85, "probability": 0.17}
      }
    }
  }
}
```

---

### 9. 重号分析

```
GET /api/analysis/repeats
```

分析相邻期重号规律。

**请求示例**:

```
GET /api/analysis/repeats
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "repeat_analysis": {
    "total_pairs": 499,
    "repeat_distribution": {
      "0": {"count": 120, "probability": 0.24},
      "1": {"count": 180, "probability": 0.36},
      "2": {"count": 120, "probability": 0.24},
      "3": {"count": 60, "probability": 0.12}
    },
    "avg_repeats": 1.5,
    "max_repeats": 5,
    "min_repeats": 0
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "repeat_analysis": {
      "total_pairs": 499,
      "repeat_distribution": {
        "0": {"count": 120, "probability": 0.24},
        "1": {"count": 180, "probability": 0.36},
        "2": {"count": 120, "probability": 0.24},
        "3": {"count": 60, "probability": 0.12}
      },
      "avg_repeats": 1.5,
      "max_repeats": 5,
      "min_repeats": 0
    }
  }
}
```

---

### 10. 连号分析

```
GET /api/analysis/consecutive
```

分析连号出现规律。

**请求示例**:

```
GET /api/analysis/consecutive
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "consecutive_analysis": {
    "total_samples": 500,
    "consecutive_distribution": {
      "1": {"count": 280, "probability": 0.56},
      "2": {"count": 150, "probability": 0.30},
      "3": {"count": 50, "probability": 0.10}
    },
    "avg_max_streak": 1.6,
    "has_consecutive_probability": 0.44
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "consecutive_analysis": {
      "total_samples": 500,
      "consecutive_distribution": {
        "1": {"count": 280, "probability": 0.56},
        "2": {"count": 150, "probability": 0.30},
        "3": {"count": 50, "probability": 0.10}
      },
      "avg_max_streak": 1.6,
      "has_consecutive_probability": 0.44
    }
  }
}
```

---

### 11. 位置相关性分析

```
GET /api/analysis/correlation
```

分析各位置号码之间的皮尔逊相关系数，验证位置独立性。

**请求示例**:

```
GET /api/analysis/correlation
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "correlation_analysis": {
    "position_names": ["第一位", "第二位", "第三位", "第四位", "第五位", "第六位", "特别号"],
    "correlation_matrix": {
      "0": {"0": 1.0, "1": 0.012, "2": -0.008, "3": 0.005, "4": -0.015, "5": 0.003, "6": 0.01},
      "1": {"0": 0.012, "1": 1.0, "2": 0.007, ...}
    },
    "total_samples": 500,
    "note": "相关系数接近0表示位置间基本独立，符合随机性假设"
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "correlation_analysis": {
      "position_names": ["第一位", "第二位", "第三位", "第四位", "第五位", "第六位", "特别号"],
      "correlation_matrix": {
        "0": {"0": 1.0, "1": 0.012, "2": -0.008, "3": 0.005, "4": -0.015, "5": 0.003, "6": 0.01},
        "1": {"0": 0.012, "1": 1.0, "2": 0.007, "3": -0.003, "4": 0.008, "5": -0.012, "6": 0.005}
      },
      "total_samples": 500,
      "note": "相关系数接近0表示位置间基本独立，符合随机性假设"
    }
  }
}
```

---

### 12. 随机性检验

```
GET /api/analysis/randomness
```

对历史数据进行随机性检验，包含卡方均匀性检验和连号随机性评估。

**请求示例**:

```
GET /api/analysis/randomness
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "randomness_test": {
    "chi_square_test": {
      "0": {
        "position_name": "第一位",
        "chi_square": 8.5,
        "degrees_of_freedom": 9,
        "expected_frequency": 50.0,
        "interpretation": "数据分布基本均匀"
      }
    },
    "consecutive_analysis": {
      "observed_prob": 0.44,
      "theory_approx": 0.55,
      "interpretation": "连号出现频率正常"
    },
    "overall_assessment": "历史数据整体呈现随机分布特征，各位置号码基本独立",
    "disclaimer": "彩票开奖为独立随机事件，历史数据统计特征不代表未来趋势"
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "randomness_test": {
      "chi_square_test": {
        "0": {
          "position_name": "第一位",
          "chi_square": 8.5,
          "degrees_of_freedom": 9,
          "expected_frequency": 50.0,
          "interpretation": "数据分布基本均匀"
        }
      },
      "consecutive_analysis": {
        "observed_prob": 0.44,
        "theory_approx": 0.55,
        "interpretation": "连号出现频率正常"
      },
      "overall_assessment": "历史数据整体呈现随机分布特征，各位置号码基本独立",
      "disclaimer": "彩票开奖为独立随机事件，历史数据统计特征不代表未来趋势"
    }
  }
}
```

---

### 13. 位置级综合分析

```
GET /api/analysis/position_analysis
```

按位置进行综合分析，返回每个位置各号码的综合热度评分。

**请求示例**:

```
GET /api/analysis/position_analysis
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "position_analysis": {
    "0": {
      "position_name": "第一位",
      "theory_prob": 0.1,
      "number_analysis": {
        "3": {
          "observed_probability": 0.124,
          "theoretical_probability": 0.1,
          "deviation_rate": 0.24,
          "current_omission": 2,
          "avg_omission": 8.1,
          "omission_ratio": 0.25,
          "heat_score": 62.5,
          "category": "hot"
        }
      },
      "hot_numbers": [3, 7, 1],
      "warm_numbers": [5, 8, 2, 9, 4, 6],
      "cold_numbers": [0],
      "sorted_by_heat": [[3, 62.5], [7, 58.3], [1, 55.1]]
    }
  },
  "analysis_time": "2026-06-15T10:00:00.000000"
}
```

**热度评分算法**:

```
heat_score = (1 + deviation_rate) * 40     // 频率偏离权重 40%
           + (1 / (1 + omission_ratio)) * 35  // 遗漏偏差权重 35%
           + (observed_prob / theory_prob) * 25  // 出现率权重 25%
```

| 级别 | 评分范围 |
|------|----------|
| 热号 (hot) | >= 60 |
| 温号 (warm) | 40 - 60 |
| 冷号 (cold) | <= 40 |

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "position_analysis": {
      "0": {
        "position_name": "第一位",
        "theory_prob": 0.1,
        "number_analysis": {
          "3": {
            "observed_probability": 0.124,
            "theoretical_probability": 0.1,
            "deviation_rate": 0.24,
            "current_omission": 2,
            "avg_omission": 8.1,
            "omission_ratio": 0.25,
            "heat_score": 62.5,
            "category": "hot"
          }
        },
        "hot_numbers": [3, 7, 1],
        "warm_numbers": [5, 8, 2, 9, 4, 6],
        "cold_numbers": [0],
        "sorted_by_heat": [[3, 62.5], [7, 58.3], [1, 55.1]]
      }
    },
    "analysis_time": "2026-06-15T10:00:00.000000"
  }
}
```

---

### 14. 综合分析

```
GET /api/analysis/comprehensive
```

执行所有维度的综合分析，返回各维度的摘要数据。

**请求示例**:

```
GET /api/analysis/comprehensive
```

**响应 data 字段**:

```json
{
  "total_samples": 500,
  "analysis_time": "2026-06-15T10:00:00.000000",
  "methodology_note": "本分析基于历史数据统计，所有号码的理论出现概率均等。",
  "position_analysis_summary": {
    "0": {
      "position_name": "第一位",
      "hot_numbers": [3, 7, 1],
      "cold_numbers": [0],
      "theory_prob": 0.1
    }
  },
  "frequency_summary": { ... },
  "omission_summary": { ... },
  "hezhi": { ... },
  "span": { ... },
  "repeats": { ... },
  "consecutive": { ... },
  "randomness": {
    "overall_assessment": "...",
    "chi_square_summary": { ... }
  },
  "correlation": {
    "note": "相关系数接近0表示位置间基本独立，符合随机性假设",
    "position_names": ["第一位", ...]
  }
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "分析完成",
  "data": {
    "total_samples": 500,
    "analysis_time": "2026-06-15T10:00:00.000000",
    "methodology_note": "本分析基于历史数据统计，所有号码的理论出现概率均等。",
    "position_analysis_summary": {
      "0": {
        "position_name": "第一位",
        "hot_numbers": [3, 7, 1],
        "cold_numbers": [0],
        "theory_prob": 0.1
      }
    },
    "frequency_summary": {
      "0": {"most_frequent": [[3, 62], [7, 58]], "least_frequent": [[0, 38], [9, 40]]}
    },
    "omission_summary": {
      "0": {"max_current_omission": 15, "min_current_omission": 1}
    },
    "hezhi": {"avg": 27.15, "theory_avg": 27.0},
    "span": {"avg": 7.8, "max": 9, "min": 2},
    "repeats": {"avg": 1.5, "max": 5},
    "consecutive": {"has_consecutive_prob": 0.44},
    "randomness": {
      "overall_assessment": "历史数据整体呈现随机分布特征，各位置号码基本独立",
      "chi_square_summary": {"0": {"interpretation": "数据分布基本均匀"}}
    },
    "correlation": {
      "note": "相关系数接近0表示位置间基本独立，符合随机性假设",
      "position_names": ["第一位", "第二位", "第三位", "第四位", "第五位", "第六位", "特别号"]
    }
  }
}
```

---

## 报告管理

> **路由前缀**: `/api/report`

### 1. 生成分析报告

```
POST /api/report/generate
```

基于历史数据生成概率分析报告，支持生成详细报告和最终最优报告。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| report_types | list | 否 | ["detailed", "optimal"] | 报告类型（detailed / optimal） |
| use_trend | bool | 否 | true | 是否使用走势图数据 |

**请求示例**:

```
POST /api/report/generate?report_types=detailed&report_types=optimal
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "报告生成成功",
  "data": {
    "generated_reports": [
      {
        "type": "detailed",
        "status": "success",
        "preview": "===== 七星彩数字概率综合分析报告（专业版） =====..."
      },
      {
        "type": "optimal",
        "status": "success",
        "recommended_numbers": "",
        "confidence_score": 0.0,
        "preview": "===== 七星彩统计特征综合分析报告 =====..."
      }
    ],
    "total_samples": 500,
    "generated_at": "2026-06-15T10:00:00.000000"
  }
}
```

---

### 2. 获取报告列表

```
GET /api/report/list
```

分页获取已生成的报告列表。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| report_type | string | 否 | null | 报告类型过滤（detailed / optimal / head4） |
| page | int | 否 | 1 | 页码 |
| page_size | int | 否 | 10 | 每页数量（1-50） |

**请求示例**:

```
GET /api/report/list?report_type=detailed&page=1&page_size=10
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "items": [
      {
        "id": 1,
        "report_date": "2026-06-15",
        "report_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "total_samples": 500,
        "confidence_level": 0.85,
        "report_type": "detailed",
        "created_at": "2026-06-15T10:00:00"
      }
    ],
    "total": 15,
    "page": 1,
    "page_size": 10,
    "pages": 2
  }
}
```

---

### 3. 获取报告详情

```
GET /api/report/{report_id}?user_id={user_id}
```

根据报告 ID 获取报告详情（自动查询详细报告表和最终报告表）。

**路径参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| report_id | int | 是 | 报告 ID |
| user_id | int | 是 | 用户ID |

**请求头**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| X-Token | string | 否 | 用户访问令牌（用于验证登录状态） |

**响应说明**:
- 已付费用户：返回完整报告内容，`is_paid=true`, `is_preview=false`
- 未付费用户：返回预览内容，`is_paid=false`, `is_preview=true`
- 未登录用户：返回 401 错误

**已付费完整响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "id": 1,
    "report_date": "2026-06-15",
    "report_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "recommended_numbers": "3,7,1,9,5,2,8",
    "confidence_score": 0.85,
    "analysis_summary": "基于500期历史数据的综合分析...",
    "key_conclusions": "第一位热号：3,7,1；冷号：0...",
    "status": "validated",
    "is_preview": false,
    "is_paid": true,
    "created_at": "2026-06-15T10:00:00"
  }
}
```

**未付费预览响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功（预览模式，请付费后查看完整内容）",
  "data": {
    "id": 1,
    "report_date": "2026-06-15",
    "report_uuid": "xxx",
    "recommended_numbers": "****",
    "confidence_score": 0.85,
    "analysis_summary": "付费后可查看完整分析摘要",
    "key_conclusions": "付费后可查看关键结论",
    "status": "validated",
    "is_preview": true,
    "is_paid": false,
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 4. 新增详细报告

```
POST /api/report/detailed
```

手动新增详细分析报告。

**请求体** (`application/json`):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| report_date | string | 是 | 报告日期 |
| report_uuid | string | 是 | 报告唯一标识（UUID格式） |
| raw_data_snapshot | string | 否 | 原始数据快照 |
| calculation_steps | string | 否 | 计算步骤记录 |
| analysis_params | string | 否 | 分析参数配置 |
| frequency_analysis | string | 否 | 频率分析结果（JSON） |
| probability_analysis | string | 否 | 概率分析结果（JSON） |
| interval_analysis | string | 否 | 间隔分析结果（JSON） |
| hezhi_analysis | string | 否 | 和值分析结果（JSON） |
| odd_even_analysis | string | 否 | 奇偶分析结果（JSON） |
| span_analysis | string | 否 | 跨度分析结果（JSON） |
| total_samples | int | 否 | 分析样本数 |
| confidence_level | float | 否 | 置信水平（0-1） |
| report_content | string | 否 | 报告内容 |

**请求示例**:

```json
{
  "report_date": "2026-06-15",
  "report_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "total_samples": 500,
  "confidence_level": 0.85,
  "report_content": "===== 七星彩数字概率综合分析报告 =====..."
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 201,
  "message": "新增成功",
  "data": {
    "id": 16,
    "report_date": "2026-06-15",
    "report_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "total_samples": 500,
    "confidence_level": 0.85,
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 5. 生成头4分析报告

```
POST /api/report/generate-head4
```

生成头4（前四位）分析报告，分析数据的前四位数字：第一位是头、第二第三位是中间、第四位是尾。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| use_trend | bool | 否 | true | 是否使用走势图数据 |

**请求示例**:

```
POST /api/report/generate-head4
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "头4分析报告生成成功",
  "data": {
    "type": "head4",
    "status": "success",
    "preview": "===== 七星彩头4（前四位）分析报告 =====...",
    "total_samples": 500,
    "generated_at": "2026-06-15T10:00:00.000000"
  }
}
```

---

### 6. 新增最终报告

```
POST /api/report/final
```

手动新增最终最优报告。

**请求体** (`application/json`):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| detailed_report_id | int | 否 | 关联详细报告 ID |
| report_date | string | 是 | 报告日期 |
| report_uuid | string | 是 | 报告唯一标识（UUID格式） |
| recommended_numbers | string | 否 | 推荐号码组合 |
| confidence_score | float | 否 | 置信分数（0-1） |
| analysis_summary | string | 否 | 分析摘要 |
| key_conclusions | string | 否 | 关键结论 |
| core_metrics | string | 否 | 核心指标（JSON） |
| decision_recommendations | string | 否 | 决策建议 |
| report_content | string | 否 | 报告内容 |
| status | string | 否 | 报告状态（draft / validated / published） |

**请求示例**:

```json
{
  "report_date": "2026-06-15",
  "report_uuid": "550e8400-e29b-41d4-a716-446655440001",
  "recommended_numbers": "3,7,1,9,5,2,8",
  "confidence_score": 0.85,
  "analysis_summary": "基于500期历史数据的综合分析",
  "key_conclusions": "第一位热号：3,7,1；冷号：0",
  "status": "validated"
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 201,
  "message": "新增成功",
  "data": {
    "id": 13,
    "report_date": "2026-06-15",
    "report_uuid": "550e8400-e29b-41d4-a716-446655440001",
    "recommended_numbers": "3,7,1,9,5,2,8",
    "confidence_score": 0.85,
    "status": "validated",
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 7. 更新报告

```
PUT /api/report/{report_id}
```

根据报告 ID 更新最终报告（仅支持最终报告表）。

**路径参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| report_id | int | 是 | 报告 ID |

**请求体** (`application/json`):

与新增最终报告接口字段相同，所有字段可选。

**请求示例**:

```json
{
  "recommended_numbers": "5,2,8,1,9,3,7",
  "confidence_score": 0.88,
  "status": "published"
}
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "更新成功",
  "data": {
    "id": 13,
    "updated_fields": ["recommended_numbers", "confidence_score", "status"],
    "affected_rows": 1
  }
}
```

---

### 8. 删除报告

```
DELETE /api/report/{report_id}
```

根据报告 ID 删除报告（自动查询详细报告表和最终报告表）。

**路径参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| report_id | int | 是 | 报告 ID |

**请求示例**:

```
DELETE /api/report/13
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "删除成功",
  "data": {
    "deleted_id": 13,
    "report_type": "optimal",
    "affected_rows": 1
  }
}
```

---

### 9. 报告统计

```
GET /api/report/summary
```

获取报告统计信息，包含各类报告数量及最新报告。

**请求示例**:

```
GET /api/report/summary
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "detailed_report_count": 15,
    "final_report_count": 12,
    "head4_report_count": 5,
    "total_report_count": 32,
    "latest_detailed_report": {
      "id": 15,
      "report_date": "2026-06-15",
      "total_samples": 500,
      "confidence_level": 0.85,
      "created_at": "2026-06-15T10:00:00"
    },
    "latest_final_report": {
      "id": 12,
      "report_date": "2026-06-15",
      "recommended_numbers": "3,7,1,9,5,2,8",
      "confidence_score": 0.85,
      "status": "validated",
      "created_at": "2026-06-15T10:00:00"
    },
    "latest_head4_report": {
      "id": 5,
      "report_date": "2026-06-15",
      "total_samples": 500,
      "created_at": "2026-06-15T10:00:00"
    }
  }
}
```

---

## 系统管理

> **路由前缀**: `/api/system`

### 1. 系统状态

```
GET /api/system/status
```

获取系统运行状态、数据库连接状态、系统资源使用情况。

**请求示例**:

```
GET /api/system/status
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "status": "healthy",
    "version": "1.0.0",
    "database_status": "connected",
    "uptime": "2:30:15",
    "data_count": {
      "history_data": 500,
      "trend_data": 120,
      "detailed_reports": 15,
      "final_reports": 12,
      "head4_reports": 5
    },
    "system_resources": {
      "cpu_usage_percent": 5.2,
      "memory_total_gb": 16.0,
      "memory_used_gb": 8.5,
      "memory_usage_percent": 53.1,
      "disk_total_gb": 500.0,
      "disk_used_gb": 200.0,
      "disk_usage_percent": 40.0
    },
    "timestamp": "2026-06-15T10:00:00.000000"
  }
}
```

---

### 2. 获取配置

```
GET /api/system/config
```

获取系统配置信息（数据库、爬虫、分析、报告配置）。

**请求示例**:

```
GET /api/system/config
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "database": {
      "host": "localhost",
      "port": 3306,
      "database": "qxc_analysis"
    },
    "crawler": {
      "base_url": "https://example.com/qxc",
      "default_qishu": 200
    },
    "analysis": {
      "default_samples": 500,
      "confidence_level": 0.95
    },
    "report": {
      "auto_generate": false,
      "default_types": ["detailed", "optimal"]
    }
  }
}
```

---

### 3. 初始化数据库

```
POST /api/system/init
```

初始化数据库表结构（创建所有必要的表）。

**请求示例**:

```
POST /api/system/init
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "数据库初始化成功",
  "data": {
    "tables_created": [
      "qxc_history_data",
      "qxc_trend_data",
      "qxc_detailed_report",
      "qxc_final_report",
      "qxc_head4_report",
      "users",
      "payment_records"
    ]
  }
}
```

---

### 4. 清理数据

```
POST /api/system/clean?confirm=true
```

清理所有数据表中的数据（危险操作，需确认）。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| confirm | bool | 否 | false | 确认删除（必须设为 true） |

**请求示例**:

```
POST /api/system/clean?confirm=true
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "数据清理成功",
  "data": {
    "cleaned_tables": [
      "qxc_history_data",
      "qxc_trend_data",
      "qxc_detailed_report",
      "qxc_final_report",
      "qxc_head4_report"
    ],
    "total_deleted_rows": 635
  }
}
```

---

### 5. 获取日志

```
GET /api/system/logs
```

获取最近的系统日志内容。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| log_type | string | 否 | database | 日志类型（database / analyzer / head4_analyzer） |
| lines | int | 否 | 50 | 获取行数（1-200） |

**请求示例**:

```
GET /api/system/logs?log_type=database&lines=30
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "log_type": "database",
    "lines": 30,
    "content": [
      "2026-06-15 10:00:00 - INFO - 数据库连接成功",
      "2026-06-15 10:00:01 - INFO - 数据表创建成功",
      "2026-06-15 10:00:02 - INFO - 查询历史数据: 500条"
    ]
  }
}
```

---

## 用户认证与付费

> **路由前缀**: `/api/auth`

### 1. 用户登录/注册

```
POST /api/auth/login
```

微信小程序用户登录，不存在则自动注册。

**请求参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| wx_openid | string | 是 | 微信用户OpenID |
| wx_unionid | string | 否 | 微信用户UnionID |
| nickname | string | 否 | 用户昵称 |
| avatar_url | string | 否 | 用户头像URL |

**请求示例**:

```
POST /api/auth/login?wx_openid=oXXXXxxxxx&nickname=测试用户&avatar_url=https://example.com/avatar.jpg
```

**响应示例（已存在用户登录）**:

```json
{
  "success": true,
  "code": 200,
  "message": "登录成功",
  "data": {
    "user_id": 1,
    "wx_openid": "oXXXXxxxxx",
    "nickname": "测试用户",
    "avatar_url": "https://example.com/avatar.jpg",
    "is_new_user": false,
    "access_token": "550e8400-e29b-41d4-a716-446655440000",
    "token_expire_at": "2026-06-22T10:00:00",
    "created_at": "2026-06-15T10:00:00"
  }
}
```

**响应示例（新用户注册并登录）**:

```json
{
  "success": true,
  "code": 201,
  "message": "注册并登录成功",
  "data": {
    "user_id": 2,
    "wx_openid": "oYYYYyyyyy",
    "nickname": "新用户",
    "avatar_url": null,
    "is_new_user": true,
    "access_token": "660e8400-e29b-41d4-a716-446655440001",
    "token_expire_at": "2026-06-22T10:00:00",
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 2. 获取用户信息

```
GET /api/auth/profile
```

根据用户ID或Token获取用户信息。

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| user_id | int | 否 | 用户ID（与X-Token二选一） |

**请求头**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| X-Token | string | 否 | 用户访问令牌（与user_id二选一） |

**请求示例**:

```
GET /api/auth/profile?user_id=1
```

或

```
GET /api/auth/profile
X-Token: 550e8400-e29b-41d4-a716-446655440000
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "user_id": 1,
    "wx_openid": "oXXXXxxxxx",
    "nickname": "测试用户",
    "avatar_url": "https://example.com/avatar.jpg",
    "login_count": 5,
    "last_login_at": "2026-06-15T10:00:00",
    "created_at": "2026-06-15T10:00:00"
  }
}
```

---

### 3. 创建付费记录

```
POST /api/auth/payment
```

记录用户付费信息。

**请求参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| user_id | int | 是 | 用户ID |
| amount | float | 是 | 付费金额（元） |
| payment_type | string | 是 | 付费类型（report_view/vip_month/vip_year） |
| order_no | string | 否 | 商户订单号 |
| transaction_id | string | 否 | 微信支付交易号 |
| description | string | 否 | 付费描述 |

**请求示例**:

```
POST /api/auth/payment?user_id=1&amount=9.99&payment_type=report_view&description=查看报告付费
```

**响应示例**:

```json
{
  "success": true,
  "code": 201,
  "message": "付费记录创建成功",
  "data": {
    "id": 1,
    "user_id": 1,
    "order_no": "QXC202606151200001",
    "transaction_id": null,
    "amount": 9.99,
    "payment_type": "report_view",
    "status": "success",
    "description": "查看报告付费",
    "paid_at": "2026-06-15T12:00:00"
  }
}
```

---

### 4. 查询用户付费记录

```
GET /api/auth/payments?user_id={user_id}
```

查询指定用户的所有付费记录。

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| user_id | int | 是 | 用户ID |
| payment_type | string | 否 | 付费类型过滤 |

**请求示例**:

```
GET /api/auth/payments?user_id=1&payment_type=report_view
```

**响应示例**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "total": 3,
    "records": [
      {
        "id": 3,
        "order_no": "QXC202606151400003",
        "transaction_id": "wx_202606151400003",
        "amount": 9.99,
        "payment_type": "report_view",
        "status": "success",
        "description": "查看报告付费",
        "paid_at": "2026-06-15T14:00:00"
      },
      {
        "id": 2,
        "order_no": "QXC202606151300002",
        "transaction_id": null,
        "amount": 29.99,
        "payment_type": "vip_month",
        "status": "success",
        "description": "月度VIP会员",
        "paid_at": "2026-06-15T13:00:00"
      },
      {
        "id": 1,
        "order_no": "QXC202606151200001",
        "transaction_id": null,
        "amount": 9.99,
        "payment_type": "report_view",
        "status": "success",
        "description": "查看报告付费",
        "paid_at": "2026-06-15T12:00:00"
      }
    ]
  }
}
```

---

### 5. 检查用户付费状态

```
GET /api/auth/payment-status?user_id={user_id}
```

检查用户是否已付费（可查看报告）。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| user_id | int | 是 | - | 用户ID |
| payment_type | string | 否 | report_view | 付费类型 |

**请求示例**:

```
GET /api/auth/payment-status?user_id=1&payment_type=report_view
```

**响应示例（已付费）**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "user_id": 1,
    "payment_type": "report_view",
    "is_paid": true,
    "last_payment": {
      "id": 3,
      "amount": 9.99,
      "paid_at": "2026-06-15T14:00:00"
    }
  }
}
```

**响应示例（未付费）**:

```json
{
  "success": true,
  "code": 200,
  "message": "查询成功",
  "data": {
    "user_id": 1,
    "payment_type": "report_view",
    "is_paid": false,
    "last_payment": null
  }
}
```

---

## 数据模型

### 数据库表结构

| 表名 | 说明 |
|------|------|
| qxc_history_data | 七星彩历史开奖数据 |
| qxc_trend_data | 走势图数据 |
| qxc_detailed_report | 详细分析报告 |
| qxc_final_report | 最终最优报告 |
| qxc_head4_report | 头4（前四位）分析报告 |
| users | 用户表（微信小程序用户） |
| payment_records | 用户付费记录表 |

### 枚举类型

**ReportStatus（报告状态）**:

| 值 | 说明 |
|------|------|
| draft | 草稿 |
| validated | 已验证 |
| published | 已发布 |

---

## 快速开始

### 启动服务

```bash
python main_api.py
```

服务默认运行在 `http://localhost:8000`。

### 在线 API 文档

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### 常用调用示例

```bash
# 爬取数据
curl -X POST "http://localhost:8000/api/data/crawl" \
  -H "Content-Type: application/json" \
  -d '{"qishu": 200, "trend": true}'

# 查看数据概览
curl "http://localhost:8000/api/data/summary"

# 综合分析
curl "http://localhost:8000/api/analysis/comprehensive"

# 遗漏值分析
curl "http://localhost:8000/api/analysis/omission"

# 用户登录/注册
curl -X POST "http://localhost:8000/api/auth/login?wx_openid=oXXXXxxxxx&nickname=测试用户"

# 创建付费记录
curl -X POST "http://localhost:8000/api/auth/payment?user_id=1&amount=9.99&payment_type=report_view&description=查看报告"

# 检查付费状态
curl "http://localhost:8000/api/auth/payment-status?user_id=1"

# 查看报告详情（需传入user_id和X-Token）
curl "http://localhost:8000/api/report/1?user_id=1" \
  -H "X-Token: your-access-token"

# 冷热号分析（最近50期）
curl "http://localhost:8000/api/analysis/hot_cold?recent_n=50"

# 生成报告
curl -X POST "http://localhost:8000/api/report/generate?report_types=detailed&report_types=optimal"

# 生成头4分析报告
curl -X POST "http://localhost:8000/api/report/generate-head4"

# 系统状态
curl "http://localhost:8000/api/system/status"
```
