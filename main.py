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

    # Wait for input fields to appear flexible selectors
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

    # Click login button
    page.click("button[type='submit'], input[type='submit'], #btnLogin")
    page.wait_for_load_state("networkidle")

    # 2. Navigate to Asset Enquiry Report and download file
    print("Downloading Asset Enquiry Report...")
    page.goto(
        "https://fams.vmart.co.in/WebfamsLive/AssetEnquiryReport",
        wait_until="networkidle",
    )

    with page.expect_download() as download_info:
      # Flexible click selector for export button
      page.click(
        "text=Export, text=Download, button:has-text('Export'),"
        " #btnExport, .btn-export"
      )

    download = download_info.value
    file_path = os.path.join(DOWNLOAD_DIR, "asset_report.xlsx")
    download.save_as(file_path)
    browser.close()
    return file_path


def update_google_sheet(file_path):
  print("Updating Google Sheet...")
  creds_dict = json.loads(GOOGLE_CREDS)
  gc = gspread.service_account_from_dict(creds_dict)

  spreadsheet_id = "18QGSZZa-H5PucmrScf0B2gJ8VMfe6vhVE7wg_FkZtPY"
  sh = gc.open_by_key(spreadsheet_id)

  # Access the "FAR Data" worksheet
  worksheet = sh.worksheet("FAR Data")

  # Read downloaded Excel file
  df = pd.read_excel(file_path)
  df = df.fillna("")

  # Clear old data and replace with new data
  worksheet.clear()
  worksheet.update([df.columns.values.tolist()] + df.values.tolist())
  print("Google Sheet updated successfully!")


if __name__ == "__main__":
  file_path = download_fams_report()
  update_google_sheet(file_path)
