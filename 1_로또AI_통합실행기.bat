@echo off
title 🎯 로또 AI 프리미엄 통합 실행기
chcp 65001 > nul
cd /d "%~dp0"

echo ===================================================
echo   🚀 로또 AI 시스템 통합 실행 관리자
echo ===================================================
echo.

:: 1. 기존 프로세스 정리
echo [1/4] 기존 실행된 파이썬 관련 창을 정리합니다...
taskkill /f /im python.exe /t >nul 2>&1
echo      - 정리 완료.

:: 2. 파이썬 엔진 체크
echo [2/4] 파이썬 설치 상태를 점검 중입니다...
set PY_CMD=python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    set PY_CMD=py
    py --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [❌] 파이썬이 설치되어 있지 않습니다.
        echo      https://www.python.org 에서 설치 후 다시 실행하세요.
        pause
        exit
    )
)
echo      - 파이썬 감지됨: %PY_CMD%

:: 3. 필수 라이브러리 검사 및 설치
echo [3/4] 필요 라이브러리를 최신 버전으로 동기화합니다...
echo      (잠시만 기다려 주세요. 환경에 따라 10~30초 소요됩니다.)
%PY_CMD% -m pip install --upgrade flask flask-cors playwright playwright-stealth >nul 2>&1
%PY_CMD% -m playwright install chromium >nul 2>&1
echo      - 환경 최적화 완료.

:: 4. 서버 실행
echo [4/4] 로또 AI 엔진(lotto_server.py) 가동 중...
echo.
echo ---------------------------------------------------
echo   🟢 서버가 정상 작동 중입니다!
echo   🟢 이 창은 끄지 마시고, 자동으로 열린 브라우저를 확인하세요.
echo   🟢 만약 창이 열리지 않으면 'lotto_ai.html'을 직접 여세요.
echo ---------------------------------------------------
echo.

%PY_CMD% lotto_server.py

if %errorlevel% neq 0 (
    echo.
    echo [❌] 서버 가동 중 에러가 발생했습니다.
    echo      위에 표시된 메시지를 확인해 주세요.
    pause
)
