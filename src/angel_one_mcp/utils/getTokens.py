import pandas as pd
import os
from fuzzywuzzy import fuzz

from .normalize import normalize_company_name

MAPPINGS_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'mappings'))


def getTokenFromName(name: str, threshold: int = 90):
    """
    Resolve a company name to a token/symbol with safe fuzzy matching.

    Behavior changes:
    - If the top fuzzy match score is < 98, do NOT auto-resolve.
      Instead raise a ValueError listing the top 3 candidate matches and their scores,
      so the caller (and ultimately the user) can pick an exact trading symbol.
    - If a very high-confidence (>==98) match is found, continue the previous behavior.
    """
    normalized_name = normalize_company_name(name)

    # Try NSE first
    symbol = _get_symbol_from_nse(normalized_name=normalized_name, original_name=name, threshold=threshold)
    if symbol:
        # confirm against angel master mapping
        token, symbol_res, exch = getTokenFromAngelMaster(symbol=symbol + '-EQ')
        if not token and not exch:
            token, symbol_res, exch = getTokenFromAngelMaster(symbol=symbol)
        return (token, symbol_res, exch)

    # Try BSE
    token_bse, symbol_bse = _get_token_from_bse(normalized_name=normalized_name, original_name=name, threshold=threshold)
    if token_bse:
        return (token_bse, symbol_bse, "bse")

    # No exact match via normalized name: perform fuzzy scan and present top 3 candidates
    nse_candidates = _fuzzy_candidates_nse(normalized_name, name)
    bse_candidates = _fuzzy_candidates_bse(normalized_name, name)

    # Combine and sort by score
    combined = []
    for cand in nse_candidates:
        combined.append({"symbol": cand.get("symbol"), "score": cand.get("score"), "source": "nse", "name": cand.get("name")})
    for cand in bse_candidates:
        combined.append({"symbol": cand.get("symbol"), "score": cand.get("score"), "source": "bse", "name": cand.get("name")})

    combined_sorted = sorted(combined, key=lambda x: x["score"], reverse=True)
    top3 = combined_sorted[:3]

    if not top3:
        # nothing found
        return (None, None, None)

    # If top match has very high confidence, accept it (>=98)
    if top3[0]["score"] >= 98:
        top = top3[0]
        # For NSE, symbol may be directly returned; attempt angel master resolution
        if top["source"] == "nse":
            token, symbol_res, exch = getTokenFromAngelMaster(symbol=top["symbol"] + '-EQ')
            if not token and not exch:
                token, symbol_res, exch = getTokenFromAngelMaster(symbol=top["symbol"])
            return (token, symbol_res, exch)
        else:
            # bse
            token, symbol = top["symbol"], top.get("symbol")
            return (token, symbol, "bse")

    # Ambiguous: top score < 98 -> do not auto-select. Raise a ValueError with top 3 candidates
    readable = ", ".join([f'{c["symbol"]} (score={c["score"]}, source={c["source"]})' for c in top3])
    raise ValueError(f"Ambiguous symbol match for '{name}'. Top candidates: {readable}. Please provide the exact trading symbol (e.g., 'SBIN-EQ').")


def _get_symbol_from_nse(normalized_name: str, original_name: str, threshold: int = 90):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol_default.csv'))
        except FileNotFoundError:
            return None

    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        return None
    else:
        return required_df['SYMBOL'].iloc[0]


def _get_token_from_bse(normalized_name: str, original_name: str, threshold: int = 90):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol_default.csv'))
        except FileNotFoundError:
            return (None, None)

    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        return (None, None)
    else:
        return required_df['token'].iloc[0], required_df['symbol'].iloc[0]


def _fuzzy_candidates_nse(normalized_name: str, original_name: str, limit: int = 10):
    candidates = []
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol_default.csv'))
        except FileNotFoundError:
            return candidates

    for idx, row in df.iterrows():
        company_name = row.get("NAME OF COMPANY") or ""
        score = fuzz.token_sort_ratio(original_name.lower(), company_name.lower())
        candidates.append({"symbol": row.get("SYMBOL"), "name": company_name, "score": score})
    sorted_cands = sorted(candidates, key=lambda x: x["score"], reverse=True)
    return sorted_cands[:limit]


def _fuzzy_candidates_bse(normalized_name: str, original_name: str, limit: int = 10):
    candidates = []
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol_default.csv'))
        except FileNotFoundError:
            return candidates

    for idx, row in df.iterrows():
        company_name = row.get("name") or ""
        score = fuzz.token_sort_ratio(original_name.lower(), company_name.lower())
        candidates.append({"symbol": row.get("token"), "name": company_name, "score": score})
    sorted_cands = sorted(candidates, key=lambda x: x["score"], reverse=True)
    return sorted_cands[:limit]


def getTokenFromAngelMaster(symbol: str):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'angelOneMaster', 'master_mapping.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'angelOneMaster', 'master_mapping_default.csv'))
        except FileNotFoundError:
            return (None, None, None)
    if symbol.endswith('-EQ'):
        symbol = symbol[:-3]
    required_df = df[df["symbol"] == symbol + '-EQ']
    if required_df.empty:
        required_df = df[df["symbol"] == symbol]
        if required_df.empty:
            return (None, None, None)
        return (required_df['token'].item(), symbol, required_df['exch_seg'].item())
    else:
        return (required_df['token'].item(), symbol + '-EQ', required_df['exch_seg'].item())
