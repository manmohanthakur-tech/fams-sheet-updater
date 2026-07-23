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
    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1366, "height": 768}
    )
    page = context.new_page()

    try:
        # Step 0: Login
        print("0. Logging into FAMS...")
        page.goto("https://fams.vmart.co.in/WebfamsLive/Account/Login", wait_until="domcontentloaded")

        user_input = page.wait_for_selector("input[name='Username'], input#Username, input[type='text']", timeout=20000)
        user_input.fill(FAMS_USER)

        pass_input = page.wait_for_selector("input[name='Password'], input#Password, input[type='password']", timeout=20000)
        pass_input.fill(FAMS_PASS)

        page.click("button[type='submit'], input[type='submit'], #btnLogin")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # Step 1: Utilities -> Asset Enquiry
        print("1. Navigating to Utilities -> Asset Enquiry...")
        page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Step 2: Select Branches (#664 onwards)
        print("2. Selecting Branches (#664 and above)...")
        try:
            branch_dropdown = page.query_selector(".multiselect, select[name*='Branch'], select[name*='Store'], #ddlBranch, .dropdown-toggle")
            if branch_dropdown:
                branch_dropdown.click()
                page.wait_for_timeout(1000)

            options = page.query_selector_all("option, .multiselect-container input[type='checkbox'], li label")
            for opt in options:
                txt = opt.inner_text() if hasattr(opt, 'inner_text') else opt.get_attribute("value") or ""
                nums = [int(s) for s in txt.replace('-', ' ').replace('_', ' ').split() if s.isdigit()]
                if nums and nums[0] >= 664:
                    if opt.tag_name == "option":
                        opt.click()
                    elif opt.tag_name == "input" and not opt.is_checked():
                        opt.check()
        except Exception as e:
            print(f"Branch selection note: {e}")

        page.wait_for_timeout(2000)

        # Step 3 & 4: Click 'Show' and wait for table to populate
        print("3 & 4. Clicking 'Show' and waiting for data table...")
        show_btn = page.query_selector("input[value*='Show'], input[value*='Search'], button:has-text('Show'), #btnShow, #btnSearch")
        if show_btn:
            show_btn.click(force=True)
        else:
            page.keyboard.press("Enter")

        try:
            page.wait_for_selector("table, .dataTables_wrapper, .grid, tbody tr", timeout=25000)
            print("Data table populated on screen!")
        except Exception as e:
            print(f"Table render wait note: {e}")

        page.wait_for_timeout(5000)

        # Step 5: Click Export below the popped-out data
        print("5. Clicking Export below the popped-out data table...")
        file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")

        export_selectors = [
            "input[value*='Export']",
            "button:has-text('Export')",
            "a:has-text('Export')",
            ".dataTables_wrapper .buttons-excel",
            ".dataTables_wrapper .buttons-csv",
            "#btnExport",
            "img[title*='Export']"
        ]

        downloaded = False
        for selector in export_selectors:
            btn = page.query_selector(selector)
            if btn:
                try:
                    with page.expect_download(timeout=15000) as download_info:
                        btn.click(force=True)
                    download = download_info.value
                    download.save_as(file_path)
                    downloaded = True
                    print(f"Successfully exported report using selector: {selector}")
                    break
                except Exception as e:
                    print(f"Export click attempt note ({selector}): {e}")

        if not downloaded:
            print("Direct file download timed out. Capturing rendered HTML table directly...")
            html_path = os.path.join(DOWNLOAD_DIR, "asset_report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            file_path = html_path

        return file_path

    finally:
        browser.close()
        p.stop()

def update_google_sheet(file_path):
    print("6. Updating Google Sheet...")
    creds_dict = json.loads(GOOGLE_CREDS)
    gc = gspread.service_account_from_dict(creds_dict)

    spreadsheet_id = "18QGSZZa-H5PucmrScf0B2gJ8VMfe6vhVE7wg_FkZtPY"
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.worksheet("FAR Data")

    if file_path.endswith(".xlsx") or file_path.endswith(".xls"):
        df = pd.read_excel(file_path)
    else:
        tables = pd.read_html(file_path, flavor='lxml')
        df = max(tables, key=lambda t: t.shape[0] * t.shape[1])

    # Filter for Store >= 664
    store_col = [col for col in df.columns if 'store' in str(col).lower() or 'site' in str(col).lower() or 'branch' in str(col).lower() or 'location' in str(col).lower()]
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
