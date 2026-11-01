#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DAG_CACHE_V1 — kes grana za dag_workers (ponovljen upit ~10ms vs ~5s)."""
import os
import json
import time
import hashlib
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dag_cache.db")
TTL = int(os.environ.get("DAG_CACHE_TTL", "21600"))  # 6h
_conn = None


def _c():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB, check_same_thread=False, timeout=5)
        _conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
        _conn.commit()
    return _conn


def _key(goal, aspect, resurs):
    raw = "|".join([str(goal), str(aspect), str(resurs)])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# akcioni upiti koje NIKAD ne kesiramo (zavise od trenutka/akcije)
_FRESH = ("posalji", "send", "obrisi", "delete", "napisi", "write", "otvori",
          "klikni", "pokreni", "run", "danas", "sada", "trenutno", "email", "mail")


def get(goal, aspect, resurs):
    if any(x in str(goal).lower() for x in _FRESH):
        return None
    try:
        r = _c().execute("SELECT v, ts FROM cache WHERE k=?", (_key(goal, aspect, resurs),)).fetchone()
        if r and (time.time() - r[1]) < TTL:
            return json.loads(r[0])
    except Exception:
        pass
    return None


def put(goal, aspect, resurs, val):
    if any(x in str(goal).lower() for x in _FRESH):
        return
    try:
        _c().execute("INSERT OR REPLACE INTO cache (k, v, ts) VALUES (?,?,?)",
                     (_key(goal, aspect, resurs), json.dumps(val, ensure_ascii=False), time.time()))
        _c().commit()
    except Exception:
        pass


def stats():
    try:
        n = _c().execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        return {"entries": n, "ttl": TTL, "db": DB}
    except Exception:
        return {"entries": 0}


def clear():
    try:
        _c().execute("DELETE FROM cache")
        _c().commit()
    except Exception:
        pass
