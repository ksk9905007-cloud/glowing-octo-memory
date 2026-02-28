# Use official Playwright Python image
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV RENDER true
ENV PORT 10000

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 공식 이미지는 이미 브라우저가 설치되어 있지만, 혹시 모르니 최소한으로 유지
RUN playwright install chromium

# Copy project files
COPY . .

# Expose port
EXPOSE 10000

# Start application using Gunicorn
# Bind to 0.0.0.0:10000 which is Render's default
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --timeout 180 --workers 1 --threads 1 --worker-class gthread"]
