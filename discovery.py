"""Bounded Overpass discovery with resumable tiles and evidence-based grouping."""
import json
import hashlib
from functools import lru_cache
import math
import os
from pathlib import Path
from email.utils import parsedate_to_datetime
import re
import time
import unicodedata
from urllib.parse import urlsplit

import requests
from shapely.geometry import Point, Polygon, LineString, box, shape as geo_shape
from shapely.ops import polygonize, transform, unary_union
from shapely.strtree import STRtree

from models import SearchInput, sweep
import store

RULES = Path(__file__).with_name("retail_rules.json")
OFFICE_RULES = Path(__file__).with_name("office_rules.json")
DEFAULT_OVERPASS = "https://overpass.private.coffee/api/interpreter"
DEFAULT_FALLBACK = "https://overpass-api.de/api/interpreter"
OVERPASS = os.environ.get("OVERPASS_URL", DEFAULT_OVERPASS)
OVERPASS_FALLBACK = os.environ.get("OVERPASS_FALLBACK_URL", DEFAULT_FALLBACK if OVERPASS == DEFAULT_OVERPASS else "")
_blocked_until = 0


def normalized(text):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


@lru_cache(maxsize=64)
def name_pattern(words):
    terms = [re.escape(normalized(word)) for word in words if normalized(word)]
    return re.compile(r'(?<!\w)(?:'+'|'.join(terms)+r')(?!\w)') if terms else None


def name_matches(name, words):
    pattern = name_pattern(tuple(words))
    return bool(pattern and pattern.search(name))


def category(tags, rules):
    name = " | ".join(normalized(tags[key]) for key in ("name", "brand", "operator") if tags.get(key))
    if name_matches(name, rules["excluded_names_or_brands"]):
        return None
    if tags.get("shop") in rules["excluded_shops"] or tags.get("amenity") in rules["excluded_amenities"]:
        return None
    for item in rules["categories"]:
        matches = any(set(tags.get(key, "").lower().split(";")) & set(values) for key, values in item.get("tags", {}).items())
        matches |= name_matches(name,item.get("name_words", []))
        matches |= any(tags.get(key, "").strip().lower() not in excluded and bool(tags.get(key, "").strip())
                       for key, excluded in item.get("nonempty_tags", {}).items())
        if matches:
            if item["id"] in rules.get("independent_categories", []) and any(tags.get(k) for k in ("brand", "brand:wikidata", "operator:wikidata")):
                continue
            return item["id"]
    return None


def geometry(element):
    if element.get("geojson"):
        return geo_shape(element["geojson"])
    if element["type"] == "node":
        return Point(element["lon"], element["lat"])
    if element["type"] == "way":
        pts = [(pt["lon"], pt["lat"]) for pt in element.get("geometry", [])]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            shape = Polygon(pts)
            return shape if shape.is_valid else None
        return LineString(pts) if len(pts) >= 2 else None
    if element["type"] == "relation":
        outer, inner = [], []
        for member in element.get("members", []):
            pts = [(pt["lon"], pt["lat"]) for pt in member.get("geometry", [])]
            if len(pts) >= 2:
                (inner if member.get("role") == "inner" else outer).append(LineString(pts))
        shape = unary_union(list(polygonize(outer)))
        if inner:
            shape = shape.difference(unary_union(list(polygonize(inner))))
        return shape if shape.is_valid and not shape.is_empty else None


def address(tags):
    if tags.get("addr:full"):
        return tags["addr:full"]
    street = " ".join(tags.get(k, "") for k in ("addr:housenumber", "addr:street")).strip()
    return ", ".join(v for v in (street, tags.get("addr:city"), tags.get("addr:state"), tags.get("addr:postcode")) if v)


def excluded_destination(tags, metric, rules, children=False):
    policy = rules.get('destination_policy', {})
    name = ' | '.join(normalized(tags.get(key,'')) for key in ('name','brand','operator'))
    if name_matches(name,policy.get('excluded_names_or_brands', [])):
        return True
    mall = tags.get('shop') == 'mall' or tags.get('building') in ('shopping_centre','shopping_center')
    try:levels=float(tags.get('building:levels','0'))
    except ValueError:levels=0
    big_mall = mall and (metric.area > policy.get('max_mall_area_m2',float('inf')) or levels > policy.get('max_mall_levels',float('inf')))
    return big_mall or (not children and metric.area > policy.get('max_site_area_m2',float('inf')))


def qualify(elements, config, rules=None):
    offices = config.discovery_kind == "offices"
    rules = rules or json.loads((OFFICE_RULES if offices else RULES).read_text(encoding="utf-8"))
    rules_hash = hashlib.sha256(store.dump(rules).encode()).hexdigest()
    area, forward, inverse = config.area()
    shops, outlines, excluded_areas = [], [], []
    for element in elements:
        tags = element.get("tags", {})
        kind = category(tags, rules)
        is_mall = tags.get("shop") == "mall" or tags.get("building") in ("shopping_centre", "shopping_center")
        office_outline = tags.get("building", "no") not in ("no", "roof", "construction", "ruins") or tags.get("landuse") == "commercial" or bool(kind)
        potential_outline = office_outline if offices else (is_mall or tags.get('landuse')=='retail' or tags.get('building')=='retail')
        named_blocker = not offices and element['type']!='node' and name_matches(' | '.join(normalized(tags[k]) for k in ('name','brand','operator') if tags.get(k)),rules.get('destination_policy',{}).get('excluded_names_or_brands',[]))
        if not kind and not potential_outline and not named_blocker:
            continue  # Filter tags before constructing/projecting irrelevant geometry.
        shape = geometry(element)
        if shape is None or shape.is_empty:
            continue
        metric = transform(forward, shape)
        if metric.is_empty or not metric.is_valid:
            continue
        pid = f"{element['type']}:{element['id']}"
        if not offices and metric.geom_type in ('Polygon','MultiPolygon') and excluded_destination(tags,metric,rules):
            if metric.geom_type in ('Polygon','MultiPolygon') and excluded_destination(tags,metric,rules,children=True):
                excluded_areas.append(metric)
            continue
        if kind:
            shops.append(dict(id=pid, tags=tags, category=kind, point=metric.representative_point(), geometry=metric))
        if metric.geom_type in ("Polygon", "MultiPolygon") and potential_outline:
            point = metric.representative_point()
            if area.covers(point):
                priority = (2 if tags.get("landuse") == "commercial" else 0 if tags.get("building") else 1) if offices else (0 if is_mall else 1 if tags.get("landuse") == "retail" else 2)
                outlines.append(dict(id=pid, tags=tags, geometry=metric, point=point, priority=priority))
    if not offices and excluded_areas:
        blocked = STRtree(excluded_areas)
        shops = [shop for shop in shops if not any(excluded_areas[int(i)].covers(shop['point']) for i in blocked.query(shop['point']))]
    # Nodes and building footprints often describe the same storefront. Use a
    # spatial index; matching name OR containment of an unnamed feature is needed.
    shops.sort(key=lambda s: (not bool(s["tags"].get("name")), s["id"]))
    tree = STRtree([s["geometry"] if offices else s["point"] for s in shops])
    duplicate = set()
    for i, shop in enumerate(shops):
        if i in duplicate:
            continue
        for j in tree.query((shop["geometry"] if offices else shop["point"]).buffer(15)):
            j = int(j)
            if j <= i or j in duplicate:
                continue
            other = shops[j]
            same_name = normalized(shop["tags"].get("name", "")) == normalized(other["tags"].get("name", ""))
            unnamed_overlap = (not other["tags"].get("name") or not shop["tags"].get("name")) and shop["geometry"].intersects(other["geometry"])
            if offices:
                # Never merge distinct tenants merely because they share a category,
                # address, operator or building. Match duplicated POI/footprint names.
                identity = same_name and bool(normalized(shop["tags"].get("name", "")))
                same_unit = all(not (shop["tags"].get(k) and other["tags"].get(k)) or shop["tags"][k] == other["tags"][k]
                                for k in ("addr:unit", "level"))
                duplicate_company = identity and same_unit and shop["geometry"].distance(other["geometry"]) <= 15
            else:
                duplicate_company = shop["category"] == other["category"] and shop["point"].distance(other["point"]) <= 15 and (same_name or unnamed_overlap)
            if duplicate_company:
                duplicate.add(j)
    shops = [s for i, s in enumerate(shops) if i not in duplicate]
    tree = STRtree([s["point"] for s in shops])
    candidates = []
    for outline in outlines:
        if not offices and any(zone.covers(outline['point']) for zone in excluded_areas):
            continue
        inside = [shops[int(i)] for i in tree.query(outline["geometry"]) if (offices or shops[int(i)]["id"] != outline["id"])
                  and outline["geometry"].covers(shops[int(i)]["point"])]
        if len(inside) >= (config.min_companies if offices else config.min_shops):
            candidates.append(outline | {"shops": inside, "ids": {s["id"] for s in inside}})
    if offices and config.min_companies == 1:
        # A mapped standalone practice/hospital is useful even without a building
        # footprint. It is one organization, never a fabricated nearby cluster.
        outline_tree = STRtree([o["geometry"] for o in outlines])
        for company in shops:
            if area.covers(company["point"]) and not any(outlines[int(i)]["geometry"].covers(company["point"]) for i in outline_tree.query(company["point"])):
                candidates.append(company | dict(priority=3, shops=[company], ids={company["id"]}))
    candidates.sort(key=lambda c: (c["priority"], -len(c["ids"]), -c["geometry"].area, c["id"]))
    kept = []
    for candidate in candidates:
        if any(len(candidate["ids"] & old["ids"]) / min(len(candidate["ids"]), len(old["ids"])) >= .8
               or (min(candidate["geometry"].area, old["geometry"].area) > 0 and candidate["geometry"].intersection(old["geometry"]).area / min(candidate["geometry"].area, old["geometry"].area) >= .8)
               for old in kept):
            continue
        kept.append(candidate)
    plazas = []
    for item in kept:
        lng, lat = inverse(item["point"].x, item["point"].y)
        name = item["tags"].get("name") or item["tags"].get("brand")
        plaza_address = address(item["tags"])
        if not plaza_address:
            # A common street is useful, but never infer a missing house number.
            streets = {s["tags"].get("addr:street") for s in item["shops"]} - {None, ""}
            if len(streets) == 1:
                plaza_address = next(iter(streets))
        entity_kind = ("office_zone" if item["tags"].get("landuse") == "commercial" else "building" if item["tags"].get("building") else "facility") if offices else "retail"
        fallback_name = {"office_zone": "未命名办公区", "building": "未命名办公 / 医疗楼", "facility": "未命名机构", "retail": "未命名商业区"}[entity_kind]
        plazas.append(dict(id=item["id"], name=name or fallback_name, address=plaza_address or "地址未映射",
                           lat=lat, lng=lng, **({"company_count": len(item["shops"]), "entity_kind": entity_kind} if offices else {"shop_count": len(item["shops"])}),
                           provenance={"source": "OpenStreetMap", "outline": item["id"],
                                       "eligible_ids": sorted(item["ids"]), "rules_hash": rules_hash,
                                       "destination": "已映射机构位置或边界内点；并非已验证入口" if offices else "商业区边界内点；并非已验证入口"}))
    return sweep(plazas, config)


def tiles(config):
    area, _, inverse = config.area()
    x0, y0, x1, y1 = area.bounds
    result = []
    columns, rows = max(1, math.ceil((x1-x0)/12000)), max(1, math.ceil((y1-y0)/12000))
    for x in range(columns):
        for y in range(rows):
            tile = box(x0+x*(x1-x0)/columns, y0+y*(y1-y0)/rows,
                       x0+(x+1)*(x1-x0)/columns, y0+(y+1)*(y1-y0)/rows)
            if not tile.intersects(area):
                continue
            west, south, east, north = transform(inverse, tile.intersection(area.envelope).buffer(500)).bounds
            if east - west > 180:
                raise ValueError("搜索范围跨越日期变更线，请缩小范围。")
            result.append((south, west, north, east))
    if len(result) > 64:
        raise ValueError("搜索范围过大，请缩小范围。")
    return result


def fetch_tile(bounds, on_activity=None, cancelled=None, discovery_kind="retail"):
    global _blocked_until
    def activity(**event):
        if on_activity:
            on_activity(**event)

    def check_cancelled():
        if cancelled and cancelled():
            raise ValueError("搜索已取消；已取得的结果已保存。")

    check_cancelled()
    bbox = ",".join(f"{v:.7f}" for v in bounds)
    selectors = ['["shop"]', '["building"~"^(retail|shopping_centre|shopping_center)$"]',
                 '["landuse"="retail"]', '["amenity"~"^(restaurant|cafe|fast_food|pharmacy|car_wash)$"]',
                 '["sport"]', '["healthcare"="acupuncture"]']
    if discovery_kind == "offices":
        selectors = ['["office"]', '["healthcare"]', '["amenity"~"^(hospital|clinic|dentist|doctors)$"]',
                     '["building"]["building"!="no"]', '["landuse"="commercial"]']
    query = '[out:json][timeout:20][maxsize:33554432];(' + "".join(f"nwr{sel}({bbox});" for sel in selectors) + ');out geom;'
    cache_key = hashlib.sha256((OVERPASS + query).encode()).hexdigest()
    cache_file = store.DATA / "tile-cache" / (cache_key + ".json")
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            age = time.time() - cached["fetched_at"]
            if 0 <= age < 900 and isinstance(cached.get("elements"), list):
                activity(phase="cache", provider="本地缓存", age_seconds=round(age), elements=len(cached["elements"]))
                return cached["elements"]
        except (ValueError, KeyError, TypeError, OSError):
            pass  # A damaged optional cache must not block live discovery.
    if time.time() < _blocked_until:
        raise ValueError(f"地图服务正在限流或限制访问，请在 {math.ceil(_blocked_until-time.time())} 秒后重试；可先打开已保存的历史。")
    endpoints = list(dict.fromkeys(url for url in (OVERPASS, OVERPASS_FALLBACK) if url))
    for attempt, endpoint in enumerate(endpoints):
        check_cancelled()
        activity(phase="query", provider=urlsplit(endpoint).hostname, attempt=attempt + 1, attempts=len(endpoints))
        try:
            with requests.post(endpoint, data={"data": query}, timeout=(5, 25), stream=True,
                               headers={"User-Agent": "PlazaPlanner/1.0 (private retail visit planner)"}) as response:
                if response.status_code in (403, 429):
                    value = response.headers.get("Retry-After", "")
                    delay = 60 if response.status_code == 429 else 300
                    try:
                        delay = max(delay, int(value) if str(value).isdigit() else parsedate_to_datetime(value).timestamp() - time.time())
                    except (ValueError, TypeError, AttributeError, OverflowError):
                        pass
                    _blocked_until = max(_blocked_until, time.time() + delay)
                    raise ValueError(f"地图数据服务返回 {response.status_code}，正在限流或限制访问，请至少 {math.ceil(delay)} 秒后重试；不会切换服务绕过限制。")
                response.raise_for_status()
                chunks, size = [], 0
                activity(phase="download", provider=urlsplit(endpoint).hostname, bytes=0)
                reported = time.monotonic()
                for chunk in response.iter_content(8192):
                    check_cancelled()
                    size += len(chunk)
                    if size > 40 * 1024 * 1024:
                        raise ValueError("该区域数据过多，请缩小范围。")
                    chunks.append(chunk)
                    if time.monotonic() - reported >= 1:
                        activity(phase="download", provider=urlsplit(endpoint).hostname, bytes=size)
                        reported = time.monotonic()
            break
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if attempt == len(endpoints)-1 or (status is not None and status not in (500, 502, 503, 504)):
                raise
            check_cancelled()
    payload = json.loads(b"".join(chunks))
    if payload.get("remark") or not isinstance(payload.get("elements"), list):
        raise ValueError("地图数据未完整返回，请稍后重试或缩小范围。")
    if any(not isinstance(e, dict) or e.get("type") not in ("node", "way", "relation") or "id" not in e for e in payload["elements"]):
        raise ValueError("地图数据格式无效，请重试。")
    check_cancelled()
    try:
        store.atomic_json(cache_file, dict(fetched_at=time.time(), elements=payload["elements"]))
        # Bound this disposable cache; scan history/checkpoints are separate.
        entries = sorted(cache_file.parent.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in entries[64:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # SQLite and per-scan checkpoints remain authoritative.
    return payload["elements"]


def run_scan(sid):
    started = time.monotonic()
    try:
        scan = store.get_scan(sid)
        config = SearchInput.model_validate(scan["config"])
        rules = scan.get("qualification_rules") or json.loads((OFFICE_RULES if config.discovery_kind == "offices" else RULES).read_text(encoding="utf-8"))
        if config.discovery_kind == 'retail':
            # A retry adopts the user's current destination exclusions. Completed
            # historical scans/routes remain untouched until a new search/retry.
            rules = rules | {'destination_policy':json.loads(RULES.read_text(encoding='utf-8')).get('destination_policy',{})}
        store.update_scan(sid, qualification_rules=rules)
        if os.environ.get('DISCOVERY_SOURCE', 'local') == 'local':
            import local_discovery
            def activity(**event):
                prior = store.get_scan(sid).get('activity') or {}
                now = time.time()
                same = prior.get('phase') == event['phase'] and prior.get('provider') == event.get('provider')
                store.update_scan(sid, activity=event | dict(updated_at=now,
                    phase_started_at=prior.get('phase_started_at', now) if same else now))
            store.update_scan(sid, status='running', run_started_at=time.time(), progress=None,
                              message='读取本机地图数据')
            last_plazas = None
            def partial_results(elements, done, total):
                nonlocal last_plazas
                last_plazas = qualify(elements,config,rules)
                store.update_scan(sid, plazas=last_plazas,
                    completed_tiles=list(range(done)),progress={'done':done,'total':total})
            elements, source = local_discovery.search(config, rules, activity,
                cancelled=lambda: store.get_scan(sid)['cancel'],on_results=partial_results)
            activity(phase='filter', elements=len(elements))
            plazas = last_plazas if last_plazas is not None else qualify(elements, config, rules)
            if store.get_scan(sid)['cancel']:
                raise ValueError('搜索已取消；已保存的数据保留。')
            store.update_scan(sid, status='complete', plazas=plazas, data_source=source,
                completed_tiles=list(range(len(source['regions']))), progress=None, message='本机地图搜索完成',
                completed=time.time(), elapsed_seconds=round(time.monotonic()-started,1))
            store.auto_export(sid)
            return
        checkpoint = store.DATA / "checkpoints" / f"{sid}.json"
        cached = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"elements": {}, "done": []}
        work = tiles(config)
        # Old scans used a grid anchored on the origin. Keep their recoverable
        # evidence, but re-fetch the new task plan instead of misreading indexes.
        if cached.get("bounds") != [list(bounds) for bounds in work]:
            cached.update(done=[], bounds=[list(bounds) for bounds in work])
        store.update_scan(sid, status="running", run_started_at=time.time(), message="读取区域数据",
                          activity=dict(phase="prepare", phase_started_at=time.time(), updated_at=time.time()),
                          progress={"done": len(cached["done"]), "total": len(work)})
        def report(**event):
            prior = store.get_scan(sid).get("activity") or {}
            same = prior.get("phase") == event["phase"] and prior.get("provider") == event.get("provider")
            now = time.time()
            store.update_scan(sid, activity=event | dict(updated_at=now,
                phase_started_at=prior.get("phase_started_at", now) if same else now))
        for index, bounds in enumerate(work):
            if store.get_scan(sid)["cancel"]:
                store.update_scan(sid, status="cancelled", progress=None, message="搜索已取消；已取得的结果已保存。")
                store.auto_export(sid)
                return
            if index in cached["done"]:
                continue
            if time.monotonic() - started > 600:
                raise ValueError("本次搜索已达 10 分钟，请重试以继续剩余区域。")
            store.update_scan(sid, message=f"读取区域 {len(cached['done']) + 1} / {len(work)}")
            for element in fetch_tile(bounds, on_activity=report, cancelled=lambda: store.get_scan(sid)["cancel"], discovery_kind=config.discovery_kind):
                cached["elements"][f"{element['type']}:{element['id']}"] = element
            if len(cached["elements"]) > 100000:
                raise ValueError("区域包含过多地图要素，请缩小范围。")
            cached["done"].append(index)
            store.atomic_json(checkpoint, cached)
            report(phase="filter", elements=len(cached["elements"]))
            store.update_scan(sid, message="核对办公 / 医疗机构与去重" if config.discovery_kind == "offices" else "核对商业区与去重", progress=None)
            plazas = qualify(cached["elements"].values(), config, rules)
            store.update_scan(sid, plazas=plazas, completed_tiles=cached["done"],
                              progress={"done": len(cached["done"]), "total": len(work)})
            if len(cached["done"]) < len(work):
                time.sleep(1)  # No pointless delay after the final region.
        if store.get_scan(sid)["cancel"]:
            store.update_scan(sid, status="cancelled", progress=None, message="搜索已取消；已取得的结果已保存。")
        else:
            store.update_scan(sid, status="complete", progress=None, message="搜索完成", completed=time.time(),
                              elapsed_seconds=round(time.monotonic() - started, 1))
        store.auto_export(sid)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Discovery failed for %s", sid)
        try:
            rec = store.get_scan(sid)
            message = str(exc) if isinstance(exc, ValueError) else "地图服务响应超时，请点击重试；已完成区域不会重复查询。" if isinstance(exc, requests.Timeout) else "地图数据服务连接失败或繁忙，请点击重试；已完成区域已保存。"
            if rec["cancel"]:
                store.update_scan(sid, status="cancelled", message="搜索已取消；已取得的结果已保存。", progress=None)
            else:
                store.update_scan(sid, status="partial" if rec["completed_tiles"] else "failed", message=message, progress=None)
            store.auto_export(sid)
        except Exception:
            logging.getLogger(__name__).exception("Could not persist failure state")
