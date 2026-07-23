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
        page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # 3. Click Show/Search via evaluate (bypasses element visibility issues)
        print("Triggering Show/Search button...")
        page.evaluate("""() => {
            const btn = document.querySelector("input[value='Show'], button:has-text('Show'), input[value='Search'], #btnSearch, #btnShow, .btn, input[type='submit']");
            if (btn) btn.click();
        }""")
        page.wait_for_timeout(8000)

        # 4. Attempt Export or Capture DOM Table
        print("Attempting Export / Data Extraction...")
        file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")

        try:
            with page.expect_download(timeout=10000) as download_info:
                page.evaluate("""() => {
                    const expBtn = document.querySelector("img[title*='Excel'], img[src*='excel'], .fa-file-excel, [onclick*='Export'], #btnExport, #btnExcel");
                    if (expBtn) expBtn.click();
                }""")
            download = download_info.value
            download.save_as(file_path)
            print("Downloaded file successfully!")
        except Exception:
            print("Direct export download skipped. Reading page tables directly...")
            content = page.content()
            html_path = os.path.join(DOWNLOAD_DIR, "asset_report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(content)
            file_path = html_path

        return file_path

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

    if file_path.endswith(".html"):
        tables = pd.read_html(file_path, flavor='lxml')
        if not tables:
            raise ValueError("No table found on page. Check portal credentials/navigation.")
        # Filter out tiny layout tables, pick the data table
        df = max(tables, key=lambda t: t.shape[0] * t.shape[1])
    else:
        try:
            df = pd.read_excel(file_path)
        except Exception:
            df = pd.read_html(file_path)[0]

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
