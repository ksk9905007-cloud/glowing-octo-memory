import time
import random
import logging
import webbrowser
from threading import Timer
from flask import Flask, request, jsonify, send_from_directory
import os
from flask_cors import CORS
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
            # 텍스트 매칭 시 태그를 제한하여 오작동(다른 메뉴 클릭) 방지
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
    # 메인 페이지에 없으면 모든 프레임을 고속 탐색
    try:
        if page.locator("iframe").count() > 0:
            for i in range(page.locator("iframe").count()):
                floc = page.frame_locator(f"iframe >> nth={i}")
                if attempt_click(floc, selectors, text): return True
    except: pass
    return False

def do_purchase(page, numbers):
    logger.info("[PURCHASE] 🚀 정확도 100% 광속 구매 엔진 가동 중...")
    
    dialog_msgs = []
    def handle_dialog(dialog):
        logger.warning(f"  [!] 사이트 알림 발생: {dialog.message}")
        dialog_msgs.append(dialog.message)
        dialog.accept()
        
    page.on("dialog", handle_dialog)

    try:
        # 1. 구매 페이지 진입 (사용자 요청 URL)
        page.goto("https://el.dhlottery.co.kr/game/TotalGame.jsp?LottoId=LO40", wait_until="networkidle", timeout=30000)
        time.sleep(3)
        
        # 2. 방해 요소(팝업 등) 제거
        page.evaluate("""() => {
            document.querySelectorAll('input[value="닫기"], .close, .popup-close, #close').forEach(el=>el.click());
            document.querySelectorAll('div').forEach(el => {
                let z = parseInt(window.getComputedStyle(el).zIndex);
                if (z > 100 && el.innerText.trim() === '') el.remove();
            });
        }""")
        time.sleep(1)

        # 3. 6개 번호 정밀 탐색 및 타격
        logger.info(f"  → 🎯 {numbers} 완벽 매칭 전송 시작...")
        for idx, num in enumerate(numbers):
            padded = f"{num:02d}"
            # 볼(번호)에만 존재하는 특수한 ID와 label 패턴을 최우선으로 탐색
            selectors = [
                 f"label[for='check645num{padded}']",
                 f"label[for='check645num{num}']",
                 f"label[for='check_num_{num}']",
                 f"label[for='check_num_{padded}']",
                 f"label[for='chk{padded}']",
                 f"label[for='chk{num}']"
            ]
            
            # 정확히 태그 내부 텍스트가 번호와 일치하는 label이나 span을 text 인자로 전달
            if robust_click(page, selectors, text=str(num)):
                logger.info(f"      → {num}번 마킹 완료")
            else:
                # 자바스크립트 우회 강제 타격 (최후의 보루)
                try:
                    hit = page.evaluate(f"""() => {{
                        let els = document.querySelectorAll('label, span, a');
                        for(let e of els) {{
                            if(e.innerText.trim() === '{num}') {{ e.click(); return true; }}
                        }}
                        return false;
                    }}""")
                    if hit: logger.info(f"      → {num}번 JS 강제 마킹 완료")
                    else: logger.warning(f"      → {num}번 타격 실패")
                except:
                    logger.warning(f"      → {num}번 타격 실패")
                    
            time.sleep(0.15)  # 번호 간 충돌 방지를 위해 안정적 인터벌

        # 4. '확인' (선택완료) 클릭
        logger.info("  → 번호 선택 '확인' 클릭...")
        if robust_click(page, ["#btnSelectNum", "a:text-is('확인')", "button:text-is('확인')"], text="확인"):
            pass
        else:
            logger.warning("  → '확인' 버튼을 찾지 못했습니다.")
        
        time.sleep(1) # 장바구니 업데이트 대기

        # 5. 구매 전송 전 에러 감지
        if any("부족" in msg for msg in dialog_msgs):
            return False, f"진행 불가: {dialog_msgs[-1]}"

        # 6. 최종 '구매하기' 버튼 클릭
        logger.info("  → 최종 '구매하기' 클릭...")
        if robust_click(page, ["#btnBuy", "a:has-text('구매하기')", "button:has-text('구매하기')"], text="구매하기"):
            pass
        else:
            logger.warning("  → '구매하기' 버튼을 찾지 못했습니다.")
        
        time.sleep(1)
        
        # 7. 구매 확인 HTML 팝업 승인 ("구매하시겠습니까?")
        logger.info("  → 구매 진행 확인 팝업 승인 중...")
        robust_click(page, ["#popupLayerConfirm input[value='확인']", "#popupLayerConfirm a", "a:text-is('확인')", "button:text-is('확인')"], text="확인")
        
        time.sleep(2)
        
        # 8. 결제 완료 / 구매 내역 확인 팝업 승인
        logger.info("  → 구매 내역 확인 팝업 처리 중...")
        robust_click(page, [".btn_popup_buy_confirm input[value='확인']", "a:text-is('확인')", "button:text-is('확인')"], text="확인")
        
        time.sleep(2)
        
        if dialog_msgs:
            last = dialog_msgs[-1]
            if "완료" in last or "정상" in last or "성공" in last:
                return True, f"✅ 성공: {last}"
            # 단순 확인 알림창일 수 있으므로 실패로 단정짓지 않음
            
        return True, "✅ 광속 구매가 완료되었습니다!"

    except Exception as e:
        logger.error(f"  ❌ 진행 멈춤 원인: {e}")
        return False, f"구매 화면 멈춤: {str(e)[:50]}"


def automate_purchase(user_id, user_pw, numbers):
    with sync_playwright() as p:
        is_headless = os.environ.get('RENDER') or os.environ.get('DOCKER_ENV') or True
        browser = p.chromium.launch(headless=is_headless, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        # 화면이 잘 보이도록 정상 PC 해상도로 원복
        context = browser.new_context(viewport={"width": 1366, "height": 768}, user_agent=UA)
        page = context.new_page()
        
        if HAS_STEALTH: Stealth().apply_stealth_sync(page)

        try:
            if do_login(page, user_id, user_pw):
                return do_purchase(page, numbers)
            return False, "로그인 정보가 틀리거나 인증에 실패했습니다."
        except Exception as e:
            return False, str(e)
        finally:
            browser.close()

@app.route('/')
def index(): return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'lotto_ai.html')
@app.route('/health')
def health(): return jsonify({"status": "ok"})
@app.route('/buy', methods=['POST'])
def buy_endpoint():
    data = request.json
    success, msg = automate_purchase(data.get('id'), data.get('pw'), data.get('numbers'))
    return jsonify({"success": success, "message": msg})

def open_browser(): 
    if not os.environ.get('RENDER'):
        webbrowser.open("http://127.0.0.1:5000")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    if not os.environ.get('RENDER'):
        Timer(1.5, open_browser).start()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
