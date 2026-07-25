# -*- coding: utf-8 -*-
"""
AI 响应 JSON 鲁棒解析工具

问题背景
--------
AI 模型（agnes-2.0-flash 等）返回的 JSON 经常不是严格合法的 RFC8259 JSON，常见非标准写法：
  - 对象 key 未加引号:  { wan: "5" } 或 { 'wan': "5" }
  - 字符串使用单引号:   { "wan": '5' }
  - Python 常量:        true/false/null 写成 True/False/None
  - 尾随逗号:           { "a": 1, }
  - 外层包裹解释性文字 / ```json 代码块
原来的解析逻辑用 find('{') + rfind('}') 截断，再用 json.loads 直接解析，遇到上述问题即失败。

本模块提供统一的修复策略，三个 AI 调用点（pipeline / predictor / ai_analyzer）共用，
确保解析逻辑一致（见 AGENTS.md）。

修复顺序
--------
1. 去除 ```json 代码块围栏与首尾解释性文字
2. 定位最外层平衡 { ... }（带字符串/转义感知，避免内部 '}' 误判）
3. 先尝试标准 json.loads
4. 失败则做修复后再 json.loads:
   a. 结构位置上的单引号字符串 -> 双引号字符串
   b. 未加引号的裸 key -> 加双引号
   c. Python 常量 True/False/None -> true/false/null
   d. 尾随逗号移除
5. 终极兜底: ast.literal_eval（处理纯 Python 字面量）
"""

import json
import re
import ast
import logging

logger = logging.getLogger(__name__)


def extract_first_json_object(text: str) -> str:
    """
    从文本中定位并返回最外层平衡 JSON 对象子串（从第一个 '{' 开始）。
    具备字符串与转义感知，不会因字符串内部出现 '{'/'}' 而误判。
    找不到则返回 None。
    """
    if not text:
        return None
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _strip_code_fences(text: str) -> str:
    """去除 ```json ... ``` 围栏及首尾空白/解释性文字的包裹。"""
    t = text.strip()
    # 去除开头的 ``` 及可选语言标记
    t = re.sub(r'^```[a-zA-Z]*\s*\n?', '', t, count=1)
    # 去除结尾的 ```
    t = re.sub(r'\n?```\s*$', '', t)
    return t.strip()


def _repair_json_string(s: str) -> str:
    """
    对疑似 JSON 文本做非标准写法修复，返回更可能被 json.loads 解析的字符串。
    仅在「结构位置」上处理单引号，避免误伤双引号字符串内的撇号。
    """
    # a. 结构位置上的单引号字符串 -> 双引号字符串
    #    要求单引号前是 { [ , : 或其后空白（即键/值位置），避免触碰 "it's" 这类内部撇号
    def _single_to_double(m):
        prefix = m.group(1)
        inner = m.group(2).replace('\\"', '\\\\"').replace('"', '\\"')
        return prefix + '"' + inner + '"'

    s = re.sub(r"([{\[,:]\s*)'([^']*)'", _single_to_double, s)

    # b. 未加引号的裸 key -> 加双引号
    #    形如 { wan: 或 , qian:  （key 为合法标识符，前面是 { 或 ,）
    s = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', s)

    # c. Python 常量 -> JSON 常量（词边界）
    s = re.sub(r'\bTrue\b', 'true', s)
    s = re.sub(r'\bFalse\b', 'false', s)
    s = re.sub(r'\bNone\b', 'null', s)

    # d. 尾随逗号:  ,} 或 ,]  ->  } 或 ]
    s = re.sub(r',(\s*[}\]])', r'\1', s)

    return s


def repair_and_parse_json(text, default=None):
    """
    鲁棒解析 AI 返回的 JSON 文本。

    Args:
        text: AI 返回的响应字符串
        default: 解析失败时的返回值（默认 None）

    Returns:
        解析成功返回 dict/list，失败返回 default。
    """
    if not text:
        return default

    t = _strip_code_fences(text)

    # 1. 直接解析（最外层平衡对象）
    obj = extract_first_json_object(t)
    if obj:
        try:
            return json.loads(obj)
        except json.JSONDecodeError:
            pass

    # 2. 修复后再解析（平衡对象）
    if obj:
        try:
            return json.loads(_repair_json_string(obj))
        except json.JSONDecodeError:
            pass

    # 3. 对整个文本做修复后解析（应对更复杂包裹）
    try:
        return json.loads(_repair_json_string(t))
    except json.JSONDecodeError:
        pass

    # 4. 终极兜底: Python 字面量求值（处理纯 Python dict 风格）
    try:
        val = ast.literal_eval(t)
        if isinstance(val, (dict, list)):
            return val
    except Exception:
        pass

    logger.error('JSON修复解析最终失败，返回默认值')
    return default
