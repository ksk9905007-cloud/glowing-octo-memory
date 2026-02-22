import os

# Render에서는 기본적으로 10000 포트를 사용하거나 PORT 환경변수를 따릅니다.
port = os.environ.get('PORT', '5000')
bind = f"0.0.0.0:{port}"
workers = 1
threads = 2
timeout = 120
accesslog = '-'
errorlog = '-'
