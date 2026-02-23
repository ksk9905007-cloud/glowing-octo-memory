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
if os.environ.get('RENDER'):
    # Render 환경에서는 프로젝트 내 .cache 폴더를 사용하도록 강제 지정
    pw_path = os.path.join(BASE_DIR, '.cache', 'ms-playwright')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = pw_path
    logger.info(f"Render 환경 감지. 브라우저 경로 설정: {pw_path}")

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
        logger.info(f"[LOGIN] {user_id} 로그인 시도 중...")
        page.goto("https://www.dhlottery.co.kr/login", wait_until="networkidle", timeout=30000)
        page.wait_for_selector("#inpUserId", timeout=15000)
        page.fill("#inpUserId", user_id)
        page.fill("#inpUserPswdEncn", user_pw)
        time.sleep(1)
        page.click("#btnLogin")
        
        # 로그인 결과 대기 (URL 변화 또는 로그아웃 버튼 감지)
        for _ in range(15):
            if is_logged_in(page):
                logger.info("[LOGIN] 로그인 성공!")
                return True
            time.sleep(1)
        logger.warning("[LOGIN] 로그인 확인 실패 (타임아웃)")
        return False
    except Exception as e:
        logger.error(f"[LOGIN] 오류 발생: {e}")
        return False

def attempt_click(context, selectors, text=None):
    for sel in selectors:
        try:
            el = context.locator(sel).first
            if el.is_visible(timeout=500):
                el.scroll_into_view_if_needed(timeout=500)
                el.click(force=True, timeout=1000)
                return True
        except: pass
    if text:
        try:
            # 텍스트가 정확히 일치하는 요소 탐색
            for tag in ["label", "span", "button", "a"]:
                el = context.locator(f"{tag}:text-is('{text}')").first
                if el.is_visible(timeout=500):
                    el.click(force=True, timeout=1000)
                    return True
        except: pass
    return False

def robust_click(page, selectors, text=None):
    # 1. 메인 페이지에서 먼저 시도
    if attempt_click(page, selectors, text): return True
    
    # 2. 로또 6/45 전용 Iframe(ifrm_lotto645) 우선 탐색
    try:
        game_frame = None
        for frame in page.frames:
            if "ifrm_lotto645" in frame.name:
                game_frame = frame
                break
        
        if game_frame and attempt_click(game_frame, selectors, text):
            return True
    except: pass
    
    # 3. 모든 프레임 탐색
    try:
        for frame in page.frames:
            if attempt_click(frame, selectors, text): return True
    except: pass
    return False

def do_purchase(page, numbers):
    logger.info(f"[PURCHASE] {numbers} 구매 엔진 가동...")
    dialog_msgs = []
    def handle_dialog(dialog):
        logger.warning(f"[DIALOG] {dialog.message}")
        dialog_msgs.append(dialog.message)
        dialog.accept()
    page.on("dialog", handle_dialog)

    try:
        # 1. 구매 페이지 이동
        logger.info("[PURCHASE] 6/45 구매 페이지 이동...")
        page.goto("https://el.dhlottery.co.kr/game/TotalGame.jsp?LottoId=LO40", wait_until="networkidle", timeout=40000)
        
        # 2. 게임 Iframe 대기
        logger.info("[PURCHASE] 게임 프레임(ifrm_lotto645) 대기 중...")
        page.wait_for_selector("#ifrm_lotto645", timeout=20000)
        time.sleep(3) # 추가 안정화 시간

        # 3. 팝업 제거 (Iframe 내부 팝업 포함)
        logger.info("[PURCHASE] 팝업 및 방해 요소 제거 중...")
        page.evaluate("""() => {
            document.querySelectorAll('input[value="닫기"], .close, .popup-close, #close').forEach(el=>el.click());
        }""")
        
        # Iframe 내부에서도 팝업 제거 시도
        try:
            game_frame = page.frame(name="ifrm_lotto645")
            if game_frame:
                game_frame.evaluate("""() => {
                    document.querySelectorAll('input[value="닫기"], .close, .popup-close, #close').forEach(el=>el.click());
                }""")
        except: pass
        time.sleep(1)

        # 4. 번호 선택
        logger.info(f"[PURCHASE] {numbers} 번호 선택 시작...")
        for num in numbers:
            padded = f"{num:02d}"
            # 볼 선택용 다양한 셀렉터
            selectors = [
                f"label[for='check645num{padded}']",
                f"label[for='check645num{num}']",
                f"#num{padded}",
                f"span:text-is('{num}')"
            ]
            if not robust_click(page, selectors):
                logger.warning(f"[PURCHASE] {num}번 클릭 실패, JS 우회 시도...")
                try:
                    game_frame = page.frame(name="ifrm_lotto645")
                    if game_frame:
                        game_frame.evaluate(f"""(n) => {{
                            let target = document.querySelector(`label[for='check645num${{n.padStart(2, '0')}}']`) || 
                                         document.querySelector(`label[for='check645num${{parseInt(n)}}']`);
                            if(target) target.click();
                            else {{
                                let els = document.querySelectorAll('label, span');
                                for(let e of els) {{ if(e.innerText.trim() == n) {{ e.click(); break; }} }}
                            }}
                        }}""", str(num))
                except: pass
            time.sleep(0.15)

        # 5. 확인/선택완료 클릭
        logger.info("[PURCHASE] 선택 완료('확인') 버튼 클릭...")
        if not robust_click(page, ["#btnSelectNum", "a:text-is('확인')", "button:text-is('확인')", "span:text-is('확인')"]):
            logger.error("[PURCHASE] '확인' 버튼을 찾지 못했습니다.")

        time.sleep(1.5)
        if any("부족" in msg for msg in dialog_msgs):
            logger.error(f"[PURCHASE] 구매 중단: 예치금 부족")
            return False, f"예치금 부족: {dialog_msgs[-1] if dialog_msgs else '알 수 없음'}"
        
        # 6. 구매하기 클릭
        logger.info("[PURCHASE] 최종 '구매하기' 버튼 클릭...")
        if not robust_click(page, ["#btnBuy", "a:text-is('구매하기')", "button:text-is('구매하기')"]):
            logger.error("[PURCHASE] '구매하기' 버튼 클릭 실패")
        
        time.sleep(1.5)

        # 7. 최종 컨펌 팝업 ("구매하시겠습니까?")
        logger.info("[PURCHASE] 최종 확인 팝업 승인 중...")
        robust_click(page, ["#popupLayerConfirm input[value='확인']", "a:text-is('확인')", "button:text-is('확인')"])
        
        time.sleep(2)
        
        # 8. 마지막 안내 팝업 처리
        robust_click(page, [".btn_popup_buy_confirm input[value='확인']", "a:text-is('확인')", "button:text-is('확인')"])
        
        return True, "✅ 구매 성공! 계정의 구매내역을 확인해 주세요."

    except Exception as e:
        logger.error(f"[PURCHASE] 심각한 오류: {e}")
        return False, f"구매 중 중단됨: {str(e)[:50]}"

def automate_purchase(user_id, user_pw, numbers):
    try:
        with sync_playwright() as p:
            # Render나 Docker 환경이 아니면 일반 화면 모드(Headless=False)로 실행
            is_headless = bool(os.environ.get('RENDER') or os.environ.get('DOCKER_ENV'))
            logger.info(f"[CORE] 브라우저 실행 모드: Headless={is_headless}")
            browser = p.chromium.launch(headless=is_headless, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            context = browser.new_context(viewport={"width": 1366, "height": 768}, user_agent=UA)
            page = context.new_page()
            
            if HAS_STEALTH: Stealth().apply_stealth_sync(page)

            try:
                if do_login(page, user_id, user_pw):
                    return do_purchase(page, numbers)
                return False, "❌ 로그인에 실패했습니다. 아이디/비번을 확인해 주세요."
            finally:
                browser.close()
    except Exception as e:
        logger.error(f"[CORE] 전체 프로세스 실패: {str(e)}")
        return False, f"시스템 오류: {str(e)}"

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
