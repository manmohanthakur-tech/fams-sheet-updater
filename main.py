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

    # 1. Login to FAMS Portal
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

    # 2. Navigate to Asset Enquiry Report
    print("Navigating to Asset Enquiry Report...")
    page.goto(
        "https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport",
        wait_until="networkidle",
    )
    page.wait_for_timeout(4000)

    # 3. Click Show / Search button first to generate the report view
    print("Clicking Search / Show button...")
    try:
      show_btn = page.wait_for_selector(
          "button:has-text('Show'), input[value='Show'],"
          " button:has-text('Search'), input[value='Search'], #btnSearch,"
          " #btnShow, .btn-primary",
          timeout=10000,
      )
      show_btn.click()
      page.wait_for_load_state("networkidle")
      page.wait_for_timeout(5000)
    except Exception as e:
      print(f"Notice: Show button step skipped or auto-loaded ({e})")

    # 4. Trigger Export Download
    print("Exporting data to Excel...")
    with page.expect_download(timeout=60000) as download_info:
      # Click Export / Excel button
      export_btn = page.wait_for_selector(
          "button:has-text('Export'), a:has-text('Export'),"
          " button:has-text('Excel'), a:has-text('Excel'), input[value='Export'],"
          " .fa-file-excel, .fa-download, #btnExport, #btnExcel",
          timeout=15000,
      )
      export_btn.click()

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

  # Read Excel or HTML export
  try:
    df = pd.read_excel(file_path)
  except Exception:
    df = pd.read_html(file_path)[0]

  # Filter Store Series #664 onwards safely in Python
  store_col = [
      col
      for col in df.columns
      if "store" in str(col).lower() or "site" in str(col).lower()
  ]
  if store_col:
    col_name = store_col[0]

    def filter_store(val):
      str_val = str(val)
      nums = [int(s) for s in str_val.replace("-", " ").split() if s.isdigit()]
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
