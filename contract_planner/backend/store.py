import json
import os
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS memory(sid TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, sid TEXT, value TEXT);
            CREATE INDEX IF NOT EXISTS events_sid ON events(sid,id);
            CREATE TABLE IF NOT EXISTS assessments(run TEXT PRIMARY KEY, sid TEXT, result TEXT, diagnosis TEXT);
            CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY, session_id TEXT, title TEXT,
                day TEXT, start TEXT, end TEXT, notes TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY, provider TEXT, model TEXT,
                input INTEGER, output INTEGER, estimated INTEGER, at TEXT);
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def memory(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT value FROM memory WHERE sid=?", (sid,)).fetchone()
            return json.loads(row[0]) if row else {}

    def remember(self, sid, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO memory VALUES(?,?)",
                (sid, json.dumps(value, ensure_ascii=False)),
            )

    def record(self, event):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(sid,value) VALUES(?,?)",
                (event["session_id"], json.dumps(event, ensure_ascii=False)),
            )

    def logs(self, sid=None):
        with self.connect() as db:
            if sid is None:
                return [
                    dict(r)
                    for r in db.execute(
                        "SELECT sid AS session_id,count(*) AS events FROM events GROUP BY sid ORDER BY max(id) DESC"
                    )
                ]
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT value FROM events WHERE sid=? ORDER BY id", (sid,)
                )
            ]

    def assessment(self, sid, run):
        with self.connect() as db:
            row = db.execute(
                "SELECT result,diagnosis FROM assessments WHERE sid=? AND run=?",
                (sid, run),
            ).fetchone()
            if row:
                return {
                    "assessment": json.loads(row[0]),
                    "diagnosis": json.loads(row[1]) if row[1] else None,
                }

    def save_assessment(self, sid, run, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO assessments(run,sid,result) VALUES(?,?,?)",
                (run, sid, json.dumps(value)),
            )

    def save_diagnosis(self, sid, run, value):
        with self.connect() as db:
            db.execute(
                "UPDATE assessments SET diagnosis=? WHERE sid=? AND run=?",
                (json.dumps(value), sid, run),
            )

    def delete_logs(self, sid):
        with self.connect() as db:
            db.execute("DELETE FROM assessments WHERE sid=?", (sid,))
            return db.execute("DELETE FROM events WHERE sid=?", (sid,)).rowcount

    def plans(self, sid):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM plans WHERE session_id=? ORDER BY day,start,id",
                    (sid,),
                )
            ]

    def plan(self, sid, pid):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM plans WHERE session_id=? AND id=?", (sid, pid)
            ).fetchone()
            return dict(row) if row else None

    def save(self, sid, plan, pid=None):
        values = (plan.title, plan.day.isoformat(), plan.start, plan.end, plan.notes)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if pid is None:
                pid = db.execute(
                    "INSERT INTO plans(title,day,start,end,notes,session_id,created_at) VALUES(?,?,?,?,?,?,?)",
                    (*values, sid, now()),
                ).lastrowid
            elif not db.execute(
                "UPDATE plans SET title=?,day=?,start=?,end=?,notes=? WHERE id=? AND session_id=?",
                (*values, pid, sid),
            ).rowcount:
                return None
            overlaps = db.execute(
                "SELECT id,title FROM plans WHERE session_id=? AND day=? AND start<? AND end>? AND id<>?",
                (sid, values[1], plan.end, plan.start, pid),
            )
            return {
                "plan": dict(
                    db.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
                ),
                "conflicts": [dict(r) for r in overlaps],
            }

    def delete_plan(self, sid, pid):
        with self.connect() as db:
            return db.execute(
                "DELETE FROM plans WHERE id=? AND session_id=?", (pid, sid)
            ).rowcount

    def measure(self, provider, model, inp, out, estimated):
        with self.connect() as db:
            db.execute(
                "INSERT INTO usage(provider,model,input,output,estimated,at) VALUES(?,?,?,?,?,?)",
                (provider, model, inp, out, int(estimated), now()),
            )

    def reserve_gms(self, model, input_estimate, output_limit):
        with self.connect() as db:
            return db.execute(
                "INSERT INTO usage(provider,model,input,output,estimated,at) VALUES('gms',?,?,?,1,?)",
                (model, input_estimate, output_limit, now()),
            ).lastrowid

    def finish_gms(self, reservation, usage=None):
        with self.connect() as db:
            if usage is None:
                db.execute(
                    "DELETE FROM usage WHERE id=? AND provider='gms'", (reservation,)
                )
            else:
                db.execute(
                    "UPDATE usage SET input=?,output=?,estimated=0 WHERE id=? AND provider='gms'",
                    (*usage, reservation),
                )

    def usage(self):
        with self.connect() as db:
            totals = {
                r["provider"]: dict(r)
                for r in db.execute(
                    "SELECT provider,count(*) calls,sum(input) input_tokens,sum(output) output_tokens,sum(estimated) estimated_calls FROM usage GROUP BY provider"
                )
            }
        zero = dict(calls=0, input_tokens=0, output_tokens=0, estimated_calls=0)
        gms = totals.get("gms", zero)
        return {
            "anthropic": totals.get("anthropic", zero),
            "gms": gms,
            "gms_calls_enabled": bool(os.getenv("GMS_API_KEY", "").strip()),
            "gms_stop_at": None,
            "gms_remote_balance": None,
        }
