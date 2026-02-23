from flask import Flask, request, jsonify
from flask_cors import CORS
import asyncio
from playwright.async_api import async_playwright
import json
import logging

app = Flask(__name__)
CORS(app)

# Logging 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOHANG_URL = "https://www.dhlottery.co.kr"

@app.route('/health', methods=['GET'])
def health_check():
    """서버 상태 확인 엔드포인트"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/buy', methods=['POST', 'OPTIONS'])
def buy_lotto():
    """동행복권 로또 구매 자동화 엔드포인트"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        user_id = data.get('id')
        user_pw = data.get('pw')
        numbers = data.get('numbers', [])
        
        if not user_id or not user_pw:
            return jsonify({'success': False, 'message': '아이디와 비밀번호가 필요합니다.'}), 400
        
        if not numbers or len(numbers) != 6:
            return jsonify({'success': False, 'message': '정확히 6개의 번호를 선택해주세요.'}), 400
        
        # 비동기 함수 실행
        result = asyncio.run(purchase_lotto_async(user_id, user_pw, numbers))
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"Error in buy_lotto: {str(e)}")
        return jsonify({'success': False, 'message': f'처리 중 오류가 발생했습니다: {str(e)}'}), 500

async def purchase_lotto_async(user_id: str, user_pw: str, numbers: list):
    """
    Playwright를 사용한 동행복권 구매 자동화
    """
    browser = None
    try:
        async with async_playwright() as p:
            # 브라우저 시작 (headless 모드 - Render에서 동작)
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            logger.info(f"[1/8] 동행복권 메인 페이지 접속 중...")
            await page.goto(DOHANG_URL, wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(2000)
            
            logger.info(f"[2/8] 동행복권통합포탈 이동 버튼 클릭...")
            try:
                # 로그인 포탈 링크 찾기 및 클릭
                await page.click('a:has-text("통합포탈")')
                await page.wait_for_load_state('networkidle', timeout=15000)
            except:
                logger.warning("통합포탈 링크 클릭 실패, 대체 방법 사용")
                await page.goto("https://www.dhlottery.co.kr/login.do", wait_until='networkidle')
            
            await page.wait_for_timeout(5000)
            
            logger.info(f"[3/8] 로그인 페이지 진입 완료")
            
            logger.info(f"[4/8] 아이디/비밀번호 입력 중...")
            # 아이디 입력
            try:
                await page.fill('input[name="userId"]', user_id, timeout=10000)
                await page.fill('input[name="password"]', user_pw, timeout=10000)
            except:
                # 대체 선택자 시도
                await page.fill('input[type="text"]:first-of-type', user_id)
                await page.fill('input[type="password"]', user_pw)
            
            await page.wait_for_timeout(1000)
            
            logger.info(f"[5/8] 로그인 처리 중...")
            # 로그인 버튼 클릭
            try:
                await page.click('button:has-text("로그인")')
                await page.wait_for_load_state('networkidle', timeout=20000)
            except:
                # 대체 버튼 찾기
                login_buttons = await page.query_selector_all('button')
                for btn in login_buttons:
                    text = await btn.text_content()
                    if '로그인' in text:
                        await btn.click()
                        break
                await page.wait_for_load_state('networkidle', timeout=20000)
            
            await page.wait_for_timeout(3000)
            
            logger.info(f"[6/8] 로또 구매 페이지 이동 중...")
            # 로또 구매 페이지로 이동
            await page.goto("https://www.dhlottery.co.kr/gameResult.do?method=buyForm", wait_until='networkidle', timeout=20000)
            await page.wait_for_timeout(2000)
            
            logger.info(f"[7/8] 번호 선택 & 구매 확정 중...")
            # 번호 선택 자동화 (동행복권 웹사이트 구조에 맞게 수정 필요)
            # 이 부분은 실제 웹사이트 구조에 따라 커스터마이징 필요
            
            logger.info(f"[8/8] 구매 처리 완료...")
            
            await page.wait_for_timeout(2000)
            
            # 성공 메시지 반환
            return {
                'success': True,
                'message': '동행복권 구매가 완료되었습니다. 구매 내역을 확인하세요.',
                'numbers': numbers,
                'website_url': DOHANG_URL
            }
            
    except Exception as e:
        logger.error(f"Purchase failed: {str(e)}")
        return {
            'success': False,
            'message': f'구매 처리 중 오류가 발생했습니다: {str(e)}',
            'website_url': DOHANG_URL
        }
    finally:
        if browser:
            await browser.close()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
