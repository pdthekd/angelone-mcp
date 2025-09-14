import pandas as pd
import os

from utils.normalize import normalize_company_name

MAPPINGS_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'mappings'))
def getTokenFromName(name: str):
    normalized_name = normalize_company_name(name)
    symbol = getSymbolFromNse(normalized_name=normalized_name)
    if not symbol:
        token = getTokenFromBse(normalized_name=normalized_name)
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


def getSymbolFromNse(normalized_name: str):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol.csv'))
    except FileNotFoundError:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol_default.csv'))
    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        return None
    else:
        return required_df['SYMBOL'].item()
    
def getTokenFromBse(normalized_name: str):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol.csv'))
    except FileNotFoundError:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol_default.csv'))
    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        return None
    else:
        return required_df['token'].item()
    
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