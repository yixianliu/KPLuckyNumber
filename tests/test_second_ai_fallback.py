import sys
import os
# Ensure project root is on sys.path for imports when running tests directly
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.article_analyzer import ArticleAnalyzer


def run_test():
    analyzer = ArticleAnalyzer()

    # Simulate redis_data with first_ai_result
    redis_data = {
        'ai_analysis': {
            'issue_number': '2026165',
            'forecast_numbers': {
                'wan': [1, 2],
                'qian': [3],
                'bai': [],
                'shi': [4],
                'ge': [5]
            }
        }
    }

    # Simulate db_history minimal
    db_history = {
        'latest_issue': '2026165',
        'data_count': 30,
        'history_data': []
    }

    result = analyzer.second_ai_analysis(redis_data, db_history)
    print('Second AI analysis result (fallback expected):')
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run_test()

