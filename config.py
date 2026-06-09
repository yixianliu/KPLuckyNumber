DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': '',
    'database': 'lucky_number',
    'charset': 'utf8mb4'
}

SPIDER_CONFIG = {
    'pages': 10,
    'timeout': 15,
    'retry_count': 3,
    'delay_min': 3,
    'delay_max': 6
}

ANALYSIS_CONFIG = {
    'confidence_threshold': 0.8,
    'min_data_count': 100
}

REPORT_CONFIG = {
    'output_dir': 'reports/',
    'chart_format': 'png',
    'chart_dpi': 100
}
