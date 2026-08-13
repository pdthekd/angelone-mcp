# src/angel_one_mcp/utils/db.py
import sqlite3
import json
import os
import hmac
import hashlib
from datetime import datetime
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
    _DB_PATH = db_path
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
