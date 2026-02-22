# Python 3.11 이미지를 베이스로 사용
FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 필요한 시스템 패키지 설치 (Playwright 종속성 대비)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libgbm-dev \
    libnss3 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# 종속성 파일 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치
RUN playwright install chromium
RUN playwright install-deps chromium

# 소스 코드 복사
COPY . .

# Flask 포트 설정 (Render 등에서 PORT 환경변수 제공)
ENV PORT=5000
EXPOSE 5000

# 서버 실행 (Render의 PORT 환경변수 사용)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT lotto_server:app"]
