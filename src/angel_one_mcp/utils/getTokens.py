import pandas as pd
import os
from fuzzywuzzy import fuzz

from .normalize import normalize_company_name

MAPPINGS_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, 'mappings'))


def getTokenFromName(name: str, threshold: int = 90):
    """
    Resolve a company name to a token/symbol with safe fuzzy matching.

    Behavior:
    - Try exact normalized-name matches in NSE and BSE mappings first.
    - If none found, compute fuzzy candidates from NSE and BSE.
    - If top candidate score >= 98, auto-resolve to that token/symbol.
    - Otherwise raise ValueError listing the top 3 candidates so the user/operator can select the exact trading symbol.
    """
    normalized_name = normalize_company_name(name)

    # Try exact normalized match for NSE
    try:
        symbol = getSymbolFromNse(normalized_name=normalized_name, original_name=name, threshold=threshold)
    except Exception:
        symbol = None

    if symbol:
        # Resolve via Angel master mapping
        token, symbol_res, exch = getTokenFromAngelMaster(symbol=symbol + '-EQ')
        if not token and not exch:
            token, symbol_res, exch = getTokenFromAngelMaster(symbol=symbol)
        return (token, symbol_res, exch)

    # Try exact normalized match for BSE
    try:
        token_bse, symbol_bse = getTokenFromBse(normalized_name=normalized_name, original_name=name, threshold=threshold)
    except Exception:
        token_bse, symbol_bse = (None, None)

    if token_bse:
        return (token_bse, symbol_bse, "bse")

    # No exact match via normalized name: perform fuzzy candidate search
    nse_candidates = _fuzzy_candidates_nse(normalized_name, name)
    bse_candidates = _fuzzy_candidates_bse(normalized_name, name)

    combined = []
    for cand in nse_candidates:
        combined.append({"symbol": cand.get("symbol"), "score": cand.get("score"), "source": "nse", "name": cand.get("name")})
    for cand in bse_candidates:
        combined.append({"symbol": cand.get("symbol"), "score": cand.get("score"), "source": "bse", "name": cand.get("name")})

    combined_sorted = sorted(combined, key=lambda x: x["score"], reverse=True)
    top3 = combined_sorted[:3]

    if not top3:
        return (None, None, None)

    # If top match has very high confidence, accept it (>= 98)
    if top3[0]["score"] >= 98:
        top = top3[0]
        if top["source"] == "nse":
            token, symbol_res, exch = getTokenFromAngelMaster(symbol=top["symbol"] + '-EQ')
            if not token and not exch:
                token, symbol_res, exch = getTokenFromAngelMaster(symbol=top["symbol"])
            return (token, symbol_res, exch)
        else:
            # bse candidate: symbol field holds token
            return (top["symbol"], top["symbol"], "bse")

    # Ambiguous: top score < 98 -> do not auto-select. Raise ValueError with top 3 candidates
    readable = ", ".join([f'{c["symbol"]} (score={c["score"]}, source={c["source"]})' for c in top3])
    raise ValueError(f"Ambiguous symbol match for '{name}'. Top candidates: {readable}. Please provide the exact trading symbol (e.g., 'SBIN-EQ').")


def getSymbolFromNse(normalized_name: str, original_name: str, threshold: int = 90):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'nse', 'nse_symbol_default.csv'))
        except FileNotFoundError:
            return None

    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        best_match_row = None
        highest_score = 0

        for idx, row in df.iterrows():
            company_name = row.get("NAME OF COMPANY") or ""
            try:
                score = fuzz.token_sort_ratio(original_name.lower(), company_name.lower())
            except Exception:
                score = 0
            if score > highest_score and score > threshold:
                highest_score = score
                best_match_row = row

        if best_match_row is not None:
            return best_match_row.get('SYMBOL')
        else:
            return None
    else:
        return required_df['SYMBOL'].iloc[0]


def getTokenFromBse(normalized_name: str, original_name: str, threshold: int = 90):
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol.csv'))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, 'bse', 'bse_symbol_default.csv'))
        except FileNotFoundError:
            return (None, None)

    required_df = df[df["normalizedName"] == normalized_name]
    if required_df.empty:
        best_match_row = None
        highest_score = 0

        for idx, row in df.iterrows():
            company_name = row.get("name") or ""
            try:
                score = fuzz.token_sort_ratio(original_name.lower(), company_name.lower())
            except Exception:
                score = 0
            if score > highest_score and score > threshold:
                highest_score = score
                best_match_row = row

        if best_match_row is not None:
            return best_match_row.get('token'), best_match_row.get('symbol')
        else:
            return None, None
    else:
        return required_df['token'].iloc[0], required_df['symbol'].iloc[0]


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


# ----------------------------------------------------------------------
# Test-compatible fuzzy candidate helpers
# These are exposed so tests can monkeypatch them easily.
# They compute fuzzy match scores from the local mapping CSVs.
# ----------------------------------------------------------------------
def _fuzzy_candidates_nse(normalized_name: str, original_name: str, limit: int = 10):
    """
    Return top-N candidate dicts: {"symbol": ..., "name": ..., "score": ...}
    """
    candidates = []
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, "nse", "nse_symbol.csv"))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, "nse", "nse_symbol_default.csv"))
        except FileNotFoundError:
            return candidates

    # some CSVs may not have the expected columns; guard that
    for idx, row in df.iterrows():
        company_name = row.get("NAME OF COMPANY") or row.get("name") or ""
        symbol = row.get("SYMBOL") or row.get("symbol") or ""
        try:
            score = fuzz.token_sort_ratio(original_name.lower(), str(company_name).lower())
        except Exception:
            score = 0
        candidates.append({"symbol": symbol, "name": company_name, "score": score})

    sorted_cands = sorted(candidates, key=lambda x: x["score"], reverse=True)
    return sorted_cands[:limit]


def _fuzzy_candidates_bse(normalized_name: str, original_name: str, limit: int = 10):
    """
    Return top-N candidate dicts for BSE: {"symbol": token, "name": ..., "score": ...}
    """
    candidates = []
    try:
        df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, "bse", "bse_symbol.csv"))
    except FileNotFoundError:
        try:
            df = pd.read_csv(os.path.join(MAPPINGS_FOLDER, "bse", "bse_symbol_default.csv"))
        except FileNotFoundError:
            return candidates

    for idx, row in df.iterrows():
        company_name = row.get("name") or row.get("NAME OF COMPANY") or ""
        symbol = row.get("token") or row.get("symbol") or ""
        try:
            score = fuzz.token_sort_ratio(original_name.lower(), str(company_name).lower())
        except Exception:
            score = 0
        candidates.append({"symbol": symbol, "name": company_name, "score": score})

    sorted_cands = sorted(candidates, key=lambda x: x["score"], reverse=True)
    return sorted_cands[:limit]