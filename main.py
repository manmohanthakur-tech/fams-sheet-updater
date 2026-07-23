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

        # Step 1: Navigates to Utilities -> Asset Enquiry
        print("1. Navigating to Utilities -> Asset Enquiry...")
        page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Step 2: Select Branches dropdown & check stores #664 and above
        print("2. Selecting Branches dropdown (stores #664 and above)...")
        page.evaluate("""() => {
            const selects = document.querySelectorAll("select");
            selects.forEach(s => {
                Array.from(s.options).forEach(opt => {
                    const txt = opt.text || opt.value || '';
                    const nums = txt.replace(/[-_]/g, ' ').split(' ').map(v => parseInt(v)).filter(v => !isNaN(v));
                    if (nums.length > 0 && nums[0] >= 664) {
                        opt.selected = true;
                    }
                });
                s.dispatchEvent(new Event('change', { bubbles: true }));
            });

            const checkboxes = document.querySelectorAll(".multiselect-container input[type='checkbox'], input[type='checkbox']");
            checkboxes.forEach(cb => {
                const label = cb.closest('label') || cb.parentElement;
                const txt = label ? label.innerText : cb.value || '';
                const nums = txt.replace(/[-_]/g, ' ').split(' ').map(v => parseInt(v)).filter(v => !isNaN(v));
                if (nums.length > 0 && nums[0] >= 664) {
                    if (!cb.checked) {
                        cb.click();
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            });
        }""")
        page.wait_for_timeout(2000)

        # Step 3: Click 'Export Excel' (located next to Export CSV)
        print("3. Clicking 'Export Excel' button (next to Export CSV)...")
        page.evaluate("""() => {
            const elements = Array.from(document.querySelectorAll("input, button, a, img, span"));
            const excelBtn = elements.find(e => {
                const txt = (e.value || e.innerText || e.title || e.alt || '').toLowerCase();
                return txt.includes('excel') || (txt.includes('export') && !txt.includes('csv'));
            });
            if (excelBtn) excelBtn.click();
        }""")
        page.wait_for_timeout(4000)

        # Step 4: Select stores under Branches dropdown (top right side inside pop-up/window)
        # & wait for table data (Asset Code, Physical ID...) to populate
        print("4. Selecting top-right Branches dropdown in pop-up & waiting for table data...")
        page.evaluate("""() => {
            // Target top-right dropdown / pop-up elements
            const popSelects = document.querySelectorAll(".modal select, .pop-up select, .ui-dialog select, select");
            popSelects.forEach(s => {
                Array.from(s.options).forEach(opt => {
                    const txt = opt.text || opt.value || '';
                    const nums = txt.replace(/[-_]/g, ' ').split(' ').map(v => parseInt(v)).filter(v => !isNaN(v));
                    if (nums.length > 0 && nums[0] >= 664) {
                        opt.selected = true;
                    }
                });
                s.dispatchEvent(new Event('change', { bubbles: true }));
            });

            const popCheckboxes = document.querySelectorAll(".modal input[type='checkbox'], .pop-up input[type='checkbox'], .multiselect-container input[type='checkbox']");
            popCheckboxes.forEach(cb => {
                const label = cb.closest('label') || cb.parentElement;
                const txt = label ? label.innerText : cb.value || '';
                const nums = txt.replace(/[-_]/g, ' ').split(' ').map(v => parseInt(v)).filter(v => !isNaN(v));
                if (nums.length > 0 && nums[0] >= 664) {
                    if (!cb.checked) {
                        cb.click();
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            });
        }""")

        try:
            # Wait for table headers (Asset Code, Physical ID, etc.) to populate
            page.wait_for_selector("table th:has-text('Asset'), table th:has-text('Code'), table th:has-text('Physical'), tbody tr", timeout=30000)
            print("Pop-up table populated on screen!")
        except Exception as e:
            print(f"Table render wait note: {e}")

        page.wait_for_timeout(3000)

        # Step 5: Click the Export button below the popped-out data
        print("5. Clicking the Export button below the popped-out data...")
        file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")

        downloaded = False
        try:
            with page.expect_download(timeout=25000) as download_info:
                page.evaluate("""() => {
                    // Look specifically for Export button in/below modal or table container
                    const btns = Array.from(document.querySelectorAll("input, button, a, img, span"));
                    const exportBtn = btns.reverse().find(b => {
                        const val = (b.value || b.innerText || b.title || b.alt || '').toLowerCase();
                        return val.includes('export');
                    });
                    if (exportBtn) exportBtn.click();
                }""")
            download = download_info.value
            download.save_as(file_path)
            downloaded = True
            print("Successfully downloaded exported report!")
        except Exception as e:
            print(f"Download trigger note: {e}")

        if not downloaded:
            print("Capturing rendered popped-out HTML table as fallback...")
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
        if not tables:
            raise ValueError("No table found in exported data.")
        df = max(tables, key=lambda t: t.shape[0] * t.shape[1])

    # Filter Store >= 664
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
