"""One SQLite source of truth. JSON files are recoverable, atomic exports."""
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager

DATA = Path(os.environ.get("PLANNER_DATA_DIR", Path(__file__).parent / "data")).resolve()


def uid():
    return uuid.uuid4().hex


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(DATA / "planner.sqlite3", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS scans (
            id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trips (
            id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            number INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(scan_id,number));
        CREATE TABLE IF NOT EXISTS assignments (
            scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            plaza_id TEXT NOT NULL, trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            PRIMARY KEY(scan_id,plaza_id));
        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, enabled INTEGER NOT NULL,
            next_run REAL, running TEXT, last_outcome TEXT);
        CREATE TABLE IF NOT EXISTS outreach_declines (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, recorded REAL NOT NULL);
        """)


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def insert_scan(config, schedule_id=None):
    sid = uid()
    record = dict(id=sid, created=time.time(), config=config, plazas=[], status="queued",
                  progress=None, message="等待搜索", cancel=False, schedule_id=schedule_id,
                  completed_tiles=[], export_error=None)
    with db() as conn:
        conn.execute("INSERT INTO scans VALUES (?,?,?)", (sid, record["created"], dump(record)))
    return record


def get_scan(sid):
    with db() as conn:
        row = conn.execute("SELECT payload FROM scans WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError("找不到这条搜索记录。")
        record = json.loads(row[0])
        record["trips"] = [dict(id=r[0], number=r[1], **json.loads(r[2])) for r in conn.execute(
            "SELECT id,number,payload FROM trips WHERE scan_id=? ORDER BY number", (sid,))]
    return record


def update_scan(sid, **changes):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload FROM scans WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError("找不到这条搜索记录。")
        record = json.loads(row[0])
        record.update(changes)
        conn.execute("UPDATE scans SET payload=? WHERE id=?", (dump(record), sid))


def list_scans(query="", status="", offset=0):
    with db() as conn:
        rows = conn.execute("SELECT id,payload FROM scans ORDER BY created DESC").fetchall()
        counts = dict(conn.execute("SELECT scan_id,count(*) FROM trips GROUP BY scan_id").fetchall())
    items = []
    for sid, payload in rows:
        rec = json.loads(payload)
        if query.casefold() not in rec["config"]["origin"]["label"].casefold() or (status and rec["status"] != status):
            continue
        items.append({k: rec[k] for k in ("id", "created", "config", "status", "message")}
                     | {"found": len(rec["plazas"]), "trip_count": counts.get(sid, 0)})
    return {"items": items[offset:offset + 50], "total": len(items)}


def trip_start(scan, trip):
    """Resolve a durable fixed start without changing the discovery queue."""
    if trip.get("start"):
        return trip["start"]
    if trip["number"] == 1:
        return dict(scan["config"]["origin"])
    previous = next(t for t in scan["trips"] if t["number"] == trip["number"] - 1)
    route = previous.get("route")
    if not route or (previous["number"] > 1 and not route.get("start")):
        raise ValueError("请先按顺序重新计算上一趟，保存正确的接续起点。")
    endpoint = route.get("end")
    if not endpoint:
        if route.get("return_to_origin"):
            endpoint = trip_start(scan, previous)
        elif route.get("order"):
            endpoint = next(p for p in scan["plazas"] if p["id"] == route["order"][-1])
        else:
            raise ValueError("上一趟缺少有效终点，请先重新计算。")
    return dict(endpoint, previous_trip_id=previous["id"], previous_trip_number=previous["number"])


def new_trip(sid, batch_size=None):
    if batch_size is not None and (type(batch_size) is not int or not 1 <= batch_size <= 15):
        raise ValueError('每趟须为 1–15 个新地点。')
    # Transaction and unique membership make concurrent clicks and refresh safe.
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload FROM scans WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError("找不到这条搜索记录。")
        scan = json.loads(row[0])
        if scan["status"] != "complete":
            raise ValueError("请先完成搜索或重试未完成的搜索。")
        scan["trips"] = [dict(id=r[0], number=r[1], **json.loads(r[2])) for r in conn.execute(
            "SELECT id,number,payload FROM trips WHERE scan_id=? ORDER BY number", (sid,))]
        existing = scan["trips"][-1] if scan["trips"] else None
        if existing and (not existing.get("route") or (existing["number"] > 1 and not existing["route"].get("start"))):
            return existing["id"]  # Reuse unfinished or legacy batches that need recalculation.
        assigned = {r[0] for r in conn.execute("SELECT plaza_id FROM assignments WHERE scan_id=?", (sid,))}
        queue = scan.get('route_plan',{}).get('order') or [p['id'] for p in scan['plazas']]
        if len(queue)!=len(scan['plazas']) or set(queue)!={p['id'] for p in scan['plazas']}:
            raise ValueError('保存的访问顺序不完整，请重新规划。')
        size=batch_size if batch_size is not None else scan.get('last_batch_size',scan['config']['batch_size'])
        declined={r[0] for r in conn.execute('SELECT id FROM outreach_declines')}
        by_id={p['id']:p for p in scan['plazas']}
        def eligible(pid):
            place=by_id[pid]
            if pid in declined or place.get('company_count',place.get('shop_count',2))<2:return False
            tenants=set(place.get('provenance',{}).get('eligible_ids',[]))
            minimum=max(2,scan['config'].get('min_companies' if scan['config'].get('discovery_kind')=='offices' else 'min_shops',2))
            return not tenants or len(tenants-declined)>=minimum
        chosen = [pid for pid in queue if pid not in assigned and eligible(pid)][:size]
        if not chosen:
            raise ValueError("没有剩余候选地点；已分趟、明确拒收或单商户地点会跳过。")
        tid = uid()
        number = existing["number"] + 1 if existing else 1
        start = trip_start(scan, dict(number=number))
        conn.execute("INSERT INTO trips VALUES (?,?,?,?)", (tid, sid, number,
                     dump(dict(plaza_ids=chosen, start=start, batch_size=size, route=None, error=None))))
        conn.executemany("INSERT INTO assignments VALUES (?,?,?)", [(sid, pid, tid) for pid in chosen])
        scan.pop('trips',None)
        scan['last_batch_size']=size
        conn.execute('UPDATE scans SET payload=? WHERE id=?',(dump(scan),sid))
    return tid


def outreach_declines():
    if not (DATA/'planner.sqlite3').exists():return []
    with db() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_declines'").fetchone():return []
        return [dict(r) for r in conn.execute('SELECT * FROM outreach_declines ORDER BY recorded DESC')]


def set_outreach_decline(pid, name, declined=True):
    with db() as conn:
        if declined:
            conn.execute('INSERT OR REPLACE INTO outreach_declines VALUES (?,?,?)',(pid,name,time.time()))
        else:conn.execute('DELETE FROM outreach_declines WHERE id=?',(pid,))


def update_trip(sid, tid, **changes):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload FROM trips WHERE id=? AND scan_id=?", (tid, sid)).fetchone()
        if not row:
            raise KeyError("找不到这趟行程。")
        payload = json.loads(row[0])
        if "route" in changes:
            # Check in the same write transaction as saving a route: another
            # request may have created a successor while OSRM was working.
            number = conn.execute("SELECT number FROM trips WHERE id=?", (tid,)).fetchone()[0]
            successors = conn.execute("SELECT payload FROM trips WHERE scan_id=? AND number>?", (sid, number)).fetchall()
            if any(json.loads(r[0]).get("start") for r in successors):
                raise ValueError("下一趟已接续此路线；本趟已锁定，请使用保存的路线。")
            if changes["route"] and changes["route"].get("start"):
                payload["start"] = changes["route"]["start"]
        payload.update(changes)
        conn.execute("UPDATE trips SET payload=? WHERE id=?", (dump(payload), tid))


def safe_name(record):
    label = unicodedata.normalize("NFKC", record["config"]["origin"]["label"])
    label = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", label).strip("-_")[:50] or "起点"
    size = str(record["config"]["diameter_miles"]).replace(".", "_")
    mode = "绘制区域" if record["config"]["mode"] == "polygon" else f"{size}mi"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(record["created"]))
    return f"{label}-{mode}-{stamp}-{record['id'][:10]}.json"


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.stem + "-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(dump(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def export_scan(sid):
    record = get_scan(sid)
    path = DATA / "exports" / safe_name(record)
    atomic_json(path, record)
    if record.get("export_error"):
        update_scan(sid, export_error=None)
    return path


def auto_export(sid):
    try:
        export_scan(sid)
    except (OSError, sqlite3.Error) as exc:
        update_scan(sid, export_error="自动导出失败，搜索已保存在历史中。请检查磁盘后点击导出重试。")


def delete_scans(sid=None):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT id,payload FROM scans" + (" WHERE id=?" if sid else ""), (sid,) if sid else ()).fetchall()
        if any(json.loads(row[1])["status"] in ("queued", "running") for row in rows):
            raise ValueError("请先取消正在进行的搜索，等待停止后再删除。")
        # File errors abort deletion so there is a direct retry and no orphaned exports.
        for scan_id, raw in rows:
            (DATA / "exports" / safe_name(json.loads(raw))).unlink(missing_ok=True)
            (DATA / "checkpoints" / f"{scan_id}.json").unlink(missing_ok=True)
        conn.execute("DELETE FROM scans" + (" WHERE id=?" if sid else ""), (sid,) if sid else ())


def recover():
    with db() as conn:
        for sid, raw in conn.execute("SELECT id,payload FROM scans").fetchall():
            rec = json.loads(raw)
            if rec["status"] in ("queued", "running"):
                rec.update(status="partial", progress=None, message="上次搜索被中断，可继续重试。", cancel=False)
                conn.execute("UPDATE scans SET payload=? WHERE id=?", (dump(rec), sid))
        conn.execute("UPDATE schedules SET running=NULL,last_outcome='上次运行被中断；错过的运行已跳过' WHERE running IS NOT NULL")
