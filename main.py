import json
import os
import gspread
import pandas as pd
from playwright.sync_api import sync_playwright

FAMS_USER = os.getenv("FAMS_USER")
FAMS_PASS = os.getenv("FAMS_PASS")
GOOGLE_CREDS = os.getenv("GOOGLE_CREDENTIALS_JSON")
# Company Code shown on the login screen ("VMART"). Not sensitive on its own,
# but can be overridden via a FAMS_COMPANY_CODE secret/env var if needed.
FAMS_COMPANY_CODE = os.getenv("FAMS_COMPANY_CODE", "VMART")

DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def debug_capture(page, label):
    """Save a screenshot + HTML snapshot so failures are diagnosable from CI artifacts."""
    try:
        print(f"[debug] {label} -> url={page.url} title={page.title()!r}")
        page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"debug_{label}.png"), full_page=True)
        with open(os.path.join(DOWNLOAD_DIR, f"debug_{label}.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        print(f"[debug] capture failed for {label}: {e}")


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

        print(f"   -> FAMS_USER length={len(FAMS_USER or '')}, FAMS_PASS length={len(FAMS_PASS or '')}")
        if not FAMS_USER or not FAMS_PASS:
            raise RuntimeError("FAMS_USER or FAMS_PASS secret is empty/unset. Check repo secrets.")

        # The login form has THREE fields: Company Code, User Name, Password.
        # A generic input[type='text'] selector matches Company Code first (wrong field),
        # so we target each field by its label text instead.
        company_input = page.locator("xpath=//label[contains(., 'Company Code')]/following::input[1]")
        company_input.wait_for(state="visible", timeout=20000)
        company_input.fill(FAMS_COMPANY_CODE)

        user_input = page.locator("xpath=//label[contains(., 'User Name')]/following::input[1]")
        user_input.wait_for(state="visible", timeout=20000)
        user_input.fill(FAMS_USER)

        pass_input = page.locator("xpath=//label[contains(., 'Password')]/following::input[1]")
        pass_input.wait_for(state="visible", timeout=20000)
        pass_input.fill(FAMS_PASS)

        debug_capture(page, "before_submit_click")

        # There are TWO buttons: "SSO Login" and "Login". A generic
        # button[type='submit'] selector can match SSO Login first since it
        # appears earlier in the DOM. Target the "Login" button by its exact
        # accessible name to avoid triggering the SSO flow by mistake.
        login_button = page.get_by_role("button", name="Login", exact=True)
        if login_button.count() == 0:
            # Fallback in case it's rendered as <input type="submit" value="Login">
            login_button = page.locator("input[type='submit'][value='Login' i]")
        login_button.first.wait_for(state="visible", timeout=20000)

        url_before_click = page.url
        login_button.first.click()
        try:
            page.wait_for_url(lambda url: url != url_before_click, timeout=8000)
            print("   -> URL changed after submit click, navigation happened.")
        except Exception:
            print("   -> URL did NOT change after submit click within 8s (button may not have submitted the form).")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        debug_capture(page, "after_login")

        # Bail early with useful evidence if login didn't actually succeed
        if "login" in page.url.lower():
            debug_capture(page, "login_still_on_login_page")
            raise RuntimeError(
                f"Still on login page after submit (url={page.url}). "
                "Check FAMS_USER/FAMS_PASS secrets, or whether the site is blocking this IP."
            )

        # Step 1: Navigate to Asset Enquiry
        print("1. Navigating to Utilities -> Asset Enquiry...")
        page.goto("https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport", wait_until="networkidle")
        page.wait_for_timeout(3000)
        debug_capture(page, "after_navigate_asset_enquiry")

        # Step 2: Select Branches dropdown (#664 onwards)
        print("2. Selecting initial Branches dropdown (#664 and above)...")
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

            const checkboxes = document.querySelectorAll("input[type='checkbox']");
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
        debug_capture(page, "after_step2_branches")

        # Step 3: Click 'Export Excel' (located next to Export CSV)
        print("3. Clicking 'Export Excel' (next to Export CSV)...")
        clicked = page.evaluate("""() => {
            const elements = Array.from(document.querySelectorAll("input, button, a, img, span"));
            const excelBtn = elements.find(e => {
                const txt = (e.value || e.innerText || e.title || e.alt || '').toLowerCase();
                return txt.includes('excel') || (txt.includes('export') && !txt.includes('csv'));
            });
            if (excelBtn) {
                excelBtn.click();
                return true;
            }
            return false;
        }""")
        print(f"   -> Export Excel button found and clicked: {clicked}")
        page.wait_for_timeout(5000)
        debug_capture(page, "after_step3_export_excel_click")

        # Step 3b: Check if Export Excel opened a NEW TAB instead of a modal.
        # If so, switch `page` to that new tab so subsequent steps operate on the right place.
        if len(context.pages) > 1:
            print("   -> New tab/page detected after Export Excel click, switching context to it.")
            page = context.pages[-1]
            page.wait_for_load_state("domcontentloaded")
            debug_capture(page, "after_step3_new_tab")

        # Step 4: Select stores under Branches dropdown (top right side) & wait for table data
        print("4. Selecting top-right Branches dropdown & waiting for table data...")
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

            const checkboxes = document.querySelectorAll("input[type='checkbox']");
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
        page.wait_for_timeout(5000)
        debug_capture(page, "after_step4_top_right_branches")

        # Also check every iframe on the page in case the report renders inside one
        # (wait_for_selector on `page` only ever looks at the MAIN frame by default)
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                el_count = frame.evaluate("document.querySelectorAll('select, table').length")
                print(f"   -> iframe {frame.url!r} has {el_count} select/table elements")
            except Exception as e:
                print(f"   -> could not inspect iframe {frame.url!r}: {e}")

        # Step 5: Click Export below the popped-out data
        print("5. Clicking Export button below popped-out data...")
        file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")

        downloaded = False
        try:
            with page.expect_download(timeout=25000) as download_info:
                page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll("input, button, a, img, span"));
                    const exportBtn = btns.reverse().find(b => {
                        const val = (b.value || b.innerText || b.title || b.alt || '').toLowerCase();
                        return val === 'export' || val.includes('export');
                    });
                    if (exportBtn) exportBtn.click();
                }""")
            download = download_info.value
            download.save_as(file_path)
            downloaded = True
            print("Successfully downloaded exported report file!")
        except Exception as e:
            print(f"Download trigger note: {e}")
            debug_capture(page, "step5_download_failed")

        if not downloaded:
            print("Capturing rendered HTML table directly as fallback...")
            html_path = os.path.join(DOWNLOAD_DIR, "asset_report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            file_path = html_path

        return file_path

    except Exception:
        # Always leave evidence behind before re-raising
        debug_capture(page, "fatal_error")
        raise
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
        tables = pd.read_html(file_path)
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
