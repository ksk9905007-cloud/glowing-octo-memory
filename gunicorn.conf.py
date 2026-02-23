import os

# Render는 PORT 환경 변수를 제공합니다. (기본값 10000)
port = os.environ.get('PORT', '10000')

# 모든 IP(0.0.0.0)에서 접속 가능하도록 바인딩
bind = f"0.0.0.0:{port}"

# 안정성을 위해 타임아웃과 워커 수 설정
timeout = 120
workers = 1
threads = 2

# 로그 설정
accesslog = '-'
errorlog = '-'
