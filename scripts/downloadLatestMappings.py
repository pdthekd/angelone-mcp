import requests
import json
import os
from bsedata.bse import BSE
import csv
import pandas as pd
from io import StringIO
import time
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.normalize import normalize_company_name

# Allowlist of domains
ALLOWED_DOMAINS = {
    "margincalculator.angelbroking.com",
    "nsearchives.nseindia.com",
}


def _fetch_url(url: str, timeout: float = 15.0, verify: bool = True) -> requests.Response:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain not in ALLOWED_DOMAINS:
        raise Exception(f"Domain not allowed for download: {domain}")
    sess = requests.Session()
    # safe headers
    sess.headers.update({
        "User-Agent": "angelone-mcp-mapping-fetcher/1.0",
        "Accept": "*/*",
    })
    resp = sess.get(url, timeout=timeout, verify=verify)
    resp.raise_for_status()
    return resp


def download_angle_master_json(base_path=None):
    try:
        if base_path is None:
            raise Exception("Base path is None")

        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = _fetch_url(url, timeout=15.0, verify=True)
        data = response.json()

        if not isinstance(data, list):
            data = [data]

        path = os.path.join(base_path, 'mappings', 'angelOneMaster', 'master_mapping.csv')
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)

        fieldnames = ['symbol', 'token', 'exch_seg']
        with open(path, "w", newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in data:
                if isinstance(item, dict):
                    writer.writerow({'symbol': item.get('symbol'), 'token': item.get('token'), 'exch_seg': item.get('exch_seg')})
        print("Angel One master data downloaded successfully")

    except Exception as e:
        print(f"Error in download_angle_master_json: {e}")


def download_bse_token_name_json(base_path=None):
    try:
        if base_path is None:
            raise Exception("Base path is None")
        # bsedata.BSE will manage its own endpoints; we keep usage but be aware it may contact BSE domains
        b = BSE(update_codes=True)
        b.getScripCodes()
        data = None
        file_location = os.path.join(os.getcwd(), 'stk.json')
        if not os.path.exists(file_location):
            raise Exception("Expected stk.json file not found after BSE update (bsedata library behavior)")

        with open(file_location, 'r') as f:
            data = json.load(f)
        # remove temporary file if exists
        try:
            os.remove(file_location)
        except Exception:
            pass

        path = os.path.join(base_path, 'mappings', 'bse', 'bse_symbol.csv')
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)

        fieldnames = ['token', 'name', 'normalizedName']
        with open(path, "w", newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for k, v in data.items():
                writer.writerow({'token': k, 'name': v, 'normalizedName': normalize_company_name(v)})
        print("BSE token data downloaded successfully")

    except Exception as e:
        print(f"Error in download_bse_token_name_json: {e}")


def download_nse_symbol_name_json(base_path=None):
    try:
        if base_path is None:
            raise Exception("Base path is None")
        # NSE equity CSV (allowed domain)
        NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

        # Fetch CSV directly using allowlisted helper
        response = _fetch_url(NSE_EQUITY_LIST_URL, timeout=15.0, verify=True)
        # Parse CSV
        df = pd.read_csv(StringIO(response.content.decode('utf-8')))
        columns = ['SYMBOL', 'NAME OF COMPANY', 'normalizedName']
        new_df = df[[col for col in columns if col in df.columns]].copy()
        new_df['normalizedName'] = df['NAME OF COMPANY'].apply(normalize_company_name)

        path = os.path.join(base_path, 'mappings', 'nse', 'nse_symbol.csv')
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)

        new_df.to_csv(path, index=False)
        print("NSE symbol data downloaded successfully")

    except Exception as e:
        print(f"Error in download_nse_symbol_name_json: {e}")


if __name__ == '__main__':
    base_path = sys.argv[1] if len(sys.argv) > 1 else None
    if base_path:
        print(f"Using base path: {base_path}")
    else:
        base_path = os.path.dirname(os.path.dirname(__file__))
        print(f"Using default base path: {base_path}")

    download_angle_master_json(base_path)
    download_bse_token_name_json(base_path)
    download_nse_symbol_name_json(base_path)
