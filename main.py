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
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_context().new_page()

    # 1. Login
    print("Navigating to FAMS login page...")
    page.goto(
        "https://fams.vmart.co.in/WebfamsLive/Account/Login?ReturnUrl=%2fWebfamsLive%2f%3fDashboard%3d1&Dashboard=1",
        wait_until="networkidle",
    )

    user_input = page.wait_for_selector(
        "input[name='Username'], input#Username, input[type='text']",
        timeout=15000,
    )
    user_input.fill(FAMS_USER)

    pass_input = page.wait_for_selector(
        "input[name='Password'], input#Password, input[type='password']",
        timeout=15000,
    )
    pass_input.fill(FAMS_PASS)

    page.click("button[type='submit'], input[type='submit'], #btnLogin")
    page.wait_for_load_state("networkidle")

    # 2. Navigate: Utilities -> Asset Enquiry
    print("Navigating to Utilities -> Asset Enquiry...")
    # Click on Utilities menu if it exists, or go directly
    try:
      page.click("text=Utilities", timeout=5000)
      page.click("text=Asset Enquiry", timeout=5000)
    except Exception:
      print("Direct navigation fallback to Asset Enquiry...")
      page.goto(
          "https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport",
          wait_until="networkidle",
      )

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)

    # 3. Select Stores starting from Store #664 onwards
    print("Applying Store filter (#664 onwards)...")
    try:
      # If there is a Store Dropdown / Multi-select
      store_dropdown = page.query_selector("select#StoreId, select[name*='Store'], #ddlStore")
      if store_dropdown:
        options = page.eval_on_selector_all("select#StoreId option, select[name*='Store'] option, #ddlStore option", "opts => opts.map(o => ({text: o.text, value: o.value}))")
        
        # Filter options that have store numbers >= 664
        selected_values = []
        for opt in options:
          # Extract numerical part from store name/code
          nums = [int(s) for s in opt['text'].split() if s.isdigit()]
          if nums and nums[0] >= 664:
            selected_values.append(opt['value'])
          elif "664" in opt['text']:
            selected_values.append(opt['value'])

        if selected_values:
          page.select_option("select#StoreId, select[name*='Store'], #ddlStore", value=selected_values)
          print(f"Selected {len(selected_values)} stores from #664 onwards.")
    except Exception as e:
      print(f"Store filter warning (proceeding with default selection): {e}")

    # 4. Click Show / Search if needed
    show_btn = page.query_selector(
        "button:has-text('Show'), input[value='Show'], button:has-text('Search'), input[value='Search'], #btnSearch, #btnShow"
    )
    if show_btn:
      print("Clicking Show/Search...")
      show_btn.click()
      page.wait_for_load_state("networkidle")
      page.wait_for_timeout(3000)

    # 5. Export Excel
    print("Clicking Export Excel...")
    with page.expect_download(timeout=60000) as download_info:
      page.click(
          "button:has-text('Export'), a:has-text('Export'),"
          " button:has-text('Excel'), a:has-text('Excel'), input[value='Export'],"
          " .fa-file-excel, .fa-download, #btnExport, #btnExcel"
      )

    download = download_info.value
    file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")
    download.save_as(file_path)
    print(f"Report downloaded successfully to {file_path}")
    browser.close()
    return file_path


def update_google_sheet(file_path):
  print("Updating Google Sheet...")
  creds_dict = json.loads(GOOGLE_CREDS)
  gc = gspread.service_account_from_dict(creds_dict)

  spreadsheet_id = "18QGSZZa-H5PucmrScf0B2gJ8VMfe6vhVE7wg_FkZtPY"
  sh = gc.open_by_key(spreadsheet_id)
  worksheet = sh.worksheet("FAR Data")

  # Read Excel or HTML export format
  try:
    df = pd.read_excel(file_path)
  except Exception:
    df = pd.read_html(file_path)[0]

  # Additional safety Python filter for Store #664 onwards (if column exists)
  store_col = [col for col in df.columns if "store" in str(col).lower() or "site" in str(col).lower()]
  if store_col:
    col_name = store_col[0]
    # Keep rows where store number is 664 or higher, or contains 664+
    def filter_store(val):
      str_val = str(val)
      nums = [int(s) for s in str_val.replace('-', ' ').split() if s.isdigit()]
      if nums:
        return nums[0] >= 664
      return True

    initial_rows = len(df)
    df = df[df[col_name].apply(filter_store)]
    print(f"Filtered rows in Python: {initial_rows} -> {len(df)} rows.")

  df = df.fillna("")

  worksheet.clear()
  worksheet.update([df.columns.values.tolist()] + df.values.tolist())
  print("Google Sheet updated successfully!")


if __name__ == "__main__":
  file_path = download_fams_report()
  update_google_sheet(file_path)
