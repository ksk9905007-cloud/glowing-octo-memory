import time
import logging
import os
import sys
import json
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ── 로깅 설정 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ── 기본 경로 ──────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Flask 앱 (즉시 생성 → gunicorn 포트 감지용) ───────────────
app = Flask(__name__)
CORS(app)

# ── 구매 이력 파일 ─────────────────────────────────────────────
HISTORY_FILE = os.path.join(BASE_DIR, 'purchase_history.json')

# ── User-Agent ────────────────────────────────────────────────
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ══════════════════════════════════════════════════════════════
#  이력 관리
# ══════════════════════════════════════════════════════════════
def load_history():
    """구매 이력 로드 + 30일 초과 자동 삭제"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
        cutoff = datetime.now() - timedelta(days=30)
        filtered = [h for h in history if datetime.fromisoformat(h['timestamp']) > cutoff]
        if len(filtered) != len(history):
            save_history(filtered)
        return filtered
    except Exception as e:
        logger.error(f"[HISTORY] 로드 실패: {e}")
        return []

def save_history(history):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[HISTORY] 저장 실패: {e}")

def add_history(numbers, round_no, round_date):
    history = load_history()
    entry = {
        'timestamp': datetime.now().isoformat(),
        'numbers': numbers,
        'round': round_no or '---',
        'round_date': round_date or datetime.now().strftime('%Y-%m-%d'),
    }
    history.insert(0, entry)
    save_history(history[:200])
    return entry

# ══════════════════════════════════════════════════════════════
#  Playwright 헬퍼
# ══════════════════════════════════════════════════════════════
def _get_playwright_module():
    """Playwright를 lazy import (서버 시작 속도에 영향 주지 않도록)"""
    from playwright.sync_api import sync_playwright
    return sync_playwright

def is_logged_in(page):
    try:
        content = page.content()
        return "로그아웃" in content or "btn_logout" in content or "myPage" in content
    except:
        return False

def do_login(page, user_id, user_pw):
    logger.info(f"[LOGIN] '{user_id}' 로그인 시도...")
    try:
        page.goto("https://www.dhlottery.co.kr/user.do?method=login",
                  wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        # 아이디 입력
        page.wait_for_selector("#userId", timeout=10000)
        page.fill("#userId", "")
        page.fill("#userId", user_id)
        time.sleep(0.3)

        # 비밀번호 입력
        page.fill("#password", "")
        page.fill("#password", user_pw)
        time.sleep(0.5)

        # 로그인 버튼 클릭
        page.click(".btn_common.lrg.blu")
        time.sleep(3)

        # 로그인 성공 확인 (최대 15초 대기)
        for i in range(15):
            if is_logged_in(page):
                logger.info("[LOGIN] ✅ 로그인 성공!")
                return True
            time.sleep(1)

        # 실패 메시지 확인
        try:
            alert_msg = page.locator(".alert_msg, .login_fail, #popupLayer").first.inner_text(timeout=2000)
            logger.warning(f"[LOGIN] 실패 메시지: {alert_msg}")
        except:
            pass

        logger.warning("[LOGIN] ❌ 로그인 확인 실패 (15초 타임아웃)")
        return False
    except Exception as e:
        logger.error(f"[LOGIN] 오류: {e}")
        return False

def _click_in_frame(page, selector, frame_name="ifrm_lotto645"):
    """ifrm_lotto645 프레임 내에서 클릭, 실패하면 메인에서 시도"""
    # 1. 지정 프레임
    try:
        frame = page.frame(name=frame_name)
        if frame:
            el = frame.locator(selector).first
            if el.is_visible(timeout=2000):
                el.click(force=True, timeout=3000)
                return True
    except:
        pass
    # 2. 모든 프레임 순회
    try:
        for frame in page.frames:
            try:
                el = frame.locator(selector).first
                if el.is_visible(timeout=500):
                    el.click(force=True, timeout=1000)
                    return True
            except:
                pass
    except:
        pass
    # 3. 메인 페이지
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=500):
            el.click(force=True, timeout=1000)
            return True
    except:
        pass
    return False

def _click_number(page, num):
    """로또 번호 선택 (볼 클릭)"""
    padded = f"{num:02d}"
    selectors = [
        f"label[for='check645num{padded}']",
        f"label[for='check645num{num}']",
        f"#num{padded}",
    ]
    for sel in selectors:
        if _click_in_frame(page, sel):
            return True

    # JS 우회 (iframe 내부)
    try:
        frame = page.frame(name="ifrm_lotto645")
        if frame:
            frame.evaluate(f"""() => {{
                const pad = n => String(n).padStart(2,'0');
                const sel1 = `label[for='check645num${{pad({num})}}']`;
                const sel2 = `label[for='check645num{num}']`;
                const el = document.querySelector(sel1) || document.querySelector(sel2);
                if (el) {{ el.click(); return; }}
                document.querySelectorAll('label, span').forEach(e => {{
                    if (e.innerText.trim() === '{num}') e.click();
                }});
            }}""")
            return True
    except:
        pass
    return False

def get_round_info(page):
    """현재 회차 정보 수집"""
    round_no, round_date = "---", datetime.now().strftime("%Y-%m-%d")
    try:
        page.goto("https://www.dhlottery.co.kr/common.do?method=main",
                  wait_until="domcontentloaded", timeout=20000)
        time.sleep(1)
        content = page.content()

        # 회차 번호
        m = re.search(r'제\s*(\d{3,4})\s*회', content)
        if not m:
            m = re.search(r'(\d{3,4})회차', content)
        if m:
            round_no = m.group(1)

        # 추첨일
        m2 = re.search(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', content)
        if m2:
            round_date = f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
    except Exception as e:
        logger.warning(f"[ROUND] 조회 실패: {e}")
    logger.info(f"[ROUND] 회차: {round_no}, 추첨일: {round_date}")
    return round_no, round_date

def do_purchase(page, numbers):
    logger.info(f"[PURCHASE] 구매 번호: {numbers}")
    dialog_msgs = []

    def handle_dialog(dialog):
        logger.info(f"[DIALOG] '{dialog.message}' → 자동 확인")
        dialog_msgs.append(dialog.message)
        dialog.accept()

    page.on("dialog", handle_dialog)

    try:
        # ─────────────────────────────────────────
        # 0. 회차 정보 수집
        # ─────────────────────────────────────────
        round_no, round_date = get_round_info(page)

        # ─────────────────────────────────────────
        # 1. 구매 페이지 이동
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] 6/45 구매 페이지 이동...")
        page.goto(
            "https://el.dhlottery.co.kr/game/TotalGame.jsp?LottoId=LO40",
            wait_until="domcontentloaded", timeout=40000
        )

        # ─────────────────────────────────────────
        # 2. iframe 로딩 대기
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] iframe 로딩 대기...")
        page.wait_for_selector("#ifrm_lotto645", timeout=20000)
        time.sleep(3)

        # ─────────────────────────────────────────
        # 3. 팝업 닫기
        # ─────────────────────────────────────────
        for close_sel in [
            "input[value='닫기']", ".close_btn", ".btn_close",
            "a:text-is('닫기')", "button:text-is('닫기')"
        ]:
            try:
                frame = page.frame(name="ifrm_lotto645")
                if frame:
                    frame.locator(close_sel).first.click(timeout=500, force=True)
            except:
                pass
            try:
                page.locator(close_sel).first.click(timeout=500, force=True)
            except:
                pass
        time.sleep(1)

        # ─────────────────────────────────────────
        # 4. 번호 선택
        # ─────────────────────────────────────────
        logger.info(f"[PURCHASE] 번호 선택 시작: {numbers}")
        for num in numbers:
            ok = _click_number(page, num)
            logger.info(f"[PURCHASE] {num}번 선택 {'✅' if ok else '⚠️'}")
            time.sleep(0.2)

        time.sleep(0.5)

        # ─────────────────────────────────────────
        # 5. '확인' 버튼 (선택 완료)
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] '확인' 버튼 클릭...")
        ok = False
        for sel in ["#btnSelectNum", "input[value='확인']", "a.btn_common:text-is('확인')"]:
            if _click_in_frame(page, sel):
                logger.info(f"[PURCHASE] '확인' 버튼 클릭 성공 ({sel})")
                ok = True
                break
        if not ok:
            logger.warning("[PURCHASE] '확인' 버튼 못 찾음, 계속 진행...")

        time.sleep(2)

        # 예치금 부족 체크
        if any("부족" in m for m in dialog_msgs):
            return False, f"예치금 부족: {dialog_msgs[-1]}", round_no, round_date

        # ─────────────────────────────────────────
        # 6. '구매하기' 버튼
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] '구매하기' 버튼 클릭...")
        ok = False
        for sel in ["#btnBuy", "input[value='구매하기']", "a.btn_common:text-is('구매하기')", "button:text-is('구매하기')"]:
            if _click_in_frame(page, sel):
                logger.info(f"[PURCHASE] '구매하기' 버튼 클릭 성공 ({sel})")
                ok = True
                break
        if not ok:
            logger.warning("[PURCHASE] '구매하기' 버튼 못 찾음")

        time.sleep(2)

        # ─────────────────────────────────────────
        # 7. 확인 팝업 ("구매하시겠습니까?")
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] 구매확인 팝업 처리...")
        for sel in [
            "#popupLayerConfirm input[value='확인']",
            ".btn_confirm input[value='확인']",
            "input[value='확인']",
            "a:text-is('확인')", "button:text-is('확인')"
        ]:
            try:
                if _click_in_frame(page, sel):
                    logger.info(f"[PURCHASE] 확인 팝업 클릭 ({sel})")
                    break
            except:
                pass

        time.sleep(2)

        # ─────────────────────────────────────────
        # 8. 구매내역 확인 팝업
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] 구매내역 확인 팝업 처리...")
        for sel in [
            ".btn_popup_buy_confirm input[value='확인']",
            ".confirm input[value='확인']",
            "input[value='확인']",
            "a:text-is('확인')", "button:text-is('확인')"
        ]:
            try:
                if _click_in_frame(page, sel):
                    logger.info(f"[PURCHASE] 구매내역 팝업 클릭 ({sel})")
                    break
            except:
                pass

        time.sleep(1)

        logger.info("[PURCHASE] ✅ 구매 프로세스 완료!")
        return True, "✅ 구매 성공! 동행복권 마이페이지에서 구매내역을 확인하세요.", round_no, round_date

    except Exception as e:
        logger.error(f"[PURCHASE] 오류: {e}", exc_info=True)
        return False, f"구매 중 오류 발생: {str(e)[:80]}", None, None

def automate_purchase(user_id, user_pw, numbers):
    sync_playwright = _get_playwright_module()
    is_headless = bool(os.environ.get('RENDER') or os.environ.get('DOCKER_ENV'))
    logger.info(f"[CORE] Headless={is_headless}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=is_headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ]
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=UA,
                locale="ko-KR",
            )
            page = context.new_page()

            # Playwright Stealth (있으면 적용)
            try:
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(page)
            except:
                pass

            try:
                if not do_login(page, user_id, user_pw):
                    return False, "❌ 로그인 실패. 아이디/비밀번호를 확인하세요.", None, None
                return do_purchase(page, numbers)
            finally:
                try:
                    browser.close()
                except:
                    pass
    except Exception as e:
        logger.error(f"[CORE] 전체 실패: {e}", exc_info=True)
        return False, f"시스템 오류: {str(e)[:80]}", None, None

# ══════════════════════════════════════════════════════════════
#  Flask Routes
# ══════════════════════════════════════════════════════════════
@app.before_request
def log_req():
    logger.info(f"[REQ] {request.method} {request.path}")

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'lotto_ai.html')

@app.route('/health')
@app.route('/ping')
def health():
    return jsonify({"status": "ok", "env": "render" if os.environ.get('RENDER') else "local"}), 200

@app.route('/buy', methods=['POST'])
def buy():
    data = request.json or {}
    uid      = data.get('id', '').strip()
    upw      = data.get('pw', '').strip()
    numbers  = data.get('numbers', [])

    if not uid or not upw:
        return jsonify({"success": False, "message": "아이디/비밀번호가 없습니다."}), 400
    if not numbers or len(numbers) != 6:
        return jsonify({"success": False, "message": "번호 6개가 필요합니다."}), 400

    success, msg, round_no, round_date = automate_purchase(uid, upw, numbers)

    entry = None
    if success:
        entry = add_history(numbers, round_no, round_date)

    return jsonify({
        "success": success,
        "message": msg,
        "round": round_no,
        "round_date": round_date,
        "entry": entry,
    })

@app.route('/history', methods=['GET'])
def get_history():
    return jsonify({"history": load_history()})

@app.route('/history', methods=['DELETE'])
def del_history():
    save_history([])
    return jsonify({"success": True})

# ══════════════════════════════════════════════════════════════
#  개발 서버 실행
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Flask 개발 서버 시작: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
