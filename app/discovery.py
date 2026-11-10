from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

BASE_SOURCES = [
    {"id":"linkedin","name":"LinkedIn","kind":"job_board","mode":"official_integration","configured":False,"note":"Connect an approved LinkedIn integration or permitted feed."},
    {"id":"internshala","name":"Internshala","kind":"job_board","mode":"feed","configured":False,"note":"Add an authorized RSS/feed URL; no account scraping."},
    {"id":"naukri","name":"Naukri","kind":"job_board","mode":"official_integration","configured":False,"note":"Connect an approved Naukri integration or permitted feed."},
    {"id":"indeed","name":"Indeed","kind":"job_board","mode":"official_integration","configured":False,"note":"Use an approved Indeed integration or permitted feed."},
    {"id":"foundit","name":"Foundit","kind":"job_board","mode":"feed","configured":False,"note":"Add an authorized RSS/feed URL."},
    {"id":"glassdoor","name":"Glassdoor","kind":"job_board","mode":"official_integration","configured":False,"note":"Connect an approved Glassdoor integration or permitted feed."},
    {"id":"freshersworld","name":"Freshersworld","kind":"job_board","mode":"feed","configured":False,"note":"Add an authorized RSS/feed URL."},
    {"id":"company_careers","name":"Company career pages","kind":"career_pages","mode":"greenhouse_lever_or_rss","configured":False,"note":"Add public Greenhouse/Lever board tokens or an RSS feed URL."}
]

def connect_db(db_path: str):
    conn=sqlite3.connect(db_path)
    conn.row_factory=sqlite3.Row
    return conn

def init_discovery(db_path: str):
    with closing(connect_db(db_path)) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_sources(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          feed_url TEXT NOT NULL DEFAULT '', board_type TEXT NOT NULL DEFAULT '',
          board_token TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discovery_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL, status TEXT NOT NULL, found INTEGER NOT NULL DEFAULT 0,
          inserted INTEGER NOT NULL DEFAULT 0, errors_json TEXT NOT NULL DEFAULT '[]'
        );
        """)
        for s in BASE_SOURCES:
            conn.execute("""INSERT OR IGNORE INTO discovery_sources
              (id,name,enabled,feed_url,board_type,board_token,updated_at)
              VALUES(?,?,1,'','','',?)""",(s["id"],s["name"],datetime.now(timezone.utc).isoformat()))
        conn.commit()

def source_status(db_path: str) -> list[dict[str,Any]]:
    with closing(connect_db(db_path)) as conn:
        rows=conn.execute("SELECT * FROM discovery_sources ORDER BY rowid").fetchall()
    out=[]
    for row in rows:
        d=dict(row); base=next(x for x in BASE_SOURCES if x["id"]==d["id"])
        configured=bool(d["feed_url"] or (d["board_type"] and d["board_token"]))
        d.update({"enabled":bool(d["enabled"]),"configured":configured,
                  "mode":base["mode"],"kind":base["kind"],"note":base["note"]})
        d.pop("board_token",None)
        out.append(d)
    return out

def configure_source(db_path: str, source_id: str, payload: dict[str,Any]) -> dict[str,Any]:
    if source_id not in {s["id"] for s in BASE_SOURCES}:
        raise ValueError("Unknown discovery source.")
    feed=(payload.get("feed_url") or "").strip()
    if feed:
        p=urlparse(feed)
        if p.scheme!="https" or not p.netloc:
            raise ValueError("Feed URL must use HTTPS.")
    board_type=(payload.get("board_type") or "").strip().lower()
    token=(payload.get("board_token") or "").strip()
    if board_type and board_type not in {"greenhouse","lever"}:
        raise ValueError("Supported company board types: greenhouse, lever.")
    with closing(connect_db(db_path)) as conn:
        conn.execute("""UPDATE discovery_sources SET enabled=?,feed_url=?,board_type=?,
          board_token=?,updated_at=? WHERE id=?""",
          (int(bool(payload.get("enabled",True))),feed,board_type,token,
           datetime.now(timezone.utc).isoformat(),source_id))
        conn.commit()
    return next(x for x in source_status(db_path) if x["id"]==source_id)

def _entries_for(source: dict[str,Any]) -> list[dict[str,Any]]:
    urls=[]
    if source["feed_url"]:
        urls.append((source["feed_url"],source["name"]))
    if source["board_type"]=="greenhouse" and source["board_token"]:
        urls.append((f'https://boards-api.greenhouse.io/v1/boards/{source["board_token"]}/jobs?content=true',source["name"]))
    if source["board_type"]=="lever" and source["board_token"]:
        urls.append((f'https://api.lever.co/v0/postings/{source["board_token"]}?mode=json',source["name"]))
    found=[]
    with httpx.Client(timeout=15,follow_redirects=True,headers={"User-Agent":"JobPilotAI/1.0 (+job discovery; contact: admin)"}) as client:
        for url,label in urls:
            response=client.get(url); response.raise_for_status()
            if "greenhouse.io" in url:
                data=response.json()
                for j in data.get("jobs",[]):
                    found.append({"title":j.get("title",""),"company":label,"platform":"Company career pages","location":(j.get("location") or {}).get("name",""),"url":j.get("absolute_url",""),"description":j.get("content",""),"role_type":"job","posted_date":j.get("updated_at","")})
            elif "api.lever.co" in url:
                for j in response.json():
                    found.append({"title":j.get("text",""),"company":label,"platform":"Company career pages","location":", ".join((j.get("categories") or {}).get("allLocations",[])),"url":j.get("hostedUrl",""),"description":j.get("descriptionPlain",""),"role_type":"job","posted_date":""})
            else:
                parsed=feedparser.parse(response.text)
                for e in parsed.entries:
                    found.append({"title":e.get("title",""),"company":label,"platform":source["name"],"location":e.get("location",""),"url":e.get("link",""),"description":e.get("summary",""),"role_type":"internship" if "intern" in e.get("title","").lower() else "job","posted_date":e.get("published","")})
    return found

def run_discovery(db_path: str, normalize_listing, save_listing) -> dict[str,Any]:
    started=datetime.now(timezone.utc).isoformat()
    sources=source_status(db_path); found=inserted=0; errors=[]
    for s in sources:
        if not s["enabled"] or not s["configured"]:
            continue
        # Retrieve secret/config fields only inside the worker.
        with closing(connect_db(db_path)) as conn:
            raw=dict(conn.execute("SELECT * FROM discovery_sources WHERE id=?",(s["id"],)).fetchone())
        try:
            entries=_entries_for(raw)
            for entry in entries:
                if not entry.get("title") or not entry.get("url"):
                    continue
                found+=1
                try:
                    if save_listing(normalize_listing(entry)): inserted+=1
                except (ValueError,KeyError):
                    continue
        except Exception as exc:
            errors.append({"source":s["name"],"error":str(exc)[:300]})
    finished=datetime.now(timezone.utc).isoformat()
    status="partial" if errors else ("success" if found else "no_sources_or_results")
    with closing(connect_db(db_path)) as conn:
        conn.execute("INSERT INTO discovery_runs(started_at,finished_at,status,found,inserted,errors_json) VALUES(?,?,?,?,?,?)",
          (started,finished,status,found,inserted,json.dumps(errors)))
        conn.commit()
        run=dict(conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 1").fetchone())
    run["errors"]=json.loads(run.pop("errors_json"))
    return run

def run_history(db_path: str, limit: int=20) -> list[dict[str,Any]]:
    with closing(connect_db(db_path)) as conn:
        rows=conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT ?",(max(1,min(limit,100)),)).fetchall()
    return [{**dict(r),"errors":json.loads(r["errors_json"])} for r in rows]
