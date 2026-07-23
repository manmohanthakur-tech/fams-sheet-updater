import json
import os
import gspread
import pandas as pd
from playwright.sync_api import sync_playwright

FAMS_USER = os.getenv("FAMS_USER")
FAMS_PASS = os.getenv("FAMS_PASS")
GOOGLE_CREDS = os.getenv("GOOGLE_CREDENTIALS_JSON")

DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def download_fams_report():
    print("Starting browser automation...")
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        # 1. Login to FAMS Portal
        print("Navigating to FAMS login page...")
        page.goto("https://fams.vmart.co.in/WebfamsLive/Account/Login", wait_until="domcontentloaded")

        user_input = page.wait_for_selector("input[name='Username'], input#Username, input[type='text']", timeout=20000)
        user_input.fill(FAMS_USER)

        pass_input = page.wait_for_selector("input[name='Password'], input#Password, input[type='password']", timeout=20000)
        pass_input.fill(FAMS_PASS)

        page.click("button[type='submit'], input[type='submit'], #btnLogin")
        page.wait_for_load_state("networkidle")

        # 2. Go to Asset Enquiry Report
        print("Navigating to Asset Enquiry Report...")
        page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # 3. Trigger Report Search
        print("Triggering Show/Search action...")
        show_btn = page.query_selector("input[value*='Show'], input[value*='Search'], button[type='submit'], #btnShow, #btnSearch")
        if show_btn:
            show_btn.click(force=True)
            print("Clicked search button!")
        else:
            page.keyboard.press("Enter")

        # Wait up to 15 seconds for a table element to appear in DOM
        try:
            page.wait_for_selector("table, .table, grid", timeout=15000)
            print("Table element detected on screen!")
        except Exception:
            print("Warning: Table selector wait timed out. Saving screenshot for debug...")
            page.screenshot(path=os.path.join(DOWNLOAD_DIR, "debug_page.png"))

        # 4. Save Page HTML
        html_path = os.path.join(DOWNLOAD_DIR, "asset_report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())

        return html_path

    finally:
        browser.close()
        p.stop()

def update_google_sheet(file_path):
    print("Updating Google Sheet...")
    creds_dict = json.loads(GOOGLE_CREDS)
    gc = gspread.service_account_from_dict(creds_dict)

    spreadsheet_id = "18QGSZZa-H5PucmrScf0B2gJ8VMfe6vhVE7wg_FkZtPY"
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.worksheet("FAR Data")

    tables = pd.read_html(file_path, flavor='lxml')
    if not tables:
        raise ValueError("No table found on page. FAMS portal requires specific search dropdown inputs.")
    
    # Pick the largest data table
    df = max(tables, key=lambda t: t.shape[0] * t.shape[1])

    # Store Filter logic (#664 onwards)
    store_col = [col for col in df.columns if 'store' in str(col).lower() or 'site' in str(col).lower() or 'location' in str(col).lower()]
    if store_col:
        col_name = store_col[0]
        def filter_store(val):
            str_val = str(val)
            nums = [int(s) for s in str_val.replace('-', ' ').split() if s.isdigit()]
            if nums:
                return nums[0] >= 664
            return True
        initial_rows = len(df)
        df = df[df[col_name].apply(filter_store)]
        print(f"Filtered rows: {initial_rows} -> {len(df)} rows.")

    df = df.fillna("")

    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())
    print("Google Sheet updated successfully!")

if __name__ == "__main__":
    file_path = download_fams_report()
    update_google_sheet(file_path)
