import time
import random
import logging
import webbrowser
from threading import Timer
from flask import Flask, request, jsonify, send_from_directory
import os
import sys

# 로깅 설정 (최상단)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 브라우저 경로 및 환경 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Docker 환경에서는 공식 이미지의 기본 브라우저 위치를 사용합니다.

from flask_cors import CORS
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

app = Flask(__name__)
CORS(app)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def is_logged_in(page):
    try:
        content = page.content()
        return ".btn_logout" in content or "로그아웃" in content
    except: return False

def do_login(page, user_id, user_pw):
    try:
        page.goto("https://www.dhlottery.co.kr/login", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector("#inpUserId", timeout=10000)
        page.fill("#inpUserId", user_id)
        page.fill("#inpUserPswdEncn", user_pw)
        time.sleep(1)
        page.click("#btnLogin")
        
        for _ in range(10):
            if is_logged_in(page): return True
            time.sleep(0.5)
        return False
    except: return False

def attempt_click(context, selectors, text=None):
    for sel in selectors:
        try:
            el = context.locator(sel).first
            el.wait_for(state="attached", timeout=200)
            el.scroll_into_view_if_needed(timeout=200)
            el.click(force=True, timeout=200)
            return True
        except: pass
    if text:
        try:
            for tag in ["label", "span", "button", "a", "div"]:
                el = context.locator(f"{tag}:text-is('{text}')").first
                try:
                    el.wait_for(state="attached", timeout=100)
                    el.scroll_into_view_if_needed(timeout=100)
                    el.click(force=True, timeout=100)
                    return True
                except: pass
        except: pass
    return False

def robust_click(page, selectors, text=None):
    if attempt_click(page, selectors, text): return True
    try:
        if page.locator("iframe").count() > 0:
            for i in range(page.locator("iframe").count()):
                floc = page.frame_locator(f"iframe >> nth={i}")
                if attempt_click(floc, selectors, text): return True
    except: pass
    return False

def do_purchase(page, numbers):
    logger.info(f"[PURCHASE] {numbers} 구매 시작...")
    dialog_msgs = []
    def handle_dialog(dialog):
        logger.warning(f"사이트 알림: {dialog.message}")
        dialog_msgs.append(dialog.message)
        dialog.accept()
    page.on("dialog", handle_dialog)
    try:
        page.goto("https://el.dhlottery.co.kr/game/TotalGame.jsp?LottoId=LO40", wait_until="networkidle", timeout=30000)
        time.sleep(3)
        page.evaluate("""() => {
            document.querySelectorAll('input[value="닫기"], .close, .popup-close, #close').forEach(el=>el.click());
        }""")
        time.sleep(1)
        for num in numbers:
            padded = f"{num:02d}"
            selectors = [f"label[for='check645num{padded}']", f"label[for='chk{padded}']"]
            if not robust_click(page, selectors, text=str(num)):
                page.evaluate(f"""() => {{
                    let els = document.querySelectorAll('label, span');
                    for(let e of els) {{ if(e.innerText.trim() === '{num}') {{ e.click(); break; }} }}
                }}""")
            time.sleep(0.15)
        
        robust_click(page, ["#btnSelectNum", "a:text-is('확인')"], text="확인")
        time.sleep(1)
        if any("부족" in msg for msg in dialog_msgs): return False, f"잔액 부족: {dialog_msgs[-1]}"
        
        robust_click(page, ["#btnBuy", "a:text-is('구매하기')"], text="구매하기")
        time.sleep(1)
        robust_click(page, ["#popupLayerConfirm input[value='확인']", "a:text-is('확인')"], text="확인")
        time.sleep(2)
        robust_click(page, [".btn_popup_buy_confirm input[value='확인']", "a:text-is('확인')"], text="확인")
        return True, "✅ 구매가 완료되었습니다!"
    except Exception as e:
        logger.error(f"구매 실패: {e}")
        return False, f"오류: {str(e)[:50]}"

def automate_purchase(user_id, user_pw, numbers):
    try:
        with sync_playwright() as p:
            is_headless = bool(os.environ.get('RENDER') or os.environ.get('DOCKER_ENV'))
            browser = p.chromium.launch(headless=is_headless, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            context = browser.new_context(viewport={"width": 1366, "height": 768}, user_agent=UA)
            page = context.new_page()
            if HAS_STEALTH: Stealth().apply_stealth_sync(page)
            try:
                if do_login(page, user_id, user_pw): return do_purchase(page, numbers)
                return False, "로그인 실패"
            finally:
                browser.close()
    except Exception as e:
        return False, f"시스템 오류: {e}"

@app.route('/')
def index(): return send_from_directory(BASE_DIR, 'lotto_ai.html')
@app.route('/health')
def health(): return jsonify({"status": "ok", "port": os.environ.get('PORT')})
@app.route('/buy', methods=['POST'])
def buy_endpoint():
    data = request.json
    success, msg = automate_purchase(data.get('id'), data.get('pw'), data.get('numbers'))
    return jsonify({"success": success, "message": msg})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
