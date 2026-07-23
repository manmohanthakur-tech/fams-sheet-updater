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
        page.wait_for_timeout(3000)

        # 2. Navigate via Menu: Utilities -> Asset Enquiry
        print("Navigating via Menu: Utilities -> Asset Enquiry...")
        try:
            # Click Utilities menu
            utilities_menu = page.query_selector("a:has-text('Utilities'), :text('Utilities'), #menu_utilities")
            if utilities_menu:
                utilities_menu.click()
                page.wait_for_timeout(1000)
            
            # Click Asset Enquiry sub-menu
            asset_enquiry = page.query_selector("a:has-text('Asset Enquiry'), :text('Asset Enquiry')")
            if asset_enquiry:
                asset_enquiry.click()
            else:
                page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="networkidle")
        except Exception as e:
            print(f"Menu navigation note: {e}. Navigating directly...")
            page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="networkidle")

        page.wait_for_timeout(3000)

        # 3. Select Store Filter (#664 onwards if dropdown/input exists)
        print("Applying store selection filters...")
        try:
            # Look for store dropdown or range inputs
            store_select = page.query_selector("select[name*='Store'], select[id*='Store'], select[name*='site']")
            if store_select:
                options = store_select.query_selector_all("option")
                for opt in options:
                    txt = opt.inner_text()
                    # Select options that contain 664 or numbers >= 664
                    nums = [int(s) for s in txt.replace('-', ' ').split() if s.isdigit()]
                    if nums and nums[0] >= 664:
                        opt.click()
        except Exception as e:
            print(f"Store filter UI selection note: {e}")

        # 4. Export Excel or Click Show
        print("Triggering Export Excel / Search...")
        file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")
        
        excel_exported = False
        try:
            # Try direct Excel Export button first
            excel_btn = page.query_selector("a:has-text('Export'), button:has-text('Export'), input[value*='Export'], img[title*='Excel'], .fa-file-excel")
            if excel_btn:
                with page.expect_download(timeout=15000) as download_info:
                    excel_btn.click(force=True)
                download = download_info.value
                download.save_as(file_path)
                excel_exported = True
                print("Excel file downloaded directly from portal!")
        except Exception as e:
            print(f"Direct export trigger note: {e}")

        if not excel_exported:
            # Fallback: Click Show/Search and parse HTML table
            show_btn = page.query_selector("input[value*='Show'], input[value*='Search'], button[type='submit'], #btnShow, #btnSearch")
            if show_btn:
                show_btn.click(force=True)
            else:
                page.keyboard.press("Enter")
            
            page.wait_for_timeout(8000)
            
            html_path = os.path.join(DOWNLOAD_DIR, "asset_report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
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

    if file_path.endswith(".xlsx") or file_path.endswith(".xls"):
        df = pd.read_excel(file_path)
    else:
        tables = pd.read_html(file_path, flavor='lxml')
        if not tables:
            raise ValueError("No table found on page. Check FAMS portal filters.")
        df = max(tables, key=lambda t: t.shape[0] * t.shape[1])

    # Ensure store filter (Store #664 and above)
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
        print(f"Filtered rows (Store >= 664): {initial_rows} -> {len(df)} rows.")

    df = df.fillna("")

    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())
    print("Google Sheet updated successfully!")

if __name__ == "__main__":
    file_path = download_fams_report()
    update_google_sheet(file_path)
