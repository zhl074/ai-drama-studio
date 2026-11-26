"""SQLite 持久层（只用标准库，无额外依赖）"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    genre       TEXT,
    logline     TEXT,
    synopsis    TEXT,
    characters  TEXT,          -- JSON array
    outline     TEXT,          -- JSON array
    idea        TEXT,          -- 用户原始创意
    cover       TEXT,          -- 封面图相对路径
    created_at  REAL
);

CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    ep_index    INTEGER NOT NULL,
    title       TEXT,
    script      TEXT,          -- JSON
    shots       TEXT,          -- JSON
    video       TEXT,          -- 相对路径
    duration    REAL DEFAULT 0,
    status      TEXT DEFAULT 'draft',
    created_at  REAL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ep_project ON episodes(project_id);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def _now() -> float:
    return time.time()


def _loads(s: Optional[str], default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------- projects
def create_project(*, title: str, genre: str, logline: str, synopsis: str,
                   characters: List[dict], outline: List[dict], idea: str) -> str:
    pid = uuid.uuid4().hex[:12]
    with connect() as c:
        c.execute(
            "INSERT INTO projects (id,title,genre,logline,synopsis,characters,outline,idea,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, title, genre, logline, synopsis, _dumps(characters),
             _dumps(outline), idea, _now()),
        )
    return pid


def list_projects() -> List[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM episodes e WHERE e.project_id=p.id) AS ep_count"
            " FROM projects p ORDER BY p.created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["characters"] = _loads(d.get("characters"), [])
        d["outline"] = _loads(d.get("outline"), [])
        out.append(d)
    return out


def get_project(pid: str) -> Optional[dict]:
    with connect() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["characters"] = _loads(d.get("characters"), [])
    d["outline"] = _loads(d.get("outline"), [])
    return d


def set_project_cover(pid: str, cover: str) -> None:
    with connect() as c:
        c.execute("UPDATE projects SET cover=? WHERE id=?", (cover, pid))


def delete_project(pid: str) -> bool:
    with connect() as c:
        cur = c.execute("DELETE FROM projects WHERE id=?", (pid,))
    return cur.rowcount > 0


# ---------------------------------------------------------------- episodes
def create_episode(*, project_id: str, ep_index: int, title: str,
                   script: dict, shots: List[dict]) -> str:
    eid = uuid.uuid4().hex[:12]
    duration = sum(float(s.get("duration") or 0) for s in shots)
    with connect() as c:
        c.execute(
            "INSERT INTO episodes (id,project_id,ep_index,title,script,shots,duration,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, project_id, ep_index, title, _dumps(script), _dumps(shots),
             duration, "storyboarded", _now()),
        )
    return eid


def list_episodes(project_id: str) -> List[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM episodes WHERE project_id=? ORDER BY ep_index",
            (project_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["script"] = _loads(d.get("script"), {})
        d["shots"] = _loads(d.get("shots"), [])
        out.append(d)
    return out


def get_episode(eid: str) -> Optional[dict]:
    with connect() as c:
        r = c.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["script"] = _loads(d.get("script"), {})
    d["shots"] = _loads(d.get("shots"), [])
    return d


def update_episode(eid: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {"title", "script", "shots", "video", "duration", "status"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        cols.append(f"{k}=?")
        vals.append(_dumps(v) if k in ("script", "shots") else v)
    if not cols:
        return
    vals.append(eid)
    with connect() as c:
        c.execute(f"UPDATE episodes SET {', '.join(cols)} WHERE id=?", vals)


if __name__ == "__main__":
    init_db()
    print("数据库初始化完成:", config.DB_PATH)