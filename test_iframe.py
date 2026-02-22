from playwright.sync_api import sync_playwright

def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto("https://ol.dhlottery.co.kr/olotto/game/game645.do", timeout=15000)
            page.wait_for_timeout(5000)
            
            print("--- FRAMES ---")
            for i, f in enumerate(page.frames):
                print(f"[{i}] URL: {f.url} | Name: {f.name}")
        except Exception as e:
            print("Error:", e)
        finally:
            browser.close()

if __name__ == "__main__":
    test()
