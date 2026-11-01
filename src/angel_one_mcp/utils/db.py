# src/angel_one_mcp/utils/db.py
import sqlite3
import json
import os
import hmac
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

_DB_PATH = None
_CONN: Optional[sqlite3.Connection] = None


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def init_db(db_path: Optional[str] = None):
    """
    Initialize or open the SQLite DB and ensure required tables exist.
    """
    global _DB_PATH, _CONN
    if db_path is None:
        db_path = os.getenv("TRADE_DB_PATH", "./trades_audit.db")
        
    # Ensure the parent directory exists before creating the database file
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    _DB_PATH = abs_path
    
    _CONN = sqlite3.connect(_DB_PATH, check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    cur = _CONN.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_intents (
            request_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            order_params TEXT NOT NULL,
            meta TEXT,
            status TEXT NOT NULL,
            execution_timestamp TEXT,
            execution_result TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            details TEXT NOT NULL,
            signature TEXT
        )
        """
    )

    # Migration: older DBs have no approved_at column.
    try:
        cur.execute("ALTER TABLE trade_intents ADD COLUMN approved_at TEXT")
    except sqlite3.OperationalError:
        pass

    _CONN.commit()


def _get_conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        init_db()
    return _CONN


def insert_trade_intent(request_id: str, order_params: Dict[str, Any], meta: Optional[Dict[str, Any]] = None):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO trade_intents (request_id, created_at, order_params, meta, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (request_id, _now_iso(), json.dumps(order_params), json.dumps(meta or {}), "PENDING"),
    )
    conn.commit()


def get_trade_intent(request_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trade_intents WHERE request_id = ?", (request_id,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "request_id": row["request_id"],
        "created_at": row["created_at"],
        "order_params": json.loads(row["order_params"]),
        "meta": json.loads(row["meta"]) if row["meta"] else {},
        "status": row["status"],
        "execution_timestamp": row["execution_timestamp"],
        "execution_result": json.loads(row["execution_result"]) if row["execution_result"] else None,
    }


def update_trade_intent_status(request_id: str, status: str, execution_result: Optional[Dict[str, Any]] = None):
    conn = _get_conn()
    cur = conn.cursor()
    exec_ts = _now_iso() if status in ("EXECUTED", "FAILED", "CANCELLED") else None
    exec_result_json = json.dumps(execution_result) if execution_result is not None else None
    cur.execute(
        """
        UPDATE trade_intents
        SET status = ?,
            execution_timestamp = COALESCE(execution_timestamp, ?),
            execution_result = COALESCE(execution_result, ?)
        WHERE request_id = ?
        """,
        (status, exec_ts, exec_result_json, request_id),
    )
    conn.commit()


def mark_human_approved(request_id: str) -> bool:
    """Transition a PENDING intent to HUMAN_APPROVED. Returns True if the transition happened."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE trade_intents SET status = 'HUMAN_APPROVED', approved_at = ? WHERE request_id = ? AND status = 'PENDING'",
        (_now_iso(), request_id),
    )
    conn.commit()
    return cur.rowcount > 0


def claim_for_execution(request_id: str, ttl_seconds: int = 900) -> str:
    """Atomically move HUMAN_APPROVED -> EXECUTING so only one caller can run an
    intent. Returns "OK", or "EXPIRED" if the approval is older than ttl_seconds
    (the intent is then reset to PENDING and needs a fresh PIN), or "NOT_APPROVED"."""
    conn = _get_conn()
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(seconds=ttl_seconds)).isoformat() + "Z"
    cur.execute(
        "UPDATE trade_intents SET status = 'EXECUTING' "
        "WHERE request_id = ? AND status = 'HUMAN_APPROVED' AND approved_at >= ?",
        (request_id, cutoff),
    )
    conn.commit()
    if cur.rowcount > 0:
        return "OK"
    cur.execute(
        "UPDATE trade_intents SET status = 'PENDING', approved_at = NULL "
        "WHERE request_id = ? AND status = 'HUMAN_APPROVED'",
        (request_id,),
    )
    conn.commit()
    return "EXPIRED" if cur.rowcount > 0 else "NOT_APPROVED"


def find_open_gtt_create_intents(symbol: str, exchange: str, transaction_type: str) -> List[Dict[str, Any]]:
    """Find local GTT-create intents for a symbol/exchange/side that haven't been
    executed yet (still PENDING or HUMAN_APPROVED) — used to catch accidental
    duplicate GTT rule creation before it reaches the broker."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT request_id, created_at, order_params, meta, status FROM trade_intents "
        "WHERE status IN ('PENDING', 'HUMAN_APPROVED', 'EXECUTING') ORDER BY created_at DESC"
    )
    matches = []
    for row in cur.fetchall():
        meta = json.loads(row["meta"]) if row["meta"] else {}
        if meta.get("type") != "gttCreateRule":
            continue
        params = json.loads(row["order_params"])
        if (
            params.get("tradingsymbol") == symbol
            and params.get("exchange") == exchange
            and params.get("transactiontype") == transaction_type
        ):
            matches.append(
                {
                    "request_id": row["request_id"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                    "price": params.get("price"),
                    "trigger_price": params.get("triggerprice"),
                    "qty": params.get("qty"),
                }
            )
    return matches


def list_pending_intents() -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trade_intents WHERE status = 'PENDING' ORDER BY created_at DESC")
    rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "request_id": row["request_id"],
                "created_at": row["created_at"],
                "order_params": json.loads(row["order_params"]),
                "meta": json.loads(row["meta"]) if row["meta"] else {},
                "status": row["status"],
            }
        )
    return out


def _compute_signature(event_type: str, timestamp: str, details_json: str) -> Optional[str]:
    key = os.getenv("AUDIT_HMAC_KEY")
    if not key:
        return None
    payload = f"{event_type}|{timestamp}|{details_json}".encode("utf-8")
    sig = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return sig


def log_audit_event(event_type: str, details: Dict[str, Any]):
    conn = _get_conn()
    cur = conn.cursor()
    ts = _now_iso()
    details_json = json.dumps(details, sort_keys=True)
    signature = _compute_signature(event_type, ts, details_json)
    cur.execute(
        """
        INSERT INTO audit_log (event_type, timestamp, details, signature)
        VALUES (?, ?, ?, ?)
        """,
        (event_type, ts, details_json, signature),
    )
    conn.commit()


def get_recent_audit_events(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recent audit events from the audit_log table, ordered by timestamp DESC."""
    global _CONN
    if _CONN is None:
        raise Exception("Database not initialized. Call init_db() first.")
    cur = _CONN.cursor()
    cur.execute(
        """
        SELECT event_type, timestamp, details, signature
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,)
    )
    rows = cur.fetchall()
    events = []
    for row in rows:
        event_type, timestamp, details_json, signature = row
        try:
            details = json.loads(details_json)
        except (json.JSONDecodeError, TypeError):
            details = {}
        events.append({
            "event_type": event_type,
            "timestamp": timestamp,
            "details": details,
            "signature": signature
        })
    return events