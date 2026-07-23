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
    page.goto(
        "https://fams.vmart.co.in/WebfamsLive/Account/Login",
        wait_until="domcontentloaded",
    )

    user_input = page.wait_for_selector(
        "input[name='Username'], input#Username, input[type='text']",
        timeout=20000,
    )
    user_input.fill(FAMS_USER)

    pass_input = page.wait_for_selector(
        "input[name='Password'], input#Password, input[type='password']",
        timeout=20000,
    )
    pass_input.fill(FAMS_PASS)

    page.click("button[type='submit'], input[type='submit'], #btnLogin")
    page.wait_for_load_state("networkidle")

    # 2. Go to Utilities -> Asset Enquiry Report
    print("Navigating to Asset Enquiry Report...")
    page.goto(
        "https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport",
        wait_until="domcontentloaded",
    )
    page.wait_for_timeout(4000)

    # 3. Select Stores (#664 onwards) if dropdown exists
    try:
      store_select = page.query_selector(
          "select[name*='Store'], select#StoreId, select#LocationId"
      )
      if store_select:
        options = page.eval_on_selector_all(
            "select[name*='Store'] option, select#StoreId option",
            "opts => opts.map(o => ({text: o.text, value: o.value}))",
        )
        selected_vals = []
        for opt in options:
          nums = [
              int(s)
              for s in opt["text"].replace("-", " ").split()
              if s.isdigit()
          ]
          if nums and nums[0] >= 664:
            selected_vals.append(opt["value"])
        if selected_vals:
          page.select_option(
              "select[name*='Store'], select#StoreId", value=selected_vals
          )
          print(f"Selected {len(selected_vals)} stores starting from #664.")
    except Exception as e:
      print(f"Store dropdown handling note: {e}")

    # 4. Click Show / Search
    try:
      show_btn = page.query_selector(
          "input[value='Show'], button:has-text('Show'), input[value='Search'],"
          " button:has-text('Search'), #btnSearch, #btnShow"
      )
      if show_btn:
        print("Clicking Show/Search...")
        show_btn.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(4000)
    except Exception as e:
      print(f"Show button note: {e}")

    # 5. Export Excel Download
    print("Clicking Export to Excel...")
    with page.expect_download(timeout=60000) as download_info:
      # Target Export button, Excel icon, or link
      export_selector = (
          "a:has-text('Export'), button:has-text('Export'),"
          " a:has-text('Excel'), button:has-text('Excel'),"
          " input[value*='Export'], .fa-file-excel, .fa-download, #btnExport,"
          " #btnExcel"
      )
      page.click(export_selector)

    download = download_info.value
    file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")
    download.save_as(file_path)
    print(f"Report downloaded successfully to {file_path}")
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

  try:
    df = pd.read_excel(file_path)
  except Exception:
    df = pd.read_html(file_path)[0]

  # Python store filtering safety net (Store #664 onwards)
  store_col = [
      col
      for col in df.columns
      if "store" in str(col).lower()
      or "site" in str(col).lower()
      or "location" in str(col).lower()
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
