import pandas as pd
import os
from fuzzywuzzy import fuzz

from utils.normalize import normalize_company_name

MAPPINGS_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'mappings'))
def getTokenFromName(name: str, threshold: int = 90):
    normalized_name = normalize_company_name(name)
    symbol = getSymbolFromNse(normalized_name=normalized_name, original_name=name, threshold=threshold)
    if not symbol:
        token = getTokenFromBse(normalized_name=normalized_name, original_name=name, threshold=threshold)
        if not token:
            return (None, None)
        else:
            return (token, "bse")
    else:
        token, exch = getTokenFromAngelMaster(symbol=symbol+'-EQ')
        if not token and not exch:
            token, exch = getTokenFromAngelMaster(symbol=symbol)
            return (token, exch)
        else:
            return (token, exch)


def getSymbolFromNse(normalized_name: str, original_name: str, threshold: int = 90):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol.csv'))
    except FileNotFoundError:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol_default.csv'))
    
    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        best_match_row = None
        highest_score = 0
        
        for idx, row in df.iterrows():
            company_name = row["NAME OF COMPANY"]
            score = fuzz.token_sort_ratio(original_name.lower(), company_name.lower())
            if score > highest_score and score > threshold:
                highest_score = score
                best_match_row = row
        
        if best_match_row is not None:
            return best_match_row['SYMBOL']
        else:
            return None
    else:
        return required_df['SYMBOL'].iloc[0]

def getTokenFromBse(normalized_name: str, original_name: str, threshold: int = 90):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol.csv'))
    except FileNotFoundError:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol_default.csv'))
    
    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        best_match_row = None
        highest_score = 0
        
        for idx, row in df.iterrows():
            company_name = row["name"]
            score = fuzz.token_sort_ratio(original_name.lower(), company_name.lower())
            if score > highest_score and score > threshold:
                highest_score = score
                best_match_row = row
        
        if best_match_row is not None:
            return best_match_row['token']
        else:
            return None
    else:
        return required_df['token'].iloc[0]
    
def getTokenFromAngelMaster(symbol: str):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'angelOneMaster', 'master_mapping.csv'))
    except FileNotFoundError:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'angelOneMaster', 'master_mapping_default.csv'))
    required_df = df[df["symbol"] == symbol]
    if required_df.empty:
        return (None, None)
    else:
        return (required_df['token'].item(), required_df['exch_seg'].item())