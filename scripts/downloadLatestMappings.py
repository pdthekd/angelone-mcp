import requests
import json
import os
from bsedata.bse import BSE
import csv
import pandas as pd
from io import StringIO
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.normalize import normalize_company_name



def download_angle_master_json(base_path=None):
    try:
        if base_path is None:
            raise Exception("Base path is None")
        
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            data = [data]
        
        path = os.path.join(base_path, 'mappings', 'angelOneMaster', 'master_mapping.csv')
        dir = os.path.dirname(path)
        if not os.path.exists(dir):
            os.makedirs(dir)

        fieldnames = {'symbol', 'token', 'exch_seg'}
        with open(path, "w", newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in data:
                if isinstance(item, dict):
                    writer.writerow({'symbol': item['symbol'], 'token': item['token'], 'exch_seg': item['exch_seg']})
        print("Angel One master data downloaded successfully")
        
    except Exception as e:
        print(f"Error in download_angle_master_json: {e}")
    

def download_bse_token_name_json(base_path=None):
    try:
        if base_path is None:
            raise Exception("Base path is None")
        b = BSE(update_codes = True)
        b.getScripCodes()
        data = None
        file_location = os.path.join(os.getcwd(), 'stk.json')
        with open(file_location, 'r') as f:
            data = json.load(f)
        os.remove(file_location)
        
        path = os.path.join(base_path, 'mappings', 'bse', 'bse_symbol.csv')
        dir = os.path.dirname(path)
        if not os.path.exists(dir):
            os.makedirs(dir)
        
        fieldnames = {'token', 'name', 'normalizedName'}
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
        NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        })
        
        path = os.path.join(base_path, 'mappings', 'nse', 'nse_symbol.csv')
        dir = os.path.dirname(path)
        if not os.path.exists(dir):
            os.makedirs(dir)
        
        homepage = session.get("https://www.nseindia.com/", timeout=10)
        time.sleep(3)
        
        response = session.get(NSE_EQUITY_LIST_URL, timeout=30)
        response.raise_for_status()
        
        df = pd.read_csv(StringIO(response.content.decode('utf-8')))
        columns = ['SYMBOL', 'NAME OF COMPANY', 'normalizedName']
        new_df = df[[col for col in columns if col in df.columns]].copy()
        new_df['normalizedName'] = df['NAME OF COMPANY'].apply(normalize_company_name)
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