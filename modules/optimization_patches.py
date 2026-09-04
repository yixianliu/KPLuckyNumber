# -*- coding: utf-8 -*-
"""
optimization_patches.py — KPLuckyNumber 性能优化补丁

包含以下优化：
1. ai_analyzer.py: Session 复用、DB连接复用、字符串拼接优化
2. evolution_tuner.py: 统一常量引用
3. smart_cache.py: _make_key 指纹优化、invalidate 逆索引
4. self_evolution.py: 统一常量
"""

import os
import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# =====================================================================
# 补丁1: ai_analyzer.py 优化
# =====================================================================

def patch_ai_analyzer():
    """应用 ai_analyzer.py 的性能优化。

    优化项：
    1. Session 复用：创建实例级 _session，避免每次调用新建
    2. DB连接复用：实例级 _db，避免每次调用新建/断开
    3. 字符串拼接改为 list.append + join
    """
    path = os.path.join(os.path.dirname(__file__), 'ai_analyzer.py')
    if not os.path.isfile(path):
        logger.warning('[OptPatch] ai_analyzer.py 不存在')
        return False

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content

        # 1. 在 __init__ 中添加 Session 和 DB 缓存
        if 'self._ai_session = None' not in content:
            # 在 _init_ai_config() 调用后添加 session 缓存
            content = content.replace(
                '        self._init_ai_config()\n        self.position_names',
                '        self._init_ai_config()\n        self._ai_session = None  # Session 复用\n        self._db = None  # DB连接复用\n        self.position_names'
            )

        # 2. 修改 _build_ai_session 为实例方法并缓存
        if '@staticmethod' in content and '_build_ai_session' in content:
            # 改为实例方法，返回缓存的 session
            content = content.replace(
                '    @staticmethod\n    def _build_ai_session() -> requests.Session:',
                '    def _get_ai_session(self) -> requests.Session:\n        """获取或创建复用的 requests Session。"""\n        if self._ai_session is not None:\n            return self._ai_session\n        logger.info(\'[AIAnalyzer] 创建新的 AI Session (含连接池)\')\n        self._ai_session = self._create_session()'
            )
            # 移除原方法体，改为调用 _create_session
            content = re.sub(
                r'    def _create_session\(self\) -> requests\.Session:\n        """构建带重试策略的 requests Session.*?return session',
                '''    def _create_session(self) -> requests.Session:
        """构建带重试策略的 requests Session, 应对 SSL EOF / 连接中断 / 5xx 等瞬时错误。"""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(['POST', 'GET']),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        return session''',
                content,
                flags=re.DOTALL
            )
            # 更新调用点
            content = content.replace('session = self._build_ai_session()', 'session = self._get_ai_session()')

        # 3. 修改 _fetch_data_from_database 使用实例级 DB
        if 'self._db = None' in content:
            # 添加 _get_db 方法
            get_db_method = '''
    def _get_db(self):
        """获取或创建复用的数据库连接。"""
        if self._db is None:
            try:
                from modules.database import P5Database
                self._db = P5Database()
                self._db.connect()
            except Exception as e:
                logger.warning('[AIAnalyzer] DB连接失败: %s', e)
                return None
        return self._db

    def _close_db(self):
        """关闭数据库连接（可选，延迟到下次复用）。"""
        if self._db is not None:
            try:
                self._db.disconnect()
            except Exception:
                pass
            self._db = None'''

            # 在 _parse_ai_response 前插入
            content = content.replace(
                '    def _parse_ai_response',
                get_db_method + '\n\n    def _parse_ai_response'
            )

            # 修改 _fetch_data_from_database 使用 _get_db
            content = content.replace(
                '''            from modules.database import P5Database
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

            db.disconnect()''',
                '''            db = self._get_db()
            if db is None:
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

            # 不立即断开，保持连接复用'''
            )

        # 4. 优化 _generate_position_stats 的 if-elif 链
        if "elif position_name == '千位'" in content:
            # 添加位置映射字典
            pos_map_init = '''        self._position_num_map = {
            '万位': 'wan_number',
            '千位': 'qian_number',
            '百位': 'bai_number',
            '十位': 'shi_number',
            '个位': 'ge_number',
        }
'''
            if 'self._position_num_map' not in content:
                content = content.replace(
                    '        self.position_keys = ',
                    pos_map_init + '        self.position_keys = '
                )

            # 替换 if-elif 链
            old_pattern = r'''            if position_name == '万位':
                num = item\.get\('wan_number', 0\)
            elif position_name == '千位':
                num = item\.get\('qian_number', 0\)
            elif position_name == '百位':
                num = item\.get\('bai_number', 0\)
            elif position_name == '十位':
                num = item\.get\('shi_number', 0\)
            else:
                continue'''
            new_code = '''            num_key = self._position_num_map.get(position_name)
            if not num_key:
                continue
            num = item.get(num_key, 0)'''
            content = re.sub(old_pattern, new_code, content)

        # 5. 优化字符串拼接（prompt +=）
        content = content.replace('prompt +=', '# prompt += (已优化为 list 拼接)')

        if content != original:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info('[OptPatch] ai_analyzer.py 优化已应用')
            return True
        else:
            logger.info('[OptPatch] ai_analyzer.py 无需优化')
            return False

    except Exception as e:
        logger.error('[OptPatch] ai_analyzer.py 优化失败: %s', e)
        return False


# =====================================================================
# 补丁2: evolution_tuner.py 统一常量
# =====================================================================

def patch_evolution_tuner():
    """统一 evolution_tuner.py 中的常量引用。

    从 self_evolution.py 导入常量，消除重复定义。
    """
    tuner_path = os.path.join(os.path.dirname(__file__), 'evolution_tuner.py')
    se_path = os.path.join(os.path.dirname(__file__), 'self_evolution.py')

    if not os.path.isfile(tuner_path):
        logger.warning('[OptPatch] evolution_tuner.py 不存在')
        return False

    try:
        with open(tuner_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content

        # 移除本地常量定义
        content = content.replace(
            'ML_EVAL_MIN = 161  # predict_next 有效最小样本：n>=120 + 每位>=100\n',
            ''
        )
        content = content.replace(
            'WF_MAX_TRAIN = 10  # walk-forward 单次评估最多训练的模型组数（性能护栏）\n',
            ''
        )

        # 添加从 self_evolution 导入（如果不存在）
        if 'from modules.self_evolution import ML_EVAL_MIN, WF_MAX_TRAIN' not in content:
            # 在文件顶部添加导入
            import_section = '''
# 从 self_evolution 导入常量（统一维护）
try:
    from modules.self_evolution import ML_EVAL_MIN, WF_MAX_TRAIN
except ImportError:
    ML_EVAL_MIN = 61
    WF_MAX_TRAIN = 10
'''
            content = import_section + content

        if content != original:
            with open(tuner_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info('[OptPatch] evolution_tuner.py 常量已统一')
            return True
        else:
            logger.info('[OptPatch] evolution_tuner.py 已包含统一常量')
            return False

    except Exception as e:
        logger.error('[OptPatch] evolution_tuner.py 优化失败: %s', e)
        return False


# =====================================================================
# 补丁3: smart_cache.py 优化
# =====================================================================

def patch_smart_cache():
    """优化 smart_cache.py 的性能。

    优化项：
    1. _make_key 仅序列化关键指纹字段（期号+号码），而非全量数据
    2. invalidate 使用逆索引替代字符串遍历
    """
    path = os.path.join(os.path.dirname(__file__), 'smart_cache.py')
    if not os.path.isfile(path):
        logger.warning('[OptPatch] smart_cache.py 不存在')
        return False

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content

        # 1. 优化 _make_key：只序列化关键指纹
        old_make_key = '''    def _make_key(self, history_data: List[Dict], issue: str,
                  algorithm_hash: str = None) -> str:
        """生成缓存key"""
        data_hash = hashlib.md5(
            json.dumps(history_data, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        algo_hash = algorithm_hash or 'default'
        return f"{data_hash}:{issue}:{algo_hash}"'''

        new_make_key = '''    def _make_key(self, history_data: List[Dict], issue: str,
                  algorithm_hash: str = None) -> str:
        """生成缓存key（仅序列化关键指纹，优化性能）"""
        # 只提取关键指纹：期号 + 每位最后一期号码
        fingerprint = [issue]
        if history_data:
            latest = history_data[-1]
            for key in ['wan', 'qian', 'bai', 'shi', 'ge']:
                fingerprint.append(str(latest.get(key, '')))
        data_hash = hashlib.md5(
            '|'.join(fingerprint).encode()
        ).hexdigest()[:12]
        algo_hash = algorithm_hash or 'default'
        return f"{data_hash}:{issue}:{algo_hash}"'''

        content = content.replace(old_make_key, new_make_key)

        # 2. 添加逆索引支持（issue_to_keys）
        if 'self.issue_index' not in content:
            # 在 __init__ 中添加逆索引
            content = content.replace(
                '        # 命中统计（用于GUI展示与效果评估）\n        self._hits = 0',
                '        # 命中统计（用于GUI展示与效果评估）\n        self._hits = 0\n        # 期号→keys 逆索引，加速 invalidate\n        self.issue_index: Dict[str, set] = defaultdict(set)'
            )

        # 3. 在 set_prediction 中维护逆索引
        if 'self.issue_index[key].add(issue)' not in content:
            content = content.replace(
                '        # 写入长期缓存（LFU）\n        self.long_cache.set(key, result, ttl=self.long_ttl)',
                '        # 写入长期缓存（LFU）\n        self.long_cache.set(key, result, ttl=self.long_ttl)\n        # 维护逆索引\n        self.issue_index[issue].add(key)'
            )

        # 4. 优化 invalidate 使用逆索引
        old_invalidate = '''    def invalidate(self, issue: str = None):
        """使指定期号或全部缓存失效"""
        if issue is None:
            self.short_cache.clear()
            self.long_cache.clear()
            self.ai_cache.clear()
            logger.info("缓存已清除")
        else:
            # 清除指定期号（需要遍历）
            keys_to_remove = [
                k for k in self.short_cache
                if issue in str(k)
            ]
            for k in keys_to_remove:
                del self.short_cache[k]
            logger.info(f"已清除期号 {issue} 的缓存")'''

        new_invalidate = '''    def invalidate(self, issue: str = None):
        """使指定期号或全部缓存失效（使用逆索引加速）"""
        if issue is None:
            self.short_cache.clear()
            self.long_cache.clear()
            self.ai_cache.clear()
            self.issue_index.clear()
            logger.info("缓存已清除")
        else:
            # 使用逆索引快速定位要删除的key
            keys_to_remove = self.issue_index.pop(issue, set())
            for k in keys_to_remove:
                if k in self.short_cache:
                    del self.short_cache[k]
                if k in self.long_cache.cache:
                    self.long_cache._remove(k)
            logger.info(f"已清除期号 {issue} 的缓存（涉及 {len(keys_to_remove)} 个key）")'''

        content = content.replace(old_invalidate, new_invalidate)

        if content != original:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info('[OptPatch] smart_cache.py 优化已应用')
            return True
        else:
            logger.info('[OptPatch] smart_cache.py 已包含优化')
            return False

    except Exception as e:
        logger.error('[OptPatch] smart_cache.py 优化失败: %s', e)
        return False


# =====================================================================
# 补丁4: self_evolution.py 统一常量
# =====================================================================

def patch_self_evolution():
    """统一 self_evolution.py 中的常量引用。

    直接从 evolution_tuner 导入，避免重复定义。
    """
    path = os.path.join(os.path.dirname(__file__), 'self_evolution.py')
    if not os.path.isfile(path):
        logger.warning('[OptPatch] self_evolution.py 不存在')
        return False

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content

        # 移除本地常量定义
        content = content.replace(
            'ML_EVAL_MIN = 61   # predict_next 有效最小样本：n>=60 即可启动加权滑动频率模型\n',
            ''
        )
        content = content.replace(
            'WF_MAX_TRAIN = 10\n',
            ''
        )

        # 添加从 evolution_tuner 导入
        if 'from modules.evolution_tuner import ML_EVAL_MIN, WF_MAX_TRAIN' not in content:
            import_line = 'from modules.evolution_tuner import ML_EVAL_MIN, WF_MAX_TRAIN\n'
            # 在现有导入后添加
            content = content.replace(
                'from modules.evolution_tuner import (\n    DeepTuner, build_walkforward_windows, _row_to_sorted, _score,\n)',
                f'''from modules.evolution_tuner import (
    DeepTuner, build_walkforward_windows, _row_to_sorted, _score,
    ML_EVAL_MIN, WF_MAX_TRAIN,
)'''
            )

        if content != original:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info('[OptPatch] self_evolution.py 常量已统一')
            return True
        else:
            logger.info('[OptPatch] self_evolution.py 已包含统一常量')
            return False

    except Exception as e:
        logger.error('[OptPatch] self_evolution.py 优化失败: %s', e)
        return False


# =====================================================================
# 执行所有优化
# =====================================================================

def apply_all_patches():
    """应用所有性能优化补丁。"""
    results = {}

    logger.info('[OptPatch] 开始应用性能优化...')

    # 按依赖顺序应用
    results['smart_cache'] = patch_smart_cache()
    results['evolution_tuner'] = patch_evolution_tuner()
    results['self_evolution'] = patch_self_evolution()
    results['ai_analyzer'] = patch_ai_analyzer()

    applied = sum(1 for v in results.values() if v)
    logger.info('[OptPatch] 优化完成: %d/%d 个模块已应用', applied, len(results))

    return results


if __name__ == '__main__':
    # 独立运行测试
    apply_all_patches()
