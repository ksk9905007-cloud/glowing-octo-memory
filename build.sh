#!/usr/bin/env bash
# exit on error
set -o errexit

# pip install
pip install -r requirements.txt

# 프로젝트 루트 경로 확보
PROJECT_ROOT=$(pwd)
export PLAYWRIGHT_BROWSERS_PATH=$PROJECT_ROOT/pw-browsers

# 브라우저 설치
echo "Installing Playwright browsers to $PLAYWRIGHT_BROWSERS_PATH..."
python -m playwright install chromium
