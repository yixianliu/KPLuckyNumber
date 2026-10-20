"""
统一预测持久化模块（v5.0 §8 阶段八）

职责：
1. 字段抽取   - 把 final_report 抽平成 p5_prediction_record 三列（扁平化，对齐验证契约）
2. 幂等写入   - 同 target_issue 已有 pending 记录则原地更新，避免同期号重复行
3. 可达性预检 + 指数退避重试（默认 0.5 / 1.0 / 2.0s）
4. 失败不静默 - 抛 DBWriteError，由调用方记日志 + 用户提示 + 本地兜底
5. 本地兜底   - predictions/ 目录写 JSON，DB 不可用时保证产物不丢
6. 配置校验   - DB_CONFIG 与 .env 一致性检查（无硬编码密钥）

设计说明：
- 本模块是纯模块：不导入 pymysql / P5Database，DB 客户端以参数注入，import 零副作用，
  因此可离线单测。调用方负责传入已 connect 的 P5Database 实例。
- 候选号统一取每位置前 4 个（对齐项目约定 [:4]）。
- 表 p5_prediction_record 的唯一定义键是 (report_uuid, target_issue) 复合索引；
  本表**刻意**允许同期号多行（一次开奖会被流水线/快速预测多次预测），
  去重发生在读取侧（database.DEDUPED_VERIFIED_SQL）。因此**不加** target_issue 单列唯一索引——
  现有数据已不满足该约束（实测 1142 行对应 972 个期号），加索引会直接失败。
  本模块改为应用层幂等：同期号存在 pending 行时原地更新。

诚实边界：本模块只负责「忠实落盘」，不对预测命中率做任何承诺。
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 每个位置的候选号数量（对齐项目约定 [:4]）
CANDIDATE_LIMIT = 4
# 五个位置（万/千/百/十/个）
POSITIONS: Tuple[str, ...] = ('wan', 'qian', 'bai', 'shi', 'ge')
# 默认重试退避序列（秒），与 §8.2.2 的 0.5/1.0/2.0s 对齐
DEFAULT_BACKOFFS: Tuple[float, ...] = (0.5, 1.0, 2.0)


class DBWriteError(Exception):
    """持久化最终失败（重试耗尽）。

    调用方必须显式处理：记日志 + 用户提示「预测已生成但持久化失败」+ 本地兜底。
    不得静默吞掉——静默丢数据是本模块要根治的问题。
    """

    def __init__(self, target_issue: str, source: str, attempts: int, cause: Optional[BaseException]):
        super().__init__(
            f'持久化失败 target_issue={target_issue} source={source} '
            f'attempts={attempts} cause={cause!r}'
        )
        self.target_issue = target_issue
        self.source = source
        self.attempts = attempts
        self.cause = cause


@dataclass
class PersistenceResult:
    """一次持久化尝试的结果。

    persisted: True 表示 DB 写入成功；False 表示降级为本地兜底（或全部失败）
    db_ok:     DB 写入是否成功（与 fallback_ok 互不排斥）
    fallback_ok: 本地 JSON 兜底是否写出
    local_path: 本地兜底文件路径（未兜底时 None）
    attempts:  实际写入尝试次数
    updated:   True 表示命中已有 pending 行做原地更新（应用层幂等）
    inserted:  True 表示新插入一行
    error:     失败原因（成功时 None）
    """

    persisted: bool = False
    db_ok: bool = False
    fallback_ok: bool = False
    local_path: Optional[str] = None
    attempts: int = 0
    updated: bool = False
    inserted: bool = False
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """整体是否可接受：DB 成功，或本地兜底成功。"""
        return self.db_ok or self.fallback_ok


def _to_int_list(value: Any, limit: int = CANDIDATE_LIMIT) -> List[int]:
    """把任意形态的候选值规范成 [int, ...]，最多 limit 个。

    兼容 list / tuple / dict / str / None；非法元素丢弃，绝不抛异常。
    """
    if value is None:
        return []
    if isinstance(value, dict):
        # 形如 {'numbers': [...], 'confidence': [...]} → 取 numbers
        value = value.get('numbers')
    if isinstance(value, str):
        # '1234' → [1,2,3,4]；'1,2,3' → [1,2,3]
        cleaned = value.replace(',', '').replace(' ', '')
        if cleaned.isdigit() and len(cleaned) <= 5:
            return [int(c) for c in cleaned[:limit]]
        parts = [p for p in value.replace(',', ' ').split() if p.strip().lstrip('-').isdigit()]
        return [int(p) for p in parts[:limit]]
    if isinstance(value, (list, tuple)):
        out: List[int] = []
        for item in value:
            if isinstance(item, dict):
                # {'number': 3, 'confidence': 0.2}
                raw = item.get('number', item.get('numbers'))
                out.extend(_to_int_list(raw, limit - len(out)))
                continue
            if isinstance(item, (int, float)):
                try:
                    n = int(round(float(item)))
                except (TypeError, ValueError):
                    continue
                if 0 <= n <= 9:
                    out.append(n)
        return out[:limit]
    if isinstance(value, (int, float)):
        return [int(round(float(value)))] if 0 <= int(round(float(value))) <= 9 else []
    return []


def extract_prediction_fields(final_report: Dict[str, Any],
                              target_issue: str) -> Dict[str, Any]:
    """从 final_report 抽取 p5_prediction_record 写入所需字段。

    兼容三种来源结构（顺序即优先级）：
      1. final_report['prediction']            —— 四步流水线 step4 产物
      2. final_report['trend_prediction']       —— 快速预测 / 多源走势产物
      3. final_report['fused_probabilities']     —— List[Dict[int,float]]，取每位置 top-4

    Returns:
        dict，含 report_uuid / target_issue / predicted_numbers(JSON str) /
        predicted_combinations(JSON str) / confidence_scores(JSON str) /
        fused_probabilities(JSON str) / source_shape
    """
    report = final_report if isinstance(final_report, dict) else {}
    source_shape = 'empty'
    raw_positions: Dict[str, Any] = {}

    for key in ('prediction', 'trend_prediction'):
        candidate = report.get(key)
        # 只认「位置键存在且值非空」的单元格；键存在但值为 None 的占位不算有效来源
        valid_positions = [p for p in POSITIONS
                           if isinstance(candidate, dict)
                           and p in candidate and candidate.get(p) is not None]
        if valid_positions:
            raw_positions = candidate
            source_shape = key
            break

    # 3. fused_probabilities 兜底：List[Dict[int,float]] 长 5
    if not raw_positions:
        fused = report.get('fused_probabilities')
        if isinstance(fused, list) and fused:
            raw_positions = {}
            source_shape = 'fused_probabilities'
            for idx, pos in enumerate(POSITIONS):
                cell = fused[idx] if idx < len(fused) else None
                if isinstance(cell, dict) and cell:
                    top = sorted(cell.items(), key=lambda kv: float(kv[1]), reverse=True)[:CANDIDATE_LIMIT]
                    raw_positions[pos] = {'numbers': [int(k) for k, _ in top]}

    # 未匹配到任何可抽取的候选号 → 形状如实标记为 'empty'。
    # （raw_positions 可能在 key 存在但值为 None/空时被赋值，此时不得谎报形状）
    if not raw_positions:
        source_shape = 'empty'

    flat_predicted: Dict[str, List[int]] = {}
    flat_confidence: Dict[str, List[float]] = {}
    for pos in POSITIONS:
        cell = raw_positions.get(pos, [])
        nums = _to_int_list(cell if not isinstance(cell, dict) else cell.get('numbers'))
        flat_predicted[pos] = nums[:CANDIDATE_LIMIT]
        conf = cell.get('confidence') if isinstance(cell, dict) else None
        flat_confidence[pos] = [round(float(c), 4) for c in (conf or [])
                                if isinstance(c, (int, float))]

    combos = report.get('recommended_combinations')
    if not isinstance(combos, list):
        combos = []

    fused = report.get('fused_probabilities')
    return {
        'report_uuid': str(report.get('report_uuid') or report.get('uuid') or ''),
        'target_issue': str(target_issue or ''),
        'predicted_numbers': json.dumps(flat_predicted, ensure_ascii=False),
        'predicted_combinations': json.dumps(combos, ensure_ascii=False),
        'confidence_scores': json.dumps(flat_confidence, ensure_ascii=False),
        'fused_probabilities': json.dumps(fused, ensure_ascii=False) if fused else None,
        'source_shape': source_shape,
        'has_candidates': any(flat_predicted.values()),
    }


def validate_db_config(db_config: Optional[Dict[str, Any]] = None) -> List[str]:
    """校验 DB 配置，返回问题列表（空列表 = 通过）。

    覆盖 §8.2.3：缺失 .env 时报明确错误而非空密码连接；密钥不得硬编码。
    配置源唯一：默认从 config.DB_CONFIG 读取。
    """
    problems: List[str] = []
    if db_config is None:
        try:
            import config as cfg
            db_config = getattr(cfg, 'DB_CONFIG', None)
        except Exception as e:
            return [f'无法导入 config 模块，DB 配置不可用: {e}']
    if not isinstance(db_config, dict) or not db_config:
        return ['DB_CONFIG 缺失或不是字典']

    # 必需字段
    for key in ('host', 'port', 'user', 'database'):
        val = db_config.get(key)
        if val in (None, ''):
            problems.append(f'DB_CONFIG.{key} 缺失（.env 未配置）')

    port = db_config.get('port')
    try:
        port_n = int(port)
    except (TypeError, ValueError):
        problems.append(f'DB_CONFIG.port 不是整数: {port!r}')
    else:
        if not (1 <= port_n <= 65535):
            problems.append(f'DB_CONFIG.port 越界: {port_n}')

    # 空密码是明确的配置问题，不能悄悄连上去
    if db_config.get('password') in (None, ''):
        problems.append(
            "DB_CONFIG.password 为空——请在 config/.env 设置 DB_PASSWORD 注入，"
            "空密码会静默连接失败或连到匿名账户"
        )
    charset = db_config.get('charset')
    if charset and charset.lower() not in ('utf8mb4', 'utf8', 'gbk'):
        problems.append(f'DB_CONFIG.charset 非预期值: {charset!r}（建议 utf8mb4）')

    # 硬编码密钥复检：password 形如真实凭据而非占位符
    pw = str(db_config.get('password') or '')
    if pw and pw.lower() in ('password', '123456', 'root', 'mysql', '12345678', '123456789'):
        problems.append('DB_CONFIG.password 使用弱默认值，建议通过 .env 注入强密码')

    return problems


def write_local_fallback(target_issue: str, final_report: Dict[str, Any],
                         out_dir: Optional[str] = None,
                         fields: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """把预测产物写到本地 JSON，DB 不可达时保证不丢。

    目录默认为项目根下 predictions/（已存在）。文件名含期号与时间戳，避免互相覆盖。

    Returns:
        写出文件路径；写入失败返回 None（绝不抛异常）。
    """
    try:
        if out_dir is None:
            out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   'predictions')
        os.makedirs(out_dir, exist_ok=True)
        safe_issue = str(target_issue or 'unknown').replace('/', '_').replace('\\', '_')
        stamp = time.strftime('%Y%m%d_%H%M%S')
        path = os.path.join(out_dir, f'prediction_{safe_issue}_{stamp}.json')
        payload = {
            'target_issue': str(target_issue or ''),
            'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'reason': 'DB 持久化失败，本地兜底',
            'fields': fields or {},
            'final_report': final_report if isinstance(final_report, dict) else {},
        }
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        logger.info('预测已本地兜底: %s', path)
        return path
    except Exception as e:
        logger.error('本地兜底写入失败: %s', e)
        return None


def _find_pending_id(db, target_issue: str) -> Optional[int]:
    """查找同期号已有的 pending 记录 id（应用层幂等依据）。"""
    try:
        db.cursor.execute(
            'SELECT id FROM p5_prediction_record '
            'WHERE target_issue = %s AND verification_status = %s ORDER BY id DESC LIMIT 1',
            (str(target_issue), 'pending'),
        )
        row = db.cursor.fetchone()
        if row:
            # 兼容 dict / tuple 两种 cursor 返回
            return int(row[0] if isinstance(row, (tuple, list)) else row.get('id'))
    except Exception as e:
        logger.warning('查找 pending 记录失败（继续尝试插入）: %s', e)
    return None


def upsert_prediction_record(db, report_uuid: str, target_issue: str,
                             fields: Dict[str, Any]) -> Tuple[bool, str]:
    """写入预测记录（应用层幂等：同期号 pending 行原地更新）。

    Returns:
        (成功?, 说明)。成功说明为 'inserted' / 'updated'。
        失败时返回 (False, 错误信息)——不抛异常，由调用方决定是否重试。
    """
    # uuid 取值顺序：显式入参优先；入参为空且 fields 自带时沿用；两者皆空才自动生成。
    # （fields 中的 report_uuid 来自 _fr 产物的原始 uuid，仅作为兜底）
    uuid_val = str(report_uuid or '')
    if not uuid_val:
        uuid_val = str(fields.get('report_uuid') or '')
    if not uuid_val:
        import uuid as _uuid_mod
        uuid_val = _uuid_mod.uuid4().hex
    if not str(target_issue or '').strip():
        return False, 'target_issue 为空，拒绝写入'

    pending_id = _find_pending_id(db, target_issue)
    if pending_id is not None:
        try:
            db.cursor.execute(
                'UPDATE p5_prediction_record SET '
                'predicted_numbers = %s, predicted_combinations = %s, '
                'confidence_scores = %s, report_uuid = %s '
                'WHERE id = %s AND verification_status = %s',
                (fields['predicted_numbers'], fields['predicted_combinations'],
                 fields['confidence_scores'], uuid_val, pending_id, 'pending'),
            )
            db.connection.commit()
            return True, 'updated'
        except Exception as e:
            return False, f'更新 pending 记录失败: {e}'

    try:
        ok = db.insert_prediction_record(
            report_uuid=uuid_val,
            target_issue=str(target_issue),
            predicted_numbers=fields['predicted_numbers'],
            predicted_combinations=fields['predicted_combinations'],
            confidence_scores=fields['confidence_scores'],
        )
        if ok:
            return True, 'inserted'
        return False, 'insert_prediction_record 返回 False（DB 层已记录错误）'
    except Exception as e:
        return False, f'写入异常: {e}'


def upsert_with_retry(db, report_uuid: str, target_issue: str,
                      fields: Dict[str, Any], *,
                      max_retries: int = 3,
                      backoffs: Sequence[float] = DEFAULT_BACKOFFS,
                      connect=None,
                      sleep: Optional[Callable[[float], None]] = None,
                      source: str = 'unknown') -> Tuple[bool, str, int]:
    """带可达性预检与指数退避重试的写入。

    Args:
        db:          已连接的 P5Database 实例（需提供 cursor / connection / insert_prediction_record）
        max_retries: 最大写入尝试次数（1 表示不重试）
        backoffs:    重试间隔序列；第 n 次失败后等待 backoffs[n]（不足时沿用末位）
        connect:     可选的重连回调（返回 True/False），失败时用于重建连接
        sleep:       可注入的睡眠函数（测试用），None 用 time.sleep
        source:      来源标识，仅用于日志

    Returns:
        (成功?, 说明, 实际尝试次数)
    """
    if sleep is None:
        sleep = time.sleep
    attempts = max(1, int(max_retries))
    last_err = '未执行'

    for attempt in range(attempts):
        # 可达性预检：连接失效时尝试重连一次
        if connect is not None and attempt > 0:
            try:
                if not connect():
                    last_err = '重连失败'
            except Exception as e:
                last_err = f'重连异常: {e}'

        ok, note = upsert_prediction_record(db, report_uuid, target_issue, fields)
        if ok:
            return True, note, attempt + 1
        last_err = note
        if attempt < attempts - 1:
            wait = float(backoffs[min(attempt, len(backoffs) - 1)])
            logger.warning('持久化第%d次失败(%s)，%.1fs 后重试', attempt + 1, note, wait)
            sleep(wait)

    return False, last_err, attempts


def persist_prediction(db, report_uuid: str, target_issue: str,
                       final_report: Dict[str, Any], *,
                       source: str = 'pipeline',
                       max_retries: int = 3,
                       backoffs: Sequence[float] = DEFAULT_BACKOFFS,
                       connect=None,
                       sleep: Optional[Callable[[float], None]] = None,
                       local_dir: Optional[str] = None,
                       fallback: bool = True) -> PersistenceResult:
    """统一持久化入口：抽取 → 预检重试写入 → 失败本地兜底。

    这是 §8.2.1 的唯一入口，三条预测路径都应调用它，避免各自实现导致覆盖不全。

    Raises:
        DBWriteError: DB 写入与本地兜底**同时**失败时抛出（不静默）。
    """
    result = PersistenceResult()
    fields = extract_prediction_fields(final_report or {}, target_issue)
    result.meta = {'source_shape': fields['source_shape'],
                   'has_candidates': fields['has_candidates'],
                   'source': source}

    if not fields['has_candidates']:
        # 没有可用候选号时写入无意义，但仍尝试兜底以保留现场
        result.error = 'final_report 无可抽取的候选号'
        logger.warning('%s', result.error)
        if fallback:
            path = write_local_fallback(target_issue, final_report or {},
                                        out_dir=local_dir, fields=fields)
            result.fallback_ok = bool(path)
            result.local_path = path
            result.persisted = result.fallback_ok
            if result.persisted:
                return result
        # 关闭兜底 / 兜底写不进 → 与「DB 失败 + 无兜底」同等对待，抛出不静默
        raise DBWriteError(str(target_issue), source, 0, cause=None)

    ok, note, attempts = upsert_with_retry(
        db, report_uuid, target_issue, fields,
        max_retries=max_retries, backoffs=backoffs,
        connect=connect, sleep=sleep, source=source,
    )
    result.attempts = attempts
    result.db_ok = ok
    if ok:
        result.updated = (note == 'updated')
        result.inserted = (note == 'inserted')
        result.persisted = True
        result.error = None
        return result

    result.error = f'DB 写入失败: {note}'
    logger.error('持久化失败 target_issue=%s source=%s: %s', target_issue, source, note)

    if fallback:
        path = write_local_fallback(target_issue, final_report or {},
                                    out_dir=local_dir, fields=fields)
        result.fallback_ok = bool(path)
        result.local_path = path
        result.persisted = result.fallback_ok

    if not result.persisted:
        raise DBWriteError(str(target_issue), source, attempts, cause=None)
    return result


__all__ = [
    'CANDIDATE_LIMIT', 'POSITIONS', 'DEFAULT_BACKOFFS',
    'DBWriteError', 'PersistenceResult',
    'extract_prediction_fields', 'validate_db_config',
    'write_local_fallback', 'upsert_prediction_record',
    'upsert_with_retry', 'persist_prediction',
]
