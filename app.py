import time
import logging
import os
import sys
import json
import re
import urllib.request
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ── Render/Docker 환경 브라우저 경로 근본 해결 ──────────────────
def _setup_browser_env():
    # 1. 환경변수가 이미 설정되어 있고 존재한다면 최우선 사용
    env_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if env_path and os.path.exists(env_path):
        return env_path
        
    # 2. 알려진 모든 경로 리스트 순차 탐색
    possible_paths = [
        "/opt/render/.cache/ms-playwright",   # Render Native (Cache)
        os.path.expanduser("~/.cache/ms-playwright"), # Linux 기본 캐시
        "/ms-playwright",                   # Docker 이미지 기본
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = path
            return path
    
    # 3. Render 환경이면 일단 Native 기본 경로로 강제 지정 (빌드 단계에서 생성됨)
    if os.environ.get('RENDER'):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = "/opt/render/.cache/ms-playwright"
    return None

_setup_browser_env()
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
    "Chrome/133.0.0.0 Safari/537.36"
)

# ── 실시간 화면 중계용 ──────────────────────────────────────────
latest_screenshot = None

# ── 프록시/타이밍 설정 ──────────────────────────────────────────
PROXY_SERVER = os.environ.get('PROXY_SERVER') # 예: http://ip:port
PROXY_USER = os.environ.get('PROXY_USER')
PROXY_PASS = os.environ.get('PROXY_PASS')
DEFAULT_TIMEOUT = 60000 # 60초 (60,000ms)

def _get_proxy_config():
    if not PROXY_SERVER:
        return None
    config = {"server": PROXY_SERVER}
    if PROXY_USER and PROXY_PASS:
        config["username"] = PROXY_USER
        config["password"] = PROXY_PASS
    return config

# ══════════════════════════════════════════════════════════════
#  이력 관리
# ══════════════════════════════════════════════════════════════
def load_history(user_id=None):
    """구매 이력 로드 + 30일 초과 자동 삭제 (user_id 기준 필터)"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
        cutoff = datetime.now() - timedelta(days=30)
        filtered = [h for h in history if datetime.fromisoformat(h['timestamp']) > cutoff]
        if len(filtered) != len(history):
            save_history(filtered)
        # 사용자 ID로 필터링
        if user_id:
            filtered = [h for h in filtered if h.get('user_id') == user_id]
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

def add_history(numbers, round_no, round_date, user_id=None):
    # 전체 이력 로드 (필터 없이)
    history = load_history()
    entry = {
        'timestamp': datetime.now().isoformat(),
        'numbers': numbers,
        'round': round_no or '---',
        'round_date': round_date or datetime.now().strftime('%Y-%m-%d'),
        'user_id': user_id or 'unknown',
    }
    history.insert(0, entry)
    save_history(history[:500])
    return entry

# ══════════════════════════════════════════════════════════════
#  Playwright 헬퍼
# ══════════════════════════════════════════════════════════════
def _get_playwright_module():
    """Playwright를 lazy import (서버 시작 속도에 영향 주지 않도록)"""
    from playwright.sync_api import sync_playwright
    return sync_playwright

def _capture_screenshot(page):
    """현재 브라우저 화면 캡처 및 전역 변수 업데이트"""
    global latest_screenshot
    try:
        # 가벼운 JPEG 포맷으로 캡처 (용량 최적화)
        latest_screenshot = page.screenshot(type="jpeg", quality=60)
    except Exception as e:
        logger.debug(f"[SCREEN] 캡처 실패: {e}")

def is_logged_in(page):
    try:
        content = page.content()
        return "로그아웃" in content or "btn_logout" in content or "myPage" in content
    except:
        return False

def do_login(page, user_id, user_pw):
    logger.info(f"[LOGIN] '{user_id}' 로그인 시도...")
    try:
        logger.info("[LOGIN] 메인 홈페이지 먼저 접속 후 대기...")
        page.goto("https://www.dhlottery.co.kr/", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        time.sleep(3)

        logger.info("[LOGIN] 로그인 페이지로 이동...")
        # 리퍼러(이전 페이지 기록)를 조작하여 정상적인 링크 탑승으로 완전 위장
        page.goto("https://www.dhlottery.co.kr/login", 
                  referer="https://www.dhlottery.co.kr/", 
                  wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        time.sleep(2)

        # 변경된 아이디 입력창 (#inpUserId)
        page.wait_for_selector("#inpUserId", timeout=30000)
        page.wait_for_timeout(500)
        page.locator("#inpUserId").click()
        page.fill("#inpUserId", "")
        page.type("#inpUserId", user_id, delay=150)
        
        # 변경된 비밀번호 입력창 (#inpUserPswdEncn)
        page.locator("#inpUserPswdEncn").click()
        page.fill("#inpUserPswdEncn", "")
        page.type("#inpUserPswdEncn", user_pw, delay=200)
        page.wait_for_timeout(500)
        _capture_screenshot(page)

        # 로그인 버튼 (#btnLogin)
        login_btn = page.locator("#btnLogin")
        login_btn.hover()
        page.wait_for_timeout(300)
        login_btn.click()
        time.sleep(3)

        # 1. 일반적인 로그인 성공 확인 + 간소화 페이지 대응
        for i in range(15):
            content = page.content()
            # 간소화 페이지 운영 중 메시지 감지
            if "간소화" in content and "운영" in content:
                logger.info("[LOGIN] ⚠️ 간소화 페이지 감지! '동행복권통합포탈이동' 버튼 클릭 시도...")
                # '동행복권통합포탈이동' 버튼 클릭 시도
                try:
                    btns = [
                        "a:text-is('동행복권통합포탈이동')",
                        "button:text-is('동행복권통합포탈이동')",
                        "a:has-text('통합포탈')",
                        "button:has-text('통합포탈')",
                        "a:text-is('동행복권포탈이동')", # 기존 대비용
                    ]
                    clicked = False
                    for b in btns:
                        if page.locator(b).first.is_visible(timeout=2000):
                            page.locator(b).first.click()
                            logger.info(f"[LOGIN] '{b}' 버튼 클릭 성공")
                            clicked = True
                            break
                    if not clicked:
                        # 버튼을 못 찾으면 직접 메인으로 재접속
                        page.goto("https://www.dhlottery.co.kr/common.do?method=main", timeout=30000)
                except:
                    pass
                time.sleep(3)
            
            if is_logged_in(page):
                logger.info("[LOGIN] ✅ 로그인 성공!")
                return True
            
            # 페이지에 '로그인 실패' 혹은 '로그인 정보' 등 키워드가 있는지 확인
            current_text = page.content()
            if "로그인 정보가 맞지 않습니다" in current_text or "아이디 또는 비밀번호" in current_text:
                logger.warning("[LOGIN] ❌ 아이디/비밀번호 불일치 메시지 감지")
                return False
            
            time.sleep(1)

        # 2. 로또 6/45 전용 직접 확인 (간소화 페이지 우회용)
        try:
            page.goto("https://ol.dhlottery.co.kr/olotto/game/game645.do", timeout=10000)
            if "로그아웃" in page.content() or "게임" in page.content():
                logger.info("[LOGIN] ✅ 로또 전용 페이지를 통해 로그인 성공 확인!")
                return True
        except: pass

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

def _click_in_frame(page, selector, frame_names=["ifrm_lotto645", "ifrm_tab"]):
    """지정된 프레임 내에서 클릭, 실패하면 전체 프레임 및 메인에서 시도"""
    # 1. 우선순위 프레임들 탐색
    for frame_name in frame_names:
        try:
            frame = page.frame(name=frame_name)
            if frame:
                el = frame.locator(selector).first
                if el.is_visible(timeout=1000):
                    el.click(force=True, timeout=2000)
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

def _prepare_lotto_board(page):
    """로또 마킹판 준비 (탭 활성화 및 초기화) - 한 번만 호출"""
    logger.info("[PURCHASE] 마킹판 초기화 및 탭 활성화...")
    try:
        for fname in ["ifrm_tab", "ifrm_lotto645"]:
            frame = page.frame(name=fname)
            if frame:
                frame.evaluate("""() => {
                    try {
                        if (typeof selectWayTab === 'function') selectWayTab(0);
                        if (typeof resetNumber645 === 'function') resetNumber645();
                        else if (typeof resetAllNum === 'function') resetAllNum();
                        
                        // 시각적 리셋 (ID 기반)
                        const btnReset = document.getElementById('resetAllNum') || document.getElementById('btnReset');
                        if (btnReset) btnReset.click();
                    } catch(e) {}
                }""")
                time.sleep(1)
                return True
    except: pass
    return False

def _mark_single_number(page, num):
    """개별 번호 마킹 (초기화 없이 단순 마킹)"""
    try:
        for fname in ["ifrm_tab", "ifrm_lotto645"]:
            frame = page.frame(name=fname)
            if frame:
                success = frame.evaluate(f"""(n) => {{
                    try {{
                        const pad = String(n).padStart(2,'0');
                        const id = 'check645num' + n;
                        const id_padded = 'check645num' + pad;
                        const cb = document.getElementById(id) || document.getElementById(id_padded);
                        
                        // 이미 체크되어 있다면 건너뜀 (중복 클릭 방지)
                        if (cb && cb.checked) return true;

                        // 사이트 내장 함수 호출
                        if (typeof check645 === 'function') {{
                            check645(n);
                            return true;
                        }} else {{
                            // 직접 클릭 (레이블 우선)
                            const label = document.querySelector(`label[for="${{id}}"]`) || 
                                           document.querySelector(`label[for="${{id_padded}}"]`);
                            if (label) {{ label.click(); return true; }}
                            if (cb) {{ cb.click(); return true; }}
                        }}
                    }} catch(e) {{}}
                    return false;
                }}""", num)
                if success: return True
    except: pass
    return False

def _mark_numbers_batch(page, numbers):
    # 이 함수는 이제 순차 마킹 로직으로 통합 운영됩니다.
    _prepare_lotto_board(page)
    count = 0
    for n in numbers:
        if _mark_single_number(page, n):
            count += 1
        time.sleep(0.8)
    return count >= 6

def _click_number(page, num):
    # 개별 마킹용 폴백
    return _mark_single_number(page, num)

def get_round_info(page):
    """현재 회차 정보 수집"""
    round_no, round_date = "---", datetime.now().strftime("%Y-%m-%d")
    try:
        page.goto("https://www.dhlottery.co.kr/common.do?method=main",
                  wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
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
            wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT
        )
        _capture_screenshot(page)

        # ─────────────────────────────────────────
        # ─────────────────────────────────────────
        # 2. iframe 로딩 대기 (다중 프레임 지원)
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] 게임 프레임 로딩 대기...")
        try:
            # ifrm_lotto645 또는 ifrm_tab 둘 중 하나가 나타날 때까지 대기
            page.wait_for_function("""() => 
                document.getElementById('ifrm_lotto645') !== null || 
                document.getElementById('ifrm_tab') !== null ||
                document.getElementsByName('ifrm_lotto645').length > 0 ||
                document.getElementsByName('ifrm_tab').length > 0
            """, timeout=30000)
            logger.info("[PURCHASE] 게임 프레임 식별 성공")
        except:
            logger.warning("[PURCHASE] 프레임 로딩 대기 시간 초과, 계속 진행 시도...")
        
        time.sleep(3)

        # ─────────────────────────────────────────
        # 3. 팝업 닫기 및 '혼합선택' 탭 클릭 (번호 입력을 위해 필수)
        # ─────────────────────────────────────────
        for close_sel in [
            "input[value='닫기']", ".close_btn", ".btn_close",
            "a:text-is('닫기')", "button:text-is('닫기')"
        ]:
            try:
                _click_in_frame(page, close_sel)
            except:
                pass
        time.sleep(1)

        # ─────────────────────────────────────────
        # 3. 마킹 준비 (탭 활성화 및 초기화)
        # ─────────────────────────────────────────
        logger.info("[PURCHASE] 번호 선택판 준비 중...")
        # 이 과정은 _mark_numbers_batch 내부에서 JS로 더 안정적으로 처리하도록 이양했습니다.
        time.sleep(1)

        # ─────────────────────────────────────────
        # ─────────────────────────────────────────
        # 4. 번호 선택 (순차적으로 정밀 마킹)
        # ─────────────────────────────────────────
        logger.info(f"[PURCHASE] {numbers} 번호를 하나씩 순차적으로 마킹합니다...")
        
        # 4-1. 마킹판 준비 (탭 활성화 및 초기화) - 한 번만 수행
        _prepare_lotto_board(page)
        
        selected_count = 0
        for num in numbers:
            # 개별 순차 마킹 (중복 클릭 및 초기화 루프 방지)
            ok = _mark_single_number(page, num)
            if ok:
                selected_count += 1
                logger.info(f"[PURCHASE] {num}번 마킹 완료 ✅ ({selected_count}/6)")
            else:
                logger.warning(f"[PURCHASE] {num}번 마킹 실패 ⚠️")
            
            # 사람이 직접 누르는 느낌과 인식률을 위해 0.8초 간격 유지
            time.sleep(0.8)
            if num == numbers[-1]: _capture_screenshot(page) # 마지막 번호 선택 후 캡처

        if selected_count < 6:
            logger.warning(f"[PURCHASE] 번호 선택이 완벽하지 않음 ({selected_count}/6)")

        time.sleep(1.0)

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
            logger.warning("[PURCHASE] ❌ '확인' 버튼 못 찾음")
            return False, "번호 선택 '확인' 버튼을 클릭하지 못했습니다.", round_no, round_date

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
            logger.warning("[PURCHASE] ❌ '구매하기' 버튼 못 찾음")
            return False, "'구매하기' 버튼을 클릭하지 못했습니다.", round_no, round_date

        time.sleep(2)
        
        # 구매 후 나타난 모든 경고/에러 다이얼로그(잔액부족, 구매한도, 구매불가 시간 등) 다시 한 번 확인
        for m in dialog_msgs:
            if any(err in m for err in ["부족", "초과", "오류", "마감", "로그인", "실패"]):
                return False, f"구매 실패: {m}", round_no, round_date

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
                    _capture_screenshot(page) # 최종 완료 직전 캡처
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
                proxy=_get_proxy_config(),
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--disable-gpu",
                    "--no-zygote",
                    "--disable-software-rasterizer"
                ]
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=UA,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                ignore_https_errors=True
            )
            
            # 고급 스텔스 설정
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            """)
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
    return jsonify({
        "status": "ok", 
        "env": "render" if os.environ.get('RENDER') else "local",
        "python": sys.version[:10],
        "playwright": "available"
    }), 200

@app.route('/diagnostic')
def diagnostic():
    """브라우저 실행 가능 여부 정밀 진단"""
    logger.info("[DIAG] 브라우저 진단 시작...")
    sync_playwright = _get_playwright_module()
    try:
        with sync_playwright() as p:
            # 브라우저 실행 시 경로 이슈를 방지하기 위해 args와 함께 명시적 실행
            browser = p.chromium.launch(
                headless=True, 
                proxy=_get_proxy_config(),
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            # 동행복권 사이트 직접 접속 테스트
            page.goto("https://www.dhlottery.co.kr/", timeout=DEFAULT_TIMEOUT)
            title = page.title()
            browser.close()
            return jsonify({"success": True, "title": title, "msg": "브라우저 엔진이 정상 작동합니다."})
    except Exception as e:
        logger.error(f"[DIAG] 실패: {e}")
        return jsonify({"success": False, "msg": str(e)}), 500

@app.route('/screenshot')
def get_screenshot():
    """현재 캡처된 화면 이미지 전송"""
    from flask import Response
    if latest_screenshot:
        return Response(latest_screenshot, mimetype='image/jpeg')
    return "No screenshot", 404

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
        entry = add_history(numbers, round_no, round_date, user_id=uid)

    return jsonify({
        "success": success,
        "message": msg,
        "round": round_no,
        "round_date": round_date,
        "entry": entry,
    })

@app.route('/history', methods=['GET'])
def get_history():
    # ?user_id=xxx 쿼리로 사용자별 필터 가능
    user_id = request.args.get('user_id', None)
    return jsonify({"history": load_history(user_id=user_id)})

@app.route('/history', methods=['DELETE'])
def del_history():
    user_id = request.args.get('user_id', None)
    if user_id:
        # 해당 유저 이력만 삭제
        all_history = load_history()
        remaining = [h for h in all_history if h.get('user_id') != user_id]
        save_history(remaining)
    else:
        save_history([])
    return jsonify({"success": True})

@app.route('/lotto-result')
def lotto_result():
    """동행복권 API에서 최신 당첨번호 조회"""
    try:
        # 1. 최신 회차 번호 파악 (메인에서 파싱)
        headers = {'User-Agent': UA}
        req = urllib.request.Request('https://www.dhlottery.co.kr/common.do?method=main', headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8')
        m = re.search(r'제\s*(\d{3,4})\s*회', html)
        if not m:
            m = re.search(r'drwNo=(\d{3,4})', html)
        latest_round = int(m.group(1)) if m else 1162

        # 2. 해당 회차 당첨 정보 API 호출
        api_url = f'https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={latest_round}'
        req2 = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            result = json.loads(resp2.read().decode('utf-8'))

        if result.get('returnValue') != 'success':
            return jsonify({'success': False, 'msg': '당첨번호 데이터 없음'}), 404

        return jsonify({
            'success': True,
            'round': result.get('drwNo'),
            'date': result.get('drwNoDate'),
            'numbers': [
                result.get('drwtNo1'), result.get('drwtNo2'),
                result.get('drwtNo3'), result.get('drwtNo4'),
                result.get('drwtNo5'), result.get('drwtNo6'),
            ],
            'bonus': result.get('bnusNo'),
            'first_winners': result.get('firstPrzwnerCo', 0),
            'first_prize': result.get('firstWinamnt', 0),
        })
    except Exception as e:
        logger.error(f"[RESULT] 당첨번호 조회 실패: {e}")
        return jsonify({'success': False, 'msg': str(e)}), 500

# ══════════════════════════════════════════════════════════════
#  개발 서버 실행
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Flask 개발 서버 시작: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
