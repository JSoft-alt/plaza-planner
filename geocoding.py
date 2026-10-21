"""Two complementary free geocoders, with explicit providers and bounded use.

Census: US street addresses. Nominatim: user-triggered public places/addresses.
No autocomplete, background lookup, reverse-location upload or bulk discovery.
"""
import hashlib
import json
import math
import os
from threading import Lock
import time
import unicodedata

import requests
import store

_locks = {"census": Lock(), "nominatim": Lock()}
_next_request = {"census": 0.0, "nominatim": 0.0}
_blocked_until = {"census": 0.0, "nominatim": 0.0}


def parse_matches(payload, provider):
    if provider == "census":
        rows = payload["result"]["addressMatches"]
    else:
        rows = payload
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("地址服务返回了无效数据，请重试。")
    matches, seen = [], set()
    for row in rows[:8]:
        if not isinstance(row, dict):
            raise ValueError("地址服务返回了无效数据，请重试。")
        if provider == "census":
            lat, lng, label = row["coordinates"]["y"], row["coordinates"]["x"], row["matchedAddress"]
            precision = "街道范围估算；请核对地图中的位置"
        else:
            lat, lng, label = row["lat"], row["lon"], row["display_name"]
            precision = "地图标注位置；请核对地点和入口"
            if row.get("addresstype") in ("city", "town", "village", "county", "state", "country", "road", "postcode"):
                precision = "区域或街道的代表位置；请在地图中校正具体起点"
        if isinstance(lat, bool) or isinstance(lng, bool):
            raise ValueError("地址服务返回了无效坐标。")
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("地址服务返回了无效坐标。") from exc
        if not math.isfinite(lat) or not math.isfinite(lng) or not -85 <= lat <= 85 or not -180 <= lng <= 180:
            raise ValueError("地址服务返回了无效坐标。")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("地址服务没有返回有效地址。")
        identity = (round(lat, 7), round(lng, 7), label.casefold())
        if identity not in seen:
            seen.add(identity)
            matches.append(dict(lat=lat, lng=lng, label=label.strip()[:300], precision=precision))
    return matches


def lookup(query, provider="census"):
    if provider not in _locks:
        raise ValueError("请选择有效的地址查询服务。")
    query = " ".join(unicodedata.normalize("NFKC", query).split())
    if not 2 <= len(query) <= 300:
        raise ValueError("请输入 2–300 个字符的完整地址或公开地点名称。")
    key = hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()
    with store.db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS geocode_cache (provider TEXT, query_hash TEXT, payload TEXT NOT NULL, expires REAL NOT NULL, PRIMARY KEY(provider,query_hash))")
    # Lock the provider, recheck cache, then pace uncached requests across all users.
    with _locks[provider]:
        with store.db() as conn:
            cached = conn.execute("SELECT payload FROM geocode_cache WHERE provider=? AND query_hash=? AND expires>?",
                                  (provider, key, time.time())).fetchone()
        if cached:
            return json.loads(cached[0])
        if time.monotonic() < _blocked_until[provider]:
            raise ValueError("地址服务暂时限流，请稍后重试；也可直接在地图中放置起点。")
        wait = _next_request[provider] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress" if provider == "census"
               else os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"))
        params = ({"address": query, "benchmark": "Public_AR_Current", "format": "json"} if provider == "census"
                  else {"q": query, "format": "jsonv2", "limit": 8, "addressdetails": 1, "accept-language": "zh-CN,en"})
        _next_request[provider] = time.monotonic() + 1.1
        try:
            response = requests.get(url, params=params, timeout=(5, 20), headers={
                "User-Agent": "PlazaPlanner/1.0 (private retail visit planner; manual geocoding)",
                "Accept": "application/json"})
            if response.status_code in (403, 429):
                try:
                    delay = max(60, min(86400, int(response.headers.get("Retry-After", "60"))))
                except ValueError:
                    delay = 60
                _blocked_until[provider] = time.monotonic() + delay
                raise ValueError("地址服务暂时限流，请稍后重试；不会自动重复发送查询。")
            response.raise_for_status()
            matches = parse_matches(response.json(), provider)
        except requests.RequestException as exc:
            raise ValueError("免费地址服务暂不可用，请重试或直接在地图中放置起点。") from exc
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("地址服务返回了无效数据，请重试。") from exc
        result = dict(matches=matches, provider=provider,
                      attribution="© OpenStreetMap contributors" if provider == "nominatim" else "U.S. Census Bureau")
        with store.db() as conn:
            conn.execute("DELETE FROM geocode_cache WHERE expires<?", (time.time(),))
            conn.execute("INSERT OR REPLACE INTO geocode_cache VALUES (?,?,?,?)", (
                provider, key, store.dump(result), time.time() + (7 * 86400 if matches else 900)))
        return result
