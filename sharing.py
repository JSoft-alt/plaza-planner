"""Direct Google-issued short links. This RPC is undocumented and may change."""
import json
import re
from threading import Lock
import time
from urllib.parse import urlsplit, urlencode

import requests
import store

_lock = Lock()


def destination_url(plaza):
    return place_url(plaza)


def place_url(plaza):
    return f"https://www.google.com/maps/place/{plaza['lat']:.7f},{plaza['lng']:.7f}"


def route_links(points):
    """Google's documented waypoint limits; adjacent segments share an endpoint.

    This is exclusively the whole-route action. Destination sharing stays a
    /maps/place URL or a verified Google-issued short link.
    """
    def segments(max_legs):
        result = []
        for start in range(0, len(points) - 1, max_legs):
            end = min(start + max_legs, len(points) - 1)
            coordinate = lambda p: f"{p['lat']:.7f},{p['lng']:.7f}"
            params = dict(api=1, origin=coordinate(points[start]), destination=coordinate(points[end]), travelmode="driving")
            if end - start > 1:
                params["waypoints"] = "|".join(coordinate(p) for p in points[start + 1:end])
            url = "https://www.google.com/maps/dir/?" + urlencode(params)
            if len(url) > 2048:
                raise ValueError("路线链接过长，请减少本趟地点数。")
            result.append(dict(url=url, from_index=start, to_index=end))
        return result
    return dict(desktop=segments(10), mobile=segments(4), legs=segments(1))


def short_link(plaza):
    target = destination_url(plaza)
    # Serialize small, user-triggered requests, and reuse a verified link forever.
    with _lock:
        with store.db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS share_links (target TEXT PRIMARY KEY, url TEXT NOT NULL)")
            cached = conn.execute("SELECT url FROM share_links WHERE target=?", (target,)).fetchone()
        if cached:
            return cached[0]
        try:
            reply = requests.get("https://www.google.com/maps/rpc/shorturl", params={
                "hl": "en", "pb": "!1s" + target.replace("!", "*21") + "!2m1!7e81!6b1"}, timeout=15)
            reply.raise_for_status()
            raw = reply.text
            if raw.startswith(")]}'"):
                raw = raw.split("\n", 1)[1]
            payload = json.loads(raw)
            link = payload[0] if isinstance(payload, list) and payload else None
            if not isinstance(link, str) or not re.fullmatch(r"https://maps\.app\.goo\.gl/[A-Za-z0-9_-]+", link):
                raise ValueError("Google 未返回有效的短链接。")
            # Google can take a moment to publish the new redirect. Never invent
            # a token or infer success from the hostname alone.
            for delay in (0, 1, 3):
                if delay:
                    time.sleep(delay)
                redirect = requests.get(link, allow_redirects=False, timeout=10)
                if redirect.status_code in (301, 302, 303, 307, 308):
                    resolved = urlsplit(redirect.headers.get("Location", ""))
                    if (resolved.scheme == "https" and resolved.hostname == "www.google.com"
                            and resolved.path == urlsplit(target).path):
                        with store.db() as conn:
                            conn.execute("INSERT OR REPLACE INTO share_links VALUES (?,?)", (target, link))
                        return link
                    raise ValueError("短链接指向的地点不匹配，未分享。")
                if redirect.status_code not in (404,):
                    break
            raise ValueError("Google 短链接暂时无法验证，请稍后重试。")
        except (requests.RequestException, ValueError, IndexError, TypeError) as exc:
            raise ValueError("无法取得并验证 Google 短链接，请重试。" +
                             (str(exc) if isinstance(exc, ValueError) else "Google 服务暂不可用。")) from exc
