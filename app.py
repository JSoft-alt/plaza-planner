"""Local plaza visit planner. Run one Uvicorn worker; see README.md."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
from threading import Lock, RLock
import time
from urllib.parse import urlsplit

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

import discovery
import geocoding
from models import Origin, SearchInput, ScheduleInput, cron_trigger
import routing
import road_data
import embeds
import sharing
import store

BASE = Path(__file__).parent
# Read local settings without copying any configuration from the old app.
if (BASE / ".env").exists():
    for line in (BASE / ".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    store.DATA = Path(os.environ.get("PLANNER_DATA_DIR", store.DATA)).resolve()
    routing.OSRM = os.environ.get("OSRM_URL", routing.OSRM).rstrip("/")
    discovery.OVERPASS = os.environ.get("OVERPASS_URL", discovery.OVERPASS)
    discovery.OVERPASS_FALLBACK = os.environ.get("OVERPASS_FALLBACK_URL", discovery.DEFAULT_FALLBACK
        if discovery.OVERPASS == discovery.DEFAULT_OVERPASS else "")

TOKEN = secrets.token_urlsafe(32)
pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="discovery")
submit_lock = RLock()
route_lock = Lock()
scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
logger = logging.getLogger(__name__)


def next_run(cron, tz, now=None):
    result = cron_trigger(cron, tz).get_next_fire_time(None, datetime.fromtimestamp(now or time.time(), timezone.utc))
    return result.timestamp() if result else None


def schedules_list():
    with store.db() as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY rowid DESC").fetchall()
    return [dict(id=r["id"], **json.loads(r["payload"]), next_run=r["next_run"],
                 running=r["running"], last_outcome=r["last_outcome"]) for r in rows]


def run(sid, schedule_id=None):
    try:
        discovery.run_scan(sid)
        completed = store.get_scan(sid)
        if completed['status'] == 'complete':
            road_data.request([(completed['config']['origin']['lng'],completed['config']['origin']['lat'])]
                              + [(p['lng'],p['lat']) for p in completed['plazas']])
    finally:
        if schedule_id:
            try:
                scan = store.get_scan(sid)
                outcome = (f"完成 · {len(scan['plazas'])} 个地点" if scan["status"] == "complete" else scan["message"])
                with store.db() as conn:
                    conn.execute("UPDATE schedules SET running=NULL,last_outcome=? WHERE id=? AND running=?",
                                 (outcome, schedule_id, sid))
            except Exception:
                logger.exception("Schedule outcome could not be saved")


def schedule_tick():
    with submit_lock:
        now = time.time()
        for spec in schedules_list():
            if not spec["enabled"] or not spec["next_run"] or spec["next_run"] > now:
                continue
            future = next_run(spec["cron"], spec["timezone"], now + 1)
            with store.db() as conn:
                conn.execute("UPDATE schedules SET next_run=? WHERE id=?", (future, spec["id"]))
            if spec["running"]:
                continue  # Coalesce/skip overlapping runs, preserving the actual last outcome.
            if sum(item["status"] in ("running", "queued") for item in store.list_scans()["items"]) >= 2:
                with store.db() as conn:
                    conn.execute("UPDATE schedules SET last_outcome=? WHERE id=?", ("本次跳过：已有两个搜索运行", spec["id"]))
                continue
            scan = store.insert_scan(spec["config"], spec["id"])
            with store.db() as conn:
                conn.execute("UPDATE schedules SET running=? WHERE id=?", (scan["id"], spec["id"]))
            pool.submit(run, scan["id"], spec["id"])


@asynccontextmanager
async def lifespan(app):
    store.init()
    # A local file lock prevents two app instances sharing the scheduler database.
    lockfile = (store.DATA / ".instance.lock").open("a+")
    if os.name == "nt":
        import msvcrt
        lockfile.seek(0)
        lockfile.write("1")
        lockfile.flush()
        lockfile.seek(0)
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    store.recover()
    # No catch-up storm after downtime. Start with the next future occurrence.
    with store.db() as conn:
        for spec in schedules_list():
            future = next_run(spec["cron"], spec["timezone"], time.time() + 1) if spec["enabled"] else None
            missed = spec["next_run"] and spec["next_run"] < time.time()
            conn.execute("UPDATE schedules SET next_run=?, last_outcome=? WHERE id=?", (
                future, "停机期间错过的运行已跳过" if missed else spec["last_outcome"], spec["id"]))
    scheduler.add_job(schedule_tick, "interval", seconds=15, max_instances=1, coalesce=True, id="dispatch")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    for rec in store.list_scans()["items"]:
        if rec["status"] in ("running", "queued"):
            store.update_scan(rec["id"], cancel=True)
    pool.shutdown(wait=True)
    road_data.shutdown()
    lockfile.close()


app = FastAPI(title="巡访 · 地点行程规划", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "请求来源不匹配，请重新打开本应用。"}, status_code=403)
        if not secrets.compare_digest(request.headers.get("x-planner-csrf", ""), TOKEN):
            return JSONResponse({"detail": "页面已过期，请刷新后重试；输入会保留。"}, status_code=403)
        if int(request.headers.get("content-length", "0") or 0) > 100000:
            return JSONResponse({"detail": "请求过大，请缩小边界。"}, status_code=413)
    try:
        response = await call_next(request)
    except (sqlite3.Error, OSError):
        logger.exception("Storage failure")
        return JSONResponse({"detail": "本地存储不可用，请检查磁盘空间或权限后重试；输入已保留。"}, status_code=503)
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Referrer-Policy": "strict-origin-when-cross-origin", "X-Frame-Options": "DENY"})
    return response


@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({"detail": exc.args[0]}, status_code=404)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(RequestValidationError)
async def validation(request, exc):
    labels = {"origin": "请先确认起点。", "diameter_miles": "直径须为 0.1–30 英里。",
              "min_shops": "最少小店数须为 1–100 的整数。", "min_companies": "最少公司数须为 1–100 的整数。",
              "discovery_kind": "请选择商店或办公 / 医疗类型。", "batch_size": "每趟地点数须为 1–15 的整数。",
              "baseline": "起始角须为 0–359.99 度。", "polygon": "请绘制有效边界，最多 100 个顶点。",
              "timezone": "请填写有效时区。", "cron": "请填写有效的五段 cron 表达式。"}
    errors = {}
    for error in exc.errors():
        path = list(error["loc"])[1:]
        key = next((str(k) for k in reversed(path) if str(k) in labels), "form")
        message = error["msg"].removeprefix("Value error, ")
        for field in ("polygon", "cron"):
            if message.startswith(field + ":"):
                key, message = field, message.split(":", 1)[1].strip()
        errors[key] = message if key in ("polygon", "cron") and not message.startswith(("Input", "Field", "List", "Tuple")) else labels.get(key, "输入无效，请检查必填项和数值范围。")
    return JSONResponse({"detail": "请检查输入。", "errors": errors}, status_code=422)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/bootstrap")
def bootstrap():
    return dict(csrf=TOKEN, map_style=os.environ.get("MAP_STYLE_URL", "https://tiles.openfreemap.org/styles/liberty"),
                exact_limit=routing.EXACT_LIMIT,
                max_stops=15, timezone="America/New_York")


class AddressInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=2, max_length=300)
    provider: Literal["census", "nominatim"] = "census"


@app.post("/api/address")
def resolve_address(body: AddressInput):
    return geocoding.lookup(body.address, body.provider)


@app.post("/api/scans")
def start_scan(body: SearchInput):
    with submit_lock:
        payload = body.model_dump(mode="json")
        active = [i for i in store.list_scans()["items"] if i["status"] in ("running", "queued")]
        for rec in active:
            if rec["config"] == payload:
                return store.get_scan(rec["id"])
        if len(active) >= 2:
            raise HTTPException(409, "已有两个搜索进行中，请等待完成。")
        # Validate tiling before accepting any work.
        discovery.tiles(body)
        rec = store.insert_scan(payload)
        pool.submit(run, rec["id"])
        return rec | {"trips": []}


@app.post("/api/validate")
def validate_area(body: SearchInput):
    area, _, _ = body.area()
    discovery.tiles(body)
    return {"valid": True, "area_km2": area.area / 1000000}


@app.get("/api/scans/{sid}")
def detail(sid: str):
    return store.get_scan(sid)


@app.post("/api/scans/{sid}/cancel")
def cancel(sid: str):
    store.update_scan(sid, cancel=True)
    return {"ok": True}


@app.post("/api/scans/{sid}/retry")
def retry(sid: str):
    with submit_lock:
        rec = store.get_scan(sid)
        if rec["status"] in ("running", "queued", "complete"):
            return rec
        if len([i for i in store.list_scans()["items"] if i["status"] in ("running", "queued")]) >= 2:
            raise HTTPException(409, "已有两个搜索进行中，请稍后重试。")
        schedule_id = rec.get("schedule_id")
        if schedule_id:
            with store.db() as conn:
                spec = conn.execute("SELECT running FROM schedules WHERE id=?", (schedule_id,)).fetchone()
                if spec and spec[0] and spec[0] != sid:
                    raise HTTPException(409, "此计划已有搜索运行，请等待完成。")
                conn.execute("UPDATE schedules SET running=? WHERE id=?", (sid, schedule_id))
        store.update_scan(sid, status="queued", cancel=False, message="等待继续搜索", progress=None)
        pool.submit(run, sid, schedule_id)
    return store.get_scan(sid)


@app.post("/api/scans/{sid}/trips")
def trip(sid: str):
    store.new_trip(sid)
    store.auto_export(sid)
    return store.get_scan(sid)


class RouteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_to_origin: bool = Field(default=False, strict=True)
    retry_coverage: bool = Field(default=False, strict=True)


@app.post("/api/routing/check")
def check_routing(body: SearchInput):
    point = dict(id="probe", lat=body.origin.lat + .00001, lng=body.origin.lng)
    coverage = road_data.request([(body.origin.lng,body.origin.lat)])
    if coverage['phase'] in ('failed','cancelled'):raise ValueError(coverage['error'])
    if coverage['phase'] != 'ready':return JSONResponse({'preparing':True,'coverage':road_data.Manager.public(coverage)},status_code=202)
    with road_data.using(coverage['endpoint']):
        routing.calculate(body, [point], osrm_url=coverage['endpoint'])
    return dict(ok=True, message="本趟起点附近的 OSRM 矩阵与道路服务已连通。计算时仍会核对所有目的地的覆盖与可达性。")


@app.post("/api/scans/{sid}/trips/{tid}/route")
def route(sid: str, tid: str, body: RouteInput):
    if not route_lock.acquire(blocking=False):
        raise HTTPException(409, "另一路线正在计算，请稍候。")
    try:
        rec = store.get_scan(sid)
        current = next((t for t in rec["trips"] if t["id"] == tid), None)
        if not current:
            raise KeyError("找不到这趟行程。")
        if any(t["number"] > current["number"] and t.get("start") for t in rec["trips"]):
            raise ValueError("下一趟已接续此路线；本趟已锁定，请使用保存的路线。")
        by_id = {p["id"]: p for p in rec["plazas"]}
        start = store.trip_start(rec, current)
        stops = [by_id[i] for i in current['plaza_ids']]
        coverage = road_data.request([(start['lng'],start['lat'])]+[(p['lng'],p['lat']) for p in stops],retry=body.retry_coverage)
        if coverage['phase'] in ('failed','cancelled'):raise ValueError(coverage['error'])
        if coverage['phase'] != 'ready':return JSONResponse({'preparing':True,'coverage':road_data.Manager.public(coverage)},status_code=202)
        with road_data.using(coverage['endpoint']):
            result = routing.calculate(SearchInput.model_validate(rec["config"]), stops,
                                      body.return_to_origin, start=start, osrm_url=coverage['endpoint'])
        result['road_data'] = coverage.get('road_data') or {'region':coverage.get('region','已配置路网')}
        store.update_trip(sid, tid, route=result, error=None)
        store.auto_export(sid)
        return store.get_scan(sid)
    except ValueError as exc:
        store.update_trip(sid, tid, error=str(exc))
        raise
    finally:
        route_lock.release()


@app.post('/api/routing/prepare')
def prepare_coverage(body: Origin):
    return road_data.Manager.public(road_data.request([(body.lng,body.lat)],retry=True))


class CoverageCancelInput(BaseModel):
    job_id: str = Field(pattern='^[a-f0-9]{24}$')


@app.post('/api/routing/cancel')
def cancel_coverage(body: CoverageCancelInput):
    return road_data.manager().cancel(body.job_id)


@app.get('/api/routing/coverage')
def coverage_status():
    return road_data.status()


@app.get('/api/scans/{sid}/embeds')
def maps_embeds(sid: str, tid: str = ''):
    rec = store.get_scan(sid)
    trip = next((t for t in rec['trips'] if t['id']==tid),None)
    urls = {'origin':embeds.place(rec['config']['origin']),
            'places':{p['id']:embeds.place(p) for p in rec['plazas']}}
    if trip and trip.get('route'):
        route=trip['route']; by_id={p['id']:p for p in rec['plazas']}
        start=route.get('start') or trip.get('start') or rec['config']['origin']
        points=[start]+[by_id[pid] for pid in route['order']]+([start] if route.get('return_to_origin') else [])
        urls['route']=embeds.route(points)
    return urls


@app.post("/api/scans/{sid}/share/{pid:path}")
def share(sid: str, pid: str):
    rec = store.get_scan(sid)
    plaza = next((p for p in rec["plazas"] if p["id"] == pid), None)
    if not plaza:
        raise KeyError("找不到这个地点。")
    try:
        return {"url": sharing.short_link(plaza), "kind": "short", "message": "Google 短链接已验证"}
    except ValueError:
        # The user explicitly accepts /maps/place as an alternative to a short
        # link. Never return /maps/dir or silently open a Google sharing dialog.
        return {"url": sharing.place_url(plaza), "kind": "place", "message": "短链接暂不可用，已使用 Google 地点链接"}


@app.get("/api/history")
def history(q: str = "", status: str = "", offset: int = 0):
    return store.list_scans(q[:300], status, max(0, offset))


@app.get("/api/scans/{sid}/export")
def export(sid: str):
    path = store.export_scan(sid)
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.delete("/api/history/{sid}")
def delete(sid: str):
    store.delete_scans(sid)
    return {"ok": True}


@app.delete("/api/history")
def clear_history():
    store.delete_scans()
    return {"ok": True}


@app.get("/api/schedules")
def list_schedules():
    return {"items": schedules_list()}


@app.post("/api/schedules")
def create_schedule(body: ScheduleInput):
    sid = store.uid()
    with submit_lock, store.db() as conn:
        if conn.execute("SELECT count(*) FROM schedules").fetchone()[0] >= 30:
            raise ValueError("最多保存 30 个定时搜索。")
        conn.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?)", (sid, store.dump(body.model_dump(mode="json")),
                     int(body.enabled), next_run(body.cron, body.timezone) if body.enabled else None, None, None))
    return {"id": sid}


@app.put("/api/schedules/{sid}")
def edit_schedule(sid: str, body: ScheduleInput):
    with submit_lock, store.db() as conn:
        if not conn.execute("SELECT id FROM schedules WHERE id=?", (sid,)).fetchone():
            raise KeyError("找不到这个定时搜索。")
        conn.execute("UPDATE schedules SET payload=?,enabled=?,next_run=? WHERE id=?", (
            store.dump(body.model_dump(mode="json")), int(body.enabled),
            next_run(body.cron, body.timezone) if body.enabled else None, sid))
    return {"ok": True}


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: str):
    with submit_lock, store.db() as conn:
        row = conn.execute("SELECT running FROM schedules WHERE id=?", (sid,)).fetchone()
        if row and row[0]:
            raise ValueError("此定时搜索正在运行，请先在历史中取消搜索。")
        conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    return {"ok": True}
