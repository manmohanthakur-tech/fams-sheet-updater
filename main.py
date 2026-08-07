import json
import os
import zipfile
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

        # Step 1: Navigate to Asset Enquiry via the actual menu (a hardcoded
        # direct URL was tried before and 404'd).
        #
        # A prior JS-based approach (finding any element containing "Asset
        # Enquiry" text and calling .click() on it) was unreliable: when the
        # menu is <li><a>Asset Enquiry</a></li>, the <li> and <a> can have
        # identical text, and clicking the <li> instead of the <a> fires no
        # navigation at all - with no error, since .click() "succeeds" either
        # way. That caused silent no-op failures in some runs.
        #
        # Instead: open the "Utilities" menu with a real click, click the
        # actual link, and then VERIFY navigation actually happened by
        # waiting for a marker that only exists on the real Asset Enquiry
        # page (the "Export CSV" / "Export Excel" radio options) rather than
        # trusting that the click "worked".
        print("1. Navigating to Utilities -> Asset Enquiry...")
        debug_capture(page, "index_before_navigate")

        def open_utilities_and_click_asset_enquiry():
            utilities_menu = page.get_by_text("Utilities", exact=True).first
            utilities_menu.wait_for(state="visible", timeout=15000)
            utilities_menu.click()
            page.wait_for_timeout(500)

            asset_enquiry_link = page.locator("a").filter(has_text="Asset Enquiry")
            # Exclude "Asset Ageing Enquiry" / anything longer that also matches
            asset_enquiry_link = asset_enquiry_link.get_by_text("Asset Enquiry", exact=True)
            if asset_enquiry_link.count() == 0:
                # Fallback: some menus render the item as non-<a> too
                asset_enquiry_link = page.get_by_text("Asset Enquiry", exact=True)
            asset_enquiry_link.first.wait_for(state="visible", timeout=15000)
            asset_enquiry_link.first.click()

        navigated = False
        for attempt in (1, 2):
            try:
                open_utilities_and_click_asset_enquiry()
            except Exception as e:
                print(f"   -> attempt {attempt}: could not click Asset Enquiry menu item ({e})")
                debug_capture(page, f"menu_click_failed_attempt{attempt}")
                continue

            page.wait_for_timeout(3000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            # Verify navigation actually happened: look for the Export
            # CSV/Excel radios that only exist on the real Asset Enquiry page.
            try:
                page.get_by_text("Export CSV", exact=True).first.wait_for(state="visible", timeout=8000)
                navigated = True
                print(f"   -> attempt {attempt}: navigation to Asset Enquiry confirmed (Export CSV visible).")
                break
            except Exception:
                print(f"   -> attempt {attempt}: click did not result in real navigation (still no Export CSV option).")
                debug_capture(page, f"navigation_not_confirmed_attempt{attempt}")

        debug_capture(page, "after_navigate_asset_enquiry")

        if not navigated:
            raise RuntimeError(
                "Clicked 'Asset Enquiry' but the page never actually navigated there "
                "(Export CSV/Export Excel options never appeared) after 2 attempts. "
                "Check debug_navigation_not_confirmed_attempt*.png to see what's on screen."
            )

        if "notfound" in page.url.lower() or "/error" in page.url.lower():
            raise RuntimeError(f"Navigation to Asset Enquiry ended up on an error page: {page.url}")

        # Step 2: Switch to "Export Excel" mode. This is a radio button, and
        # clicking it is what REVEALS the Branches-driven Search button, the
        # live data table, and the Export button - none of those exist while
        # "Export CSV" (the default) is selected. We already confirmed
        # "Export CSV" is visible above, so "Export Excel" (right next to it)
        # should be too - this is just a normal wait, not a recovery path.
        print("2. Selecting 'Export Excel' option...")
        debug_capture(page, "before_export_excel_wait")

        export_excel_option = page.get_by_text("Export Excel", exact=True).first
        export_excel_option.wait_for(state="visible", timeout=15000)
        export_excel_option.click()
        page.wait_for_timeout(2000)
        debug_capture(page, "after_select_export_excel_mode")

        # Confirms the Excel-mode UI (Search button etc.) actually rendered
        page.get_by_role("button", name="Search", exact=True).wait_for(state="visible", timeout=30000)

        # Step 3: Check every branch numbered >= 664 in the Branches multi-select.
        #
        # IMPORTANT on HOW we select: inspecting the page HTML revealed the
        # checkbox UI is just a visual layer over a real, hidden backing
        # element: <select id="_ddlbranch" multiple onchange=
        # "BranchListSelectedIndexChanged1(this,value)">. This is what the
        # site's application code actually listens to - NOT the individual
        # checkboxes. Three checkbox-based approaches were tried and all
        # failed for different reasons:
        #   1) .click() on all ~200 checkboxes in one synchronous burst ->
        #      loading overlay permanently stuck (never cleared, even after
        #      150s).
        #   2) Setting .checked directly + one synthetic 'change' event on a
        #      checkbox -> overlay cleared fine, but selection never
        #      actually registered (Search returned ~11 leftover rows).
        #   3) .click() on every checkbox, fully sequential with an explicit
        #      wait between each -> selection registered correctly, but the
        #      overlay STILL never cleared even once across 206 individual
        #      waits (24+ minutes total) - proving this isn't a
        #      timing/overlap problem, clicking checkboxes directly is just
        #      not the right interaction at all for this widget.
        #
        # Fix: skip the checkbox UI entirely. Set .selected = true directly
        # on the matching <option> elements inside the real backing <select>,
        # then dispatch exactly ONE native 'change' event on that <select> -
        # this is exactly what the site's own handler expects (the same
        # event it would see from a real multi-select interaction), fired
        # once, not 206 times.
        print("3. Selecting Branches >= 664...")
        debug_capture(page, "before_branches_select")

        selected_count = page.evaluate("""() => {
            const select = document.querySelector('#_ddlbranch')
                || document.querySelector('select[name="ddlBranches"]');
            if (!select) return -1;
            let count = 0;
            Array.from(select.options).forEach(opt => {
                const txt = opt.text || '';
                const match = txt.match(/#?(\\d+)/);
                if (match && parseInt(match[1], 10) >= 664) {
                    opt.selected = true;
                    count++;
                }
            });
            select.dispatchEvent(new Event('change', { bubbles: true }));
            return count;
        }""")

        if selected_count == -1:
            debug_capture(page, "branches_select_not_found")
            raise RuntimeError(
                "Could not find the backing <select id='_ddlbranch'> element. "
                "Open debug_branches_select_not_found.html to check the current markup."
            )

        # The site's onchange handler processes the bulk selection - give it
        # a moment, but this is one request now, not 206, so it shouldn't
        # need anywhere near as long as the old per-checkbox approach.
        LOADING_SELECTOR = "#loading-block .loading-indicator, .loading-indicator"
        print("   -> Waiting for the branch-selection request to finish...")
        try:
            page.wait_for_selector(LOADING_SELECTOR, state="hidden", timeout=30000)
            print("   -> Loading overlay cleared.")
        except Exception as e:
            print(f"   -> Loading overlay did not clear within 30s ({e}); continuing anyway.")

        print(f"   -> Branches matched and checked (>= 664): {selected_count}")
        debug_capture(page, "after_branches_select")

        if selected_count == 0:
            debug_capture(page, "no_branches_matched")
            raise RuntimeError(
                "No branches numbered >= 664 were found/checked. "
                "Open debug_after_branches_select.html from the artifacts to inspect the branch list markup."
            )

        # Close the dropdown so it doesn't overlap the Search/Export buttons
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        debug_capture(page, "before_search_click")

        # Step 4: Click Search and wait for the table to actually populate
        print("4. Clicking Search and waiting for table data...")
        page.get_by_role("button", name="Search", exact=True).click(timeout=60000)
        table_populated = False
        try:
            page.wait_for_function(
                """() => {
                    const table = document.querySelector('table');
                    if (!table) return false;
                    const bodyText = table.innerText || '';
                    return !bodyText.includes('No data available in table')
                        && table.querySelectorAll('tbody tr').length > 0;
                }""",
                timeout=60000,
            )
            table_populated = True
            print("   -> Table populated with data.")
        except Exception as e:
            print(f"   -> Table did not appear to populate within 60s: {e}")
        debug_capture(page, "after_search_table_loaded")

        if not table_populated:
            raise RuntimeError(
                "Search ran but the results table never populated with real data. "
                "Check debug_after_search_table_loaded.png / .html to see what's on screen - "
                "likely the branch selection didn't actually register with the site's search query."
            )

        # Step 5: Click Export and capture the download
        print("5. Clicking Export button...")
        file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")

        # The Export click navigates the page to a URL that generates the
        # file server-side (confirmed: last run's failure showed url=
        # '.../AssetEnquiry/Export', title='Loading .../Export' - the click
        # DID fire correctly). With 221 branches selected this is a large
        # export (likely tens of thousands of rows across ~100 columns), so
        # give the server plenty of time rather than assuming it's stuck.
        DOWNLOAD_TIMEOUT_MS = 8 * 60 * 1000  # 8 minutes
        downloaded = False
        try:
            with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
                # A stray "loading-indicator" element intercepts pointer
                # events at this point even though it's not actually
                # blocking real functionality (confirmed: it's still
                # visible in a screenshot taken well after the table
                # finished loading, and never clears no matter how long we
                # wait). Playwright's own .click() refuses to click through
                # it. Bypass hit-testing entirely with a direct JS click,
                # which fires the real handler regardless of what's
                # visually overlapping it - same fix that worked for the
                # branch <select> in step 3.
                clicked = page.evaluate("""() => {
                    const candidates = Array.from(
                        document.querySelectorAll("button, input[type='button'], input[type='submit'], a")
                    );
                    const exportBtn = candidates.find(el => {
                        const txt = (el.value || el.innerText || el.textContent || '').trim();
                        return txt === 'Export';
                    });
                    if (exportBtn) {
                        exportBtn.click();
                        return true;
                    }
                    return false;
                }""")
                if not clicked:
                    raise RuntimeError("Export button not found in the DOM at click time.")
            download = download_info.value
            download.save_as(file_path)
            downloaded = True
            print("Successfully downloaded exported report file!")
        except Exception as e:
            print(f"Download trigger note: {e}")
            debug_capture(page, "step5_download_failed")

        if not downloaded:
            # IMPORTANT: do NOT silently fall back to scraping the on-screen
            # HTML table. That fallback only ever captures whatever small
            # preview/leftover table happens to be visible (confirmed twice:
            # it produced ~11 rows with a different, wrong column set,
            # while still reporting "success" and overwriting the Google
            # Sheet with bad data). A failed real download means something
            # is genuinely wrong upstream - better to fail the run loudly
            # than silently corrupt the sheet with incomplete data.
            debug_capture(page, "download_failed_fatal")
            raise RuntimeError(
                "Real file download never triggered/completed. Refusing to fall back to "
                "scraping the on-screen HTML table, since that has previously produced "
                "incomplete/wrong data while still 'succeeding'. Check debug_download_failed_fatal.png "
                "and debug_after_search_table_loaded.png to see what was on screen."
            )

        # The site wraps large exports in a ZIP archive rather than sending
        # the .xlsx directly (confirmed: the downloaded file's real content
        # is a zip containing a single "Asset-Enquiry.xlsx" inside - despite
        # us naming the download "asset_report.xlsx", pandas correctly
        # detected the actual bytes as a zip and had no reader for it).
        # Detect this and extract the real spreadsheet before returning.
        if zipfile.is_zipfile(file_path):
            print("   -> Downloaded file is a ZIP archive; extracting the real spreadsheet...")
            with zipfile.ZipFile(file_path) as zf:
                inner_names = [
                    n for n in zf.namelist()
                    if n.lower().endswith((".xlsx", ".xls", ".csv")) and not n.endswith("/")
                ]
                if not inner_names:
                    raise RuntimeError(
                        f"Downloaded ZIP contained no .xlsx/.xls/.csv file. Contents: {zf.namelist()}"
                    )
                inner_name = inner_names[0]
                extracted_path = os.path.join(DOWNLOAD_DIR, os.path.basename(inner_name))
                with zf.open(inner_name) as src, open(extracted_path, "wb") as dst:
                    dst.write(src.read())
                print(f"   -> Extracted '{inner_name}' from ZIP -> {extracted_path}")
                file_path = extracted_path

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
