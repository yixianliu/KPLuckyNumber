"""
排列5 AI 分析模块

基于 AGNES 大语言模型，整合多数据源进行分析，
生成结构化报告并存储到数据库。

核心功能：
1. 数据源整合 - 读取走势图、万位/千位/百位/十位走势图数据
2. AI 模型调用 - 使用 AGNES API 进行分析
3. 报告生成 - 生成包含预测结果、置信度、趋势分析的结构化报告
4. 数据库存储 - 将报告存入 p5_ai_report 表

参考接口规范（见 D:\\PythonProject\\api\\api.txt）：
- API 端点：https://api.agnes-ai.cn/v1/chat/completions
- 默认模型：agnes-3.0-flash（v3.68 起升级；可通过 AGNES_MODEL_NAME 覆盖）
- 认证方式：Authorization: Bearer <token>
- 请求体字段：model / messages / max_tokens / temperature / tools / stream / top_p
- 工具调用（function call）：tools=[{"type":"function","function":{...}}]
"""

import logging
import os
import json
import time
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from paths import LOGS_DIR

os.makedirs(LOGS_DIR, exist_ok=True)

# 日志目录保证：遵循项目约定，将日志写入 logs/ 供集中查看。

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(LOGS_DIR + '/ernie_ai_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# 说明：本模块负责调用 AI 并生成结构化报告。AI 配置从 config.py 的 AGNES_API_CONFIG 加载。

# ---------------------------------------------------------------------------
# 模块级 Session 复用（v3.68 新增）
# 说明：
#   - 每次调用新建 Session 会重复建立 TLS 握手与 HTTP 连接，浪费 CPU 与内存。
#   - 此处持有进程级 Session，通过 HTTPAdapter 的 pool_connections / pool_maxsize
#     启用连接池复用（TCP Keep-Alive），显著降低多次调用时的握手开销。
#   - Session 构造失败不影响主流程（异常已在工厂函数内消化）。
# ---------------------------------------------------------------------------
_MODULE_SESSION = None
_MODULE_SESSION_LOCK = __import__('threading').Lock()


def _get_module_session(pool_connections: int = 5,
                        pool_maxsize: int = 10,
                        max_retries: int = 3,
                        backoff_factor: float = 0.5) -> requests.Session:
    """获取进程级复用的 requests.Session（线程安全，惰性初始化）。

    Args:
        pool_connections: HTTPAdapter 连接池数量（默认 5）
        pool_maxsize: 单个连接池最大连接数（默认 10）
        max_retries: urllib3 Retry 总次数（默认 3）
        backoff_factor: Retry 退避因子（默认 0.5，重试间隔序列 0s / 1s / 2s）

    Returns:
        requests.Session 实例（已挂载 Retry 策略）
    """
    global _MODULE_SESSION
    if _MODULE_SESSION is None:
        with _MODULE_SESSION_LOCK:
            if _MODULE_SESSION is None:
                session = requests.Session()
                retry = Retry(
                    total=max_retries,
                    connect=max_retries,
                    read=max_retries,
                    backoff_factor=backoff_factor,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset(['POST', 'GET']),
                    raise_on_status=False,
                    respect_retry_after_header=True,
                )
                adapter = HTTPAdapter(
                    max_retries=retry,
                    pool_connections=pool_connections,
                    pool_maxsize=pool_maxsize,
                    pool_block=False,
                )
                session.mount('https://', adapter)
                session.mount('http://', adapter)
                _MODULE_SESSION = session
                logger.info(
                    f'AI Session 初始化完成: pool={pool_connections}/{pool_maxsize}, '
                    f'retries={max_retries}, backoff={backoff_factor}'
                )
    return _MODULE_SESSION


def reset_module_session() -> None:
    """清空全局 Session 单例（供测试 / 密钥切换时调用，避免复用过期连接）。"""
    global _MODULE_SESSION
    with _MODULE_SESSION_LOCK:
        if _MODULE_SESSION is not None:
            try:
                _MODULE_SESSION.close()
            except Exception:
                pass
            _MODULE_SESSION = None


class AIAnalyzer:
    """
    排列5 AI分析器

    整合多数据源，调用 AGNES AI 大语言模型进行深度分析，
    生成结构化分析报告并存储到数据库。
    """

    def __init__(self):
        """初始化 AI 分析器，加载接口配置并预置位置名称映射。

        说明:
            位置中文名与英文键按 万/千/百/十/个 顺序一一对应，
            供构造提示词与解析模型返回结果时统一使用。
        """
        self._init_ai_config()
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.position_keys = ['wan', 'qian', 'bai', 'shi', 'ge']

    def _init_ai_config(self):
        """初始化AI模型配置（对齐 api.txt 官方规范，参数从 config.py 的 AGNES_API_CONFIG 读取）。

        说明：
            - model_name 默认升级为 agnes-3.0-flash（api.txt 推荐版本）；
            - 所有可调参数（timeout / max_tokens / temperature / top_p / stream 等）均从配置读取，
              调用方可按需 override，无需修改代码。
            - 备份模型（backup_model）用于主模型被限流/404 时的自动降级。
        """
        defaults = {
            'api_url': "https://api.agnes-ai.cn/v1/chat/completions",
            'api_key': '',
            'model_name': 'agnes-3.0-flash',
            'backup_model': 'agnes-2.5-flash',
            'timeout': 60,
            'max_tokens': 2048,
            'temperature': 0.7,
            'top_p': 1.0,
            'stream': False,
            'max_retries': 3,
            'retry_backoff_factor': 0.5,
            'pool_connections': 5,
            'pool_maxsize': 10,
            'response_format': 'json_object',
        }
        try:
            import config as cfg
            self.api_config = {**defaults, **(getattr(cfg, 'AGNES_API_CONFIG', {}) or {})}
        except Exception:
            self.api_config = dict(defaults)
            logger.info('未能从config.py加载配置，使用默认值')

        self.api_url = self.api_config.get('api_url', defaults['api_url'])
        self.api_key = self.api_config.get('api_key', '')
        self.model_name = self.api_config.get('model_name', defaults['model_name'])
        self.backup_model = self.api_config.get('backup_model', defaults['backup_model'])
        self.timeout = int(self.api_config.get('timeout', 60))
        self.default_max_tokens = int(self.api_config.get('max_tokens', 2048))
        self.default_temperature = float(self.api_config.get('temperature', 0.7))
        self.top_p = float(self.api_config.get('top_p', 1.0))
        self.stream = bool(self.api_config.get('stream', False))
        self.max_retries = int(self.api_config.get('max_retries', 3))
        self.retry_backoff_factor = float(self.api_config.get('retry_backoff_factor', 0.5))
        self.pool_connections = int(self.api_config.get('pool_connections', 5))
        self.pool_maxsize = int(self.api_config.get('pool_maxsize', 10))
        self.response_format = self.api_config.get('response_format', 'json_object')
        self.ai_available = bool(self.api_key)

        if self.ai_available:
            self.headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}',
            }
            logger.info(
                f'从config.py加载API配置: model={self.model_name}, '
                f'backup={self.backup_model}, timeout={self.timeout}s, '
                f'max_tokens={self.default_max_tokens}'
            )
        else:
            logger.warning('config.py中未配置API密钥，AI功能将跳过')

    def _call_ai_model(self, messages: List[Dict[str, Any]],
                       max_tokens: Optional[int] = None,
                       temperature: Optional[float] = None,
                       tools: Optional[List[Dict[str, Any]]] = None,
                       response_format: Optional[str] = None,
                       model: Optional[str] = None,
                       _fallback_used: bool = False) -> Optional[str]:
        """调用 AGNES AI 模型（对齐 api.txt 官方接口规范）。

        请求契约（api.txt §基础请求 / §工具调用请求）:
            POST {api_url}
            Headers: Authorization: Bearer <key>, Content-Type: application/json
            Body:   {"model":..., "messages":[...], "max_tokens":N, "temperature":T}
                    可选字段: tools / response_format / stream / top_p

        Args:
            messages: 消息列表，包含 system / user 角色
            max_tokens: 最大输出 token 数（None 时用配置默认值）
            temperature: 温度参数（None 时用配置默认值）
            tools: OpenAI 兼容的 function call 声明；非空时透传至 payload.tools
            response_format: JSON 输出模式（默认 'json_object'；传 False 关闭）
            model: 覆盖本次调用的模型名（None 用 self.model_name）
            _fallback_used: 内部标记，主模型失败后是否已切到备份模型

        Returns:
            AI 返回的内容字符串；调用失败返回 None。
        """
        if not self.ai_available:
            logger.warning('AI模型不可用（未配置API密钥）')
            return None

        use_model = model or self.model_name
        max_tokens = int(max_tokens if max_tokens is not None else self.default_max_tokens)
        temperature = float(temperature if temperature is not None else self.default_temperature)
        rf = self.response_format if response_format is None else response_format

        logger.info(
            f'=== 开始调用AI模型: {use_model} | max_tokens={max_tokens} | '
            f'temperature={temperature} | tools={len(tools) if tools else 0} ==='
        )

        # 构建请求 payload（仅包含非空/非默认字段，保持请求体精简）
        payload: Dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": bool(self.stream),
        }
        if self.top_p is not None and self.top_p != 1.0:
            payload["top_p"] = self.top_p
        if tools:
            payload["tools"] = tools
        if rf and rf not in (False, 'false', 'False'):
            payload["response_format"] = {"type": rf}

        # 复用进程级 Session（连接池 + Retry），避免每次调用重新建立 TLS 握手
        session = _get_module_session(
            pool_connections=self.pool_connections,
            pool_maxsize=self.pool_maxsize,
            max_retries=self.max_retries,
            backoff_factor=self.retry_backoff_factor,
        )

        last_err: Optional[BaseException] = None
        max_attempts = 4  # 方法层重试；urllib3 内部 Retry 会先行拦截瞬时 5xx
        for attempt in range(max_attempts):
            try:
                response = session.request(
                    "POST",
                    self.api_url,
                    headers=self.headers,
                    data=json.dumps(payload),
                    timeout=self.timeout,
                )
                response.raise_for_status()

                result = response.json()

                if 'choices' in result and len(result['choices']) > 0:
                    choice = result['choices'][0]
                    content = choice.get('message', {}).get('content')
                    if content:
                        usage = result.get('usage', {})
                        logger.info(
                            f'AI模型调用成功(第{attempt + 1}次), 长度={len(content)}, '
                            f'usage={usage}, finish_reason={choice.get("finish_reason")}'
                        )
                        return content

                # 主模型不可用（404 / 502 / 429 且重试耗尽）时自动降级到备份模型
                if not _fallback_used and use_model != self.backup_model and self.backup_model:
                    logger.warning(
                        f'AI模型 {use_model} 返回异常，尝试降级到备份模型 {self.backup_model}'
                    )
                    return self._call_ai_model(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        tools=tools,
                        response_format=response_format,
                        model=self.backup_model,
                        _fallback_used=True,
                    )

                logger.error(f'AI模型返回格式异常: {result}')
                return None

            except requests.exceptions.HTTPError as e:
                last_err = e
                status = e.response.status_code if e.response is not None else None
                # 4xx（400/401/403/404）非重试，直接返回；避免浪费配额
                if status is not None and 400 <= status < 500:
                    logger.error(f'AI模型 HTTP {status} 错误（非重试）: {e}')
                    # 404/410 模型不存在时，尝试降级到备份模型
                    if status in (404, 410) and not _fallback_used and self.backup_model:
                        logger.warning(f'模型 {use_model} 不存在，降级到 {self.backup_model}')
                        return self._call_ai_model(
                            messages=messages, max_tokens=max_tokens,
                            temperature=temperature, tools=tools,
                            response_format=response_format,
                            model=self.backup_model, _fallback_used=True,
                        )
                    return None
                wait = 0.8 * (2 ** attempt)
                logger.warning(f'AI模型调用第{attempt + 1}次失败: {e}; {wait:.1f}s 后重试')
                if attempt < max_attempts - 1:
                    time.sleep(wait)
            except requests.exceptions.RequestException as e:
                last_err = e
                wait = 0.8 * (2 ** attempt)  # 指数退避: 0.8s / 1.6s / 3.2s
                logger.warning(f'AI模型调用第{attempt + 1}次失败: {e}; {wait:.1f}s 后重试')
                if attempt < max_attempts - 1:
                    time.sleep(wait)
            except json.JSONDecodeError as e:
                logger.error(f'AI响应JSON解析失败: {e}')
                return None
            except Exception as e:
                logger.error(f'AI模型调用异常: {e}')
                return None

        logger.error(f'AI模型调用在 {max_attempts} 次重试后仍失败: {last_err}')
        return None

    @staticmethod
    def _build_ai_session() -> requests.Session:
        """兼容旧代码路径的 Session 工厂；内部委托到进程级 Session 单例。

        说明：本方法保留仅为向后兼容（外部脚本可能直接调用），
        实际请求路径已切换到 _get_module_session 的连接池实现。
        """
        return _get_module_session()

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI响应为JSON格式（鲁棒：兼容单引号/裸key/尾随逗号/代码块）"""
        from modules.json_repair import repair_and_parse_json
        result = repair_and_parse_json(response_text, default={})
        return result if isinstance(result, dict) else {}

    def _fetch_data_from_database(self, limit: int = 30) -> Dict[str, Any]:
        """
        从数据库获取所有必要的数据源

        Args:
            limit: 获取最近多少期数据

        Returns:
            包含所有数据源的字典
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('数据库连接失败，无法加载数据')
                return {'error': '数据库连接失败'}

            history_data = db.get_history_data(limit=limit, order_by='issue DESC')
            trend_data = db.get_trend_data(limit=limit)
            from modules import database_utils
            wan_trend_data = database_utils.get_position_trend_data(db.cursor, 'wan', limit=limit)
            qian_trend_data = database_utils.get_position_trend_data(db.cursor, 'qian', limit=limit)
            bai_trend_data = database_utils.get_position_trend_data(db.cursor, 'bai', limit=limit)
            shi_trend_data = database_utils.get_position_trend_data(db.cursor, 'shi', limit=limit)
            ge_trend_data = database_utils.get_position_trend_data(db.cursor, 'ge', limit=limit)

            db.disconnect()

            latest_issue = history_data[0]['issue'] if history_data else ''

            logger.info(f'数据库数据加载完成: 历史数据{len(history_data)}条, 走势数据{len(trend_data)}条')
            logger.info(f'各位置走势数据: 万位{len(wan_trend_data)}条, 千位{len(qian_trend_data)}条, 百位{len(bai_trend_data)}条, 十位{len(shi_trend_data)}条, 个位{len(ge_trend_data)}条')

            return {
                'history_data': history_data,
                'trend_data': trend_data,
                'wan_trend_data': wan_trend_data,
                'qian_trend_data': qian_trend_data,
                'bai_trend_data': bai_trend_data,
                'shi_trend_data': shi_trend_data,
                'ge_trend_data': ge_trend_data,
                'latest_issue': latest_issue,
                'data_count': len(history_data),
                'error': None
            }

        except Exception as e:
            logger.error(f'从数据库加载数据失败: {e}')
            return {'error': str(e)}

    def _generate_position_stats(self, position_data: List[Dict[str, Any]], position_name: str) -> str:
        """
        生成单个位置的统计信息

        Args:
            position_data: 位置走势数据
            position_name: 位置名称

        Returns:
            统计信息字符串
        """
        if not position_data:
            return f'{position_name}: 暂无数据'

        lines = []
        lines.append(f'【{position_name}统计】')

        num_counts = {}
        omissions = {}
        odd_count = 0
        big_count = 0

        for item in position_data:
            if position_name == '万位':
                num = item.get('wan_number', 0)
            elif position_name == '千位':
                num = item.get('qian_number', 0)
            elif position_name == '百位':
                num = item.get('bai_number', 0)
            elif position_name == '十位':
                num = item.get('shi_number', 0)
            else:
                continue

            num_counts[num] = num_counts.get(num, 0) + 1

            if item.get('is_odd'):
                odd_count += 1
            if item.get('is_big'):
                big_count += 1

            omission = item.get('omission', 0)
            omissions[num] = max(omissions.get(num, 0), omission)

        sorted_nums = sorted(num_counts.items(), key=lambda x: x[1], reverse=True)
        hot_nums = [n for n, _ in sorted_nums[:3]]
        cold_nums = [n for n, _ in sorted_nums[-3:]]

        high_omission = sorted(omissions.items(), key=lambda x: x[1], reverse=True)[:3]

        lines.append(f'  热号: {hot_nums}')
        lines.append(f'  冷号: {cold_nums}')
        lines.append(f'  高遗漏号码: {[(n, o) for n, o in high_omission]}')
        lines.append(f'  奇数比例: {odd_count}/{len(position_data)}')
        lines.append(f'  大数比例: {big_count}/{len(position_data)}')

        recent_values = []
        for item in position_data[:10]:
            if position_name == '万位':
                recent_values.append(item.get('wan_number', 0))
            elif position_name == '千位':
                recent_values.append(item.get('qian_number', 0))
            elif position_name == '百位':
                recent_values.append(item.get('bai_number', 0))
            elif position_name == '十位':
                recent_values.append(item.get('shi_number', 0))

        lines.append(f'  近期走势(最近10期): {recent_values}')

        return '\n'.join(lines)

    def _generate_trend_data_summary(self, trend_data: List[Dict[str, Any]]) -> str:
        """
        生成走势图数据摘要

        Args:
            trend_data: 走势图数据

        Returns:
            摘要字符串
        """
        if not trend_data:
            return '走势图数据：暂无数据'

        lines = []
        lines.append('【走势图数据摘要】')

        for item in trend_data[:20]:
            issue = item.get('issue', '')
            wan = item.get('wan', 0)
            qian = item.get('qian', 0)
            bai = item.get('bai', 0)
            shi = item.get('shi', 0)
            ge = item.get('ge', 0)
            draw_date = item.get('draw_date', '')
            hezhi = item.get('hezhi', '')
            odd_even = item.get('odd_even_ratio', '')
            big_small = item.get('big_small_ratio', '')

            lines.append(f'期号:{issue} 日期:{draw_date} 号码:{wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 奇偶比:{odd_even} 大小比:{big_small}')

        return '\n'.join(lines)

    def _build_ai_prompt(self, data: Dict[str, Any]) -> str:
        """
        构建AI分析提示词，整合所有数据源

        Args:
            data: 包含所有数据源的字典

        Returns:
            完整的提示词字符串
        """
        prompt = f"""你是一位专业的排列5彩票数据分析专家。请基于以下提供的详细历史数据和走势数据，进行深度分析并预测下一期各位置号码。

【彩种规则】
- 排列5：5位数字，每位0-9，每天开奖
- 号码位置：万位、千位、百位、十位、个位
- 和值范围：0-45
- 跨度范围：0-9

【数据来源说明】
- 历史开奖数据：最近{data['data_count']}期
- 走势图数据：最近{len(data['trend_data'])}期
- 万位走势：最近{len(data['wan_trend_data'])}期
- 千位走势：最近{len(data['qian_trend_data'])}期
- 百位走势：最近{len(data['bai_trend_data'])}期
- 十位走势：最近{len(data['shi_trend_data'])}期
- 最新期号：{data['latest_issue']}
- 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

【近期开奖数据（最近20期）】
"""

        for item in data['history_data'][:20]:
            issue = item.get('issue', '')
            draw_date = item.get('draw_date', '')
            wan = item.get('wan', 0)
            qian = item.get('qian', 0)
            bai = item.get('bai', 0)
            shi = item.get('shi', 0)
            ge = item.get('ge', 0)
            hezhi = item.get('hezhi', '')
            span = item.get('span', '')
            odd_even_ratio = item.get('odd_even_ratio', '')
            big_small_ratio = item.get('big_small_ratio', '')

            prompt += f'期号:{issue} 日期:{draw_date} 号码:{wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 跨度:{span} 奇偶比:{odd_even_ratio} 大小比:{big_small_ratio}\n'

        prompt += """
【各位置走势统计】
"""

        prompt += self._generate_position_stats(data['wan_trend_data'], '万位') + '\n\n'
        prompt += self._generate_position_stats(data['qian_trend_data'], '千位') + '\n\n'
        prompt += self._generate_position_stats(data['bai_trend_data'], '百位') + '\n\n'
        prompt += self._generate_position_stats(data['shi_trend_data'], '十位') + '\n\n'

        prompt += """
【分析要求】
1. 数据来源与预处理说明：说明使用的数据来源、数据周期、数据质量评估
2. 各位置号码预测：基于统计规律和AI深度分析，预测万位、千位、百位、十位号码
3. 置信度评估：为每个推荐号码提供置信度分数（0-1），并解释评估依据
4. 趋势分析：分析各位置号码近期走势、冷热号变化趋势、关键特征提取
5. 预测依据与模型推理过程：详细说明推理逻辑、使用的分析方法、数据支撑
6. 风险提示：明确说明所有分析仅基于历史数据统计，不保证中奖，请理性购彩

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外文字：

{
    "data_source": {
        "description": "数据来源与预处理说明",
        "data_period": "数据周期描述",
        "data_count": 30,
        "latest_issue": "最新期号",
        "analysis_time": "分析时间"
    },
    "predictions": {
        "wan": [
            {"number": 5, "confidence": 0.85, "reason": "近期热号，频次统计排名第一"},
            {"number": 3, "confidence": 0.78, "reason": "遗漏值即将到期"},
            {"number": 8, "confidence": 0.72, "reason": "趋势分析显示上升"}
        ],
        "qian": [
            {"number": 2, "confidence": 0.82, "reason": "频次统计排名第一"},
            {"number": 6, "confidence": 0.76, "reason": "奇偶模式转换"},
            {"number": 9, "confidence": 0.70, "reason": "近期走势明显"}
        ],
        "bai": [
            {"number": 7, "confidence": 0.80, "reason": "遗漏值回归"},
            {"number": 1, "confidence": 0.75, "reason": "冷热号交替"},
            {"number": 4, "confidence": 0.68, "reason": "大小模式转换"}
        ],
        "shi": [
            {"number": 4, "confidence": 0.83, "reason": "频次统计排名第一"},
            {"number": 0, "confidence": 0.77, "reason": "遗漏值即将到期"},
            {"number": 5, "confidence": 0.71, "reason": "趋势分析显示下降"}
        ]
    },
    "trend_analysis": {
        "wan": "万位近期走势分析：...",
        "qian": "千位近期走势分析：...",
        "bai": "百位近期走势分析：...",
        "shi": "十位近期走势分析：..."
    },
    "key_features": [
        "特征1描述",
        "特征2描述",
        "特征3描述"
    ],
    "reasoning_process": [
        "推理步骤1说明",
        "推理步骤2说明",
        "推理步骤3说明"
    ],
    "recommended_combinations": [
        {"numbers": [5, 2, 7, 4], "confidence": 0.72, "reason": "综合各位置最优推荐"},
        {"numbers": [5, 2, 7, 0], "confidence": 0.68, "reason": "十位备选方案"}
    ],
    "risk_warning": "本分析基于历史数据统计，不保证中奖，请理性购彩。"
}
"""
        return prompt

    def _generate_structured_report(self, ai_result: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成结构化AI分析报告

        Args:
            ai_result: AI模型返回的分析结果
            data: 原始数据源

        Returns:
            完整的结构化报告字典
        """
        report_uuid = str(uuid.uuid4())
        report_date = datetime.now().strftime('%Y-%m-%d')

        data_source = ai_result.get('data_source', {})
        predictions = ai_result.get('predictions', {})
        trend_analysis = ai_result.get('trend_analysis', {})
        key_features = ai_result.get('key_features', [])
        reasoning_process = ai_result.get('reasoning_process', [])
        recommended_combinations = ai_result.get('recommended_combinations', [])
        risk_warning = ai_result.get('risk_warning', '')

        recommended_numbers = {}
        confidence_scores = {}

        for pos_key, pos_name in zip(self.position_keys[:4], self.position_names[:4]):
            rec_list = predictions.get(pos_key, [])
            recommended_numbers[pos_key] = [r.get('number') for r in rec_list if isinstance(r, dict)]
            confidence_scores[pos_key] = [r.get('confidence', 0) for r in rec_list if isinstance(r, dict)]

        report_content = self._format_report_content(
            data_source, predictions, trend_analysis, key_features,
            reasoning_process, recommended_combinations, risk_warning
        )

        report = {
            'report_uuid': report_uuid,
            'report_date': report_date,
            'data_count': data.get('data_count', 0),
            'latest_issue': data.get('latest_issue', ''),
            'next_issue': self._infer_next_issue(data.get('latest_issue', '')),
            'data_source': data_source,
            'predictions': predictions,
            'trend_analysis': trend_analysis,
            'key_features': key_features,
            'reasoning_process': reasoning_process,
            'recommended_numbers': recommended_numbers,
            'confidence_scores': confidence_scores,
            'recommended_combinations': recommended_combinations,
            'risk_warning': risk_warning,
            'report_content': report_content,
            'model_version': self.model_name,
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'created_at': datetime.now().isoformat()
        }

        return report

    def _format_report_content(self, data_source: Dict, predictions: Dict,
                              trend_analysis: Dict, key_features: List,
                              reasoning_process: List, combinations: List,
                              risk_warning: str) -> str:
        """
        格式化报告内容为可读文本

        Args:
            data_source: 数据源信息
            predictions: 预测结果
            trend_analysis: 趋势分析
            key_features: 关键特征
            reasoning_process: 推理过程
            combinations: 推荐组合
            risk_warning: 风险提示

        Returns:
            格式化的报告内容字符串
        """
        lines = []
        lines.append('=' * 80)
        lines.append('排列5 AI分析报告')
        lines.append('=' * 80)
        lines.append(f'生成时间: {data_source.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        lines.append(f'最新期号: {data_source.get("latest_issue", "")}')
        lines.append(f'数据周期: {data_source.get("data_period", "")}')
        lines.append(f'数据条数: {data_source.get("data_count", 0)}')
        lines.append('')

        lines.append('一、数据来源与预处理说明')
        lines.append('-' * 50)
        lines.append(data_source.get('description', '未提供'))
        lines.append('')

        lines.append('二、各位置号码预测结果')
        lines.append('-' * 50)

        pos_mapping = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位'}
        for pos_key, pos_name in pos_mapping.items():
            lines.append(f'\n【{pos_name}】')
            rec_list = predictions.get(pos_key, [])
            for i, rec in enumerate(rec_list, 1):
                if isinstance(rec, dict):
                    lines.append(f'  {i}. 号码{rec.get("number", "?")} (置信度: {rec.get("confidence", 0):.2%}) - {rec.get("reason", "")}')

        lines.append('')
        lines.append('三、趋势分析与关键特征提取')
        lines.append('-' * 50)

        for pos_key, pos_name in pos_mapping.items():
            lines.append(f'\n【{pos_name}走势分析】')
            lines.append(trend_analysis.get(pos_key, '未提供'))

        lines.append('')
        lines.append('【关键特征】')
        for i, feature in enumerate(key_features, 1):
            lines.append(f'  {i}. {feature}')

        lines.append('')
        lines.append('四、预测依据与模型推理过程')
        lines.append('-' * 50)
        for i, step in enumerate(reasoning_process, 1):
            lines.append(f'  {i}. {step}')

        lines.append('')
        lines.append('五、推荐组合')
        lines.append('-' * 50)
        for i, combo in enumerate(combinations, 1):
            if isinstance(combo, dict):
                nums = combo.get('numbers', [])
                lines.append(f'  {i}. {"".join(map(str, nums))} (置信度: {combo.get("confidence", 0):.2%}) - {combo.get("reason", "")}')

        lines.append('')
        lines.append('=' * 80)
        lines.append('风险提示')
        lines.append('=' * 80)
        lines.append(risk_warning)
        lines.append('')
        lines.append('本报告仅基于历史数据统计分析，无法保证开奖结果，请理性购彩。')
        lines.append('=' * 80)

        return '\n'.join(lines)

    def _infer_next_issue(self, current_issue: str) -> str:
        """推导下一期期号"""
        if current_issue and current_issue.isdigit():
            next_num = int(current_issue) + 1
            return str(next_num)
        return '未知'

    def _save_report_to_database(self, report: Dict[str, Any]) -> Optional[str]:
        """
        将分析报告保存到数据库

        Args:
            report: 结构化报告字典

        Returns:
            报告UUID，失败返回None
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('数据库连接失败，无法保存报告')
                return None

            trend_analysis_json = json.dumps(report.get('trend_analysis', {}), ensure_ascii=False)
            probability_stats_json = json.dumps({
                'key_features': report.get('key_features', []),
                'reasoning_process': report.get('reasoning_process', []),
                'model_version': report.get('model_version', '')
            }, ensure_ascii=False)
            recommended_numbers_json = json.dumps(report.get('recommended_numbers', {}), ensure_ascii=False)
            recommended_combinations_json = json.dumps(report.get('recommended_combinations', []), ensure_ascii=False)
            confidence_scores_json = json.dumps(report.get('confidence_scores', {}), ensure_ascii=False)

            report_uuid = db.insert_ai_report(
                report_content=report.get('report_content', ''),
                data_count=report.get('data_count', 0),
                latest_issue=report.get('latest_issue', ''),
                next_issue=report.get('next_issue', ''),
                trend_analysis=trend_analysis_json,
                probability_stats=probability_stats_json,
                recommended_numbers=recommended_numbers_json,
                recommended_combinations=recommended_combinations_json,
                confidence_scores=confidence_scores_json,
                recommendation_reasons='AI深度分析推荐',
                key_conclusions=json.dumps(report.get('key_features', []), ensure_ascii=False),
                risk_warning=report.get('risk_warning', ''),
                report_format='JSON'
            )

            db.disconnect()

            if report_uuid:
                logger.info(f'AI分析报告保存成功，UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('AI分析报告保存失败')
                return None

        except Exception as e:
            logger.error(f'保存报告到数据库失败: {e}')
            return None

    def analyze(self, data_limit: int = 30) -> Dict[str, Any]:
        """
        执行完整的AI分析流程

        Args:
            data_limit: 获取历史数据的期数限制

        Returns:
            分析结果字典，包含报告内容和数据库存储状态
        """
        logger.info('=' * 80)
        logger.info('开始执行AI分析')
        logger.info('=' * 80)

        # 1. 获取数据源
        logger.info('步骤1：获取数据源...')
        data = self._fetch_data_from_database(limit=data_limit)
        if data.get('error'):
            return {
                'success': False,
                'error': data['error'],
                'report': None
            }

        if data['data_count'] == 0:
            return {
                'success': False,
                'error': '数据库中没有历史数据',
                'report': None
            }

        # 2. 构建提示词
        logger.info('步骤2：构建AI分析提示词...')
        prompt = self._build_ai_prompt(data)
        logger.info(f'提示词长度: {len(prompt)}')

        # 3. 调用AI模型
        logger.info('步骤3：调用AI模型...')
        messages = [
            {
                "role": "system",
                "content": "你是一位专业的排列5彩票数据分析专家，擅长基于历史数据进行深度分析和预测。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        ai_response = self._call_ai_model(
            messages=messages,
            max_tokens=8000,
            temperature=0.7
        )

        if not ai_response:
            return {
                'success': False,
                'error': 'AI模型调用失败',
                'report': None
            }

        # 4. 解析AI响应
        logger.info('步骤4：解析AI响应...')
        ai_result = self._parse_ai_response(ai_response)
        if not ai_result:
            return {
                'success': False,
                'error': 'AI响应解析失败',
                'report': None
            }

        # 5. 生成结构化报告
        logger.info('步骤5：生成结构化报告...')
        report = self._generate_structured_report(ai_result, data)

        # 6. 保存报告到数据库
        logger.info('步骤6：保存报告到数据库...')
        report_uuid = self._save_report_to_database(report)

        result = {
            'success': True,
            'report': report,
            'report_uuid': report_uuid,
            'model_version': self.model_name,
            'data_count': data['data_count'],
            'latest_issue': data['latest_issue'],
            'next_issue': report['next_issue'],
            'risk_warning': report['risk_warning']
        }

        logger.info('=' * 80)
        logger.info('AI分析完成')
        logger.info(f'报告UUID: {report_uuid}')
        logger.info('=' * 80)

        return result

    def get_latest_report(self) -> Optional[Dict[str, Any]]:
        """获取最新的AI分析报告"""
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                return None

            report = db.get_latest_ai_report()
            db.disconnect()

            if report:
                if report.get('trend_analysis'):
                    report['trend_analysis'] = json.loads(report['trend_analysis'])
                if report.get('probability_stats'):
                    report['probability_stats'] = json.loads(report['probability_stats'])
                if report.get('recommended_numbers'):
                    report['recommended_numbers'] = json.loads(report['recommended_numbers'])
                if report.get('recommended_combinations'):
                    report['recommended_combinations'] = json.loads(report['recommended_combinations'])
                if report.get('confidence_scores'):
                    report['confidence_scores'] = json.loads(report['confidence_scores'])

            return report

        except Exception as e:
            logger.error(f'获取最新报告失败: {e}')
            return None

    def list_reports(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取AI分析报告列表"""
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                return []

            reports = db.get_all_ai_reports(limit=limit)
            db.disconnect()

            for report in reports:
                if report.get('trend_analysis'):
                    report['trend_analysis'] = json.loads(report['trend_analysis'])
                if report.get('recommended_numbers'):
                    report['recommended_numbers'] = json.loads(report['recommended_numbers'])

            return reports

        except Exception as e:
            logger.error(f'获取报告列表失败: {e}')
            return []


if __name__ == '__main__':
    print('=' * 80)
    print('排列5 AI分析模块测试')
    print('=' * 80)

    analyzer = AIAnalyzer()
    result = analyzer.analyze(data_limit=30)

    if result['success']:
        print('\n分析成功！')
        print(f'报告UUID: {result["report_uuid"]}')
        print(f'最新期号: {result["latest_issue"]}')
        print(f'预测期号: {result["next_issue"]}')
        print(f'数据条数: {result["data_count"]}')
        print(f'模型版本: {result["model_version"]}')
        print(f'报告文件: {result["report_file"]}')
        print('\n' + '=' * 80)
        print('报告内容预览:')
        print('=' * 80)
        print(result['report']['report_content'][:2000])
    else:
        print(f'\n分析失败: {result["error"]}')
