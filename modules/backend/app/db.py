"""SQLite 存储：无外部依赖，单文件，线程安全（锁 + 每次新连接）。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

from . import config

_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dishes (
    dish_id TEXT PRIMARY KEY, name TEXT NOT NULL,
    countable INTEGER DEFAULT 0, unit_name TEXT, unit_mass_g REAL,
    std_portion_g REAL, prep_time_min REAL DEFAULT 3, batch_size REAL,
    freshness_min REAL, cost_per_10g REAL, note TEXT,
    dispatch_suspended INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS servings (
    serving_id TEXT PRIMARY KEY, plate_id TEXT NOT NULL, dish_id TEXT NOT NULL,
    status TEXT DEFAULT 'open', opened_at TEXT, closed_at TEXT, close_reason TEXT,
    tare_g REAL, baseline_g REAL, trusted_net_g REAL, simulated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS station_events (
    event_id TEXT PRIMARY KEY, serving_id TEXT, plate_id TEXT NOT NULL,
    station_id TEXT NOT NULL, observed_at TEXT NOT NULL, lap_index INTEGER,
    net_weight_g REAL, gross_weight_g REAL, tare_g REAL, item_count INTEGER,
    dish_id_claim TEXT, image_ref TEXT, visual_level TEXT, quality TEXT,
    source TEXT, simulated INTEGER DEFAULT 0, interpretation TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    op_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, plate_id TEXT,
    op_type TEXT NOT NULL, dish_id TEXT, recorded_net_g REAL, recorded_added_g REAL,
    tare_g REAL, disposal_reason TEXT, operator TEXT, note TEXT, source TEXT,
    simulated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY, action TEXT NOT NULL, dish_id TEXT NOT NULL,
    serving_id TEXT, plate_id TEXT, quantity_g REAL, quantity_count INTEGER,
    unit TEXT DEFAULT 'g', priority TEXT DEFAULT 'medium', need_by TEXT,
    status TEXT DEFAULT 'pending', reason TEXT, evidence TEXT,
    simulated INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
    closed_at TEXT, close_note TEXT
);
CREATE TABLE IF NOT EXISTS covers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
    party_size INTEGER NOT NULL, note TEXT, simulated INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_events_plate_time ON station_events(plate_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_events_serving ON station_events(serving_id);
CREATE INDEX IF NOT EXISTS idx_ops_plate_time ON operations(plate_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_tasks_dish_status ON tasks(dish_id, status);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def execute(sql: str, params: tuple = ()) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


# ---- 便捷封装 ----

def upsert_dish(d: dict) -> None:
    execute(
        """INSERT INTO dishes (dish_id,name,countable,unit_name,unit_mass_g,std_portion_g,
               prep_time_min,batch_size,freshness_min,cost_per_10g,note)
           VALUES (:dish_id,:name,:countable,:unit_name,:unit_mass_g,:std_portion_g,
                   :prep_time_min,:batch_size,:freshness_min,:cost_per_10g,:note)
           ON CONFLICT(dish_id) DO UPDATE SET name=:name,countable=:countable,
               unit_name=:unit_name,unit_mass_g=:unit_mass_g,std_portion_g=:std_portion_g,
               prep_time_min=:prep_time_min,batch_size=:batch_size,freshness_min=:freshness_min,
               cost_per_10g=:cost_per_10g,note=:note""",
        d,
    )


def get_dishes() -> list[dict]:
    return query("SELECT * FROM dishes ORDER BY dish_id")


def get_dish(dish_id: str) -> Optional[dict]:
    return query_one("SELECT * FROM dishes WHERE dish_id=?", (dish_id,))


def set_dish_flag(dish_id: str, flag: str, value: bool) -> None:
    execute(f"UPDATE dishes SET {flag}=? WHERE dish_id=?", (int(value), dish_id))


def insert_serving(s: dict) -> None:
    execute(
        """INSERT OR REPLACE INTO servings (serving_id,plate_id,dish_id,status,opened_at,
               closed_at,close_reason,tare_g,baseline_g,trusted_net_g,simulated)
           VALUES (:serving_id,:plate_id,:dish_id,:status,:opened_at,:closed_at,
                   :close_reason,:tare_g,:baseline_g,:trusted_net_g,:simulated)""",
        s,
    )


def get_servings(status: Optional[str] = None, plate_id: Optional[str] = None) -> list[dict]:
    sql, params = "SELECT * FROM servings", ()
    conds = []
    if status:
        conds.append("status=?")
        params += (status,)
    if plate_id:
        conds.append("plate_id=?")
        params += (plate_id,)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return query(sql + " ORDER BY opened_at", params)


def insert_event(e: dict) -> None:
    e = dict(e)
    e["interpretation"] = json.dumps(e.get("interpretation"), ensure_ascii=False)
    execute(
        """INSERT OR IGNORE INTO station_events (event_id,serving_id,plate_id,station_id,
               observed_at,lap_index,net_weight_g,gross_weight_g,tare_g,item_count,
               dish_id_claim,image_ref,visual_level,quality,source,simulated,interpretation,created_at)
           VALUES (:event_id,:serving_id,:plate_id,:station_id,:observed_at,:lap_index,
                   :net_weight_g,:gross_weight_g,:tare_g,:item_count,:dish_id_claim,:image_ref,
                   :visual_level,:quality,:source,:simulated,:interpretation,:created_at)""",
        e,
    )


def get_events(plate_id: Optional[str] = None, limit: int = 500) -> list[dict]:
    if plate_id:
        rows = query(
            "SELECT * FROM station_events WHERE plate_id=? ORDER BY observed_at LIMIT ?",
            (plate_id, limit),
        )
    else:
        rows = query("SELECT * FROM station_events ORDER BY observed_at LIMIT ?", (limit,))
    for r in rows:
        r["interpretation"] = json.loads(r["interpretation"]) if r["interpretation"] else None
    return rows


def insert_op(o: dict) -> None:
    execute(
        """INSERT OR IGNORE INTO operations (op_id,timestamp,plate_id,op_type,dish_id,
               recorded_net_g,recorded_added_g,tare_g,disposal_reason,operator,note,source,simulated)
           VALUES (:op_id,:timestamp,:plate_id,:op_type,:dish_id,:recorded_net_g,
                   :recorded_added_g,:tare_g,:disposal_reason,:operator,:note,:source,:simulated)""",
        o,
    )


def get_ops(plate_id: Optional[str] = None, after: Optional[str] = None, before: Optional[str] = None,
            limit: int = 500) -> list[dict]:
    sql, params = "SELECT * FROM operations", ()
    conds, vals = [], []
    if plate_id:
        conds.append("plate_id=?")
        vals.append(plate_id)
    if after:
        conds.append("timestamp>?")
        vals.append(after)
    if before:
        conds.append("timestamp<=?")
        vals.append(before)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
        params = tuple(vals)
    return query(sql + " ORDER BY timestamp LIMIT ?", params + (limit,))


def insert_task(t: dict) -> None:
    t = dict(t)
    t["evidence"] = json.dumps(t.get("evidence") or [], ensure_ascii=False)
    execute(
        """INSERT OR REPLACE INTO tasks (task_id,action,dish_id,serving_id,plate_id,
               quantity_g,quantity_count,unit,priority,need_by,status,reason,evidence,
               simulated,created_at,updated_at,closed_at,close_note)
           VALUES (:task_id,:action,:dish_id,:serving_id,:plate_id,:quantity_g,:quantity_count,
                   :unit,:priority,:need_by,:status,:reason,:evidence,:simulated,
                   :created_at,:updated_at,:closed_at,:close_note)""",
        t,
    )


def get_tasks(status: Optional[str] = None, dish_id: Optional[str] = None) -> list[dict]:
    sql, params = "SELECT * FROM tasks", ()
    conds, vals = [], []
    if status:
        conds.append("status=?")
        vals.append(status)
    if dish_id:
        conds.append("dish_id=?")
        vals.append(dish_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
        params = tuple(vals)
    rows = query(sql + " ORDER BY created_at DESC", params)
    for r in rows:
        r["evidence"] = json.loads(r["evidence"]) if r["evidence"] else []
    return rows


def insert_covers(timestamp: str, party_size: int, note: Optional[str], simulated: bool) -> None:
    execute(
        "INSERT INTO covers (timestamp,party_size,note,simulated) VALUES (?,?,?,?)",
        (timestamp, party_size, note, int(simulated)),
    )


def get_meta(k: str) -> Optional[str]:
    row = query_one("SELECT v FROM meta WHERE k=?", (k,))
    return row["v"] if row else None


def set_meta(k: str, v: Any) -> None:
    execute(
        "INSERT INTO meta (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v),
    )


def reset_all() -> None:
    """清空业务数据（保留菜品配置），回放前调用。"""
    with _LOCK:
        conn = _connect()
        try:
            for t in ("station_events", "operations", "tasks", "servings", "covers", "meta"):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        finally:
            conn.close()
