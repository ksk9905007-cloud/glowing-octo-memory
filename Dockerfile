# Playwright 공식 Python 이미지 사용 (브라우저 및 시스템 의존성 포함)
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

# 작업 디렉토리 설정
WORKDIR /app

# 종속성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# Docker 환경임을 명시
ENV DOCKER_ENV=true
ENV PORT=10000

# Gunicorn으로 서버 실행
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT app:app"]
