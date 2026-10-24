"""Local OSRM durations, bounded optimization and validated road geometry."""
from functools import lru_cache
import math
import os

import requests
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from models import MAX_STOPS

EXACT_LIMIT = 12
OSRM = os.environ.get("OSRM_URL", "http://127.0.0.1:5001").rstrip("/")
SNAP_METERS = 100


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_waypoints(payload, size, keys):
    for key in keys:
        waypoints = payload.get(key)
        if not isinstance(waypoints, list) or len(waypoints) != size:
            raise ValueError("道路服务没有返回完整地点信息。")
        for waypoint in waypoints:
            if not isinstance(waypoint, dict) or not finite_number(waypoint.get("distance")) or waypoint["distance"] > SNAP_METERS:
                raise ValueError("有地点距道路超过 100 米或不在本地路网内，请核对地点与路网覆盖。")


def request_osrm(service, points, **params):
    endpoint = params.pop("_base_url", None) or OSRM
    coordinates = ";".join(f"{lng:.7f},{lat:.7f}" for lng, lat in points)
    params["radiuses"] = ";".join([str(SNAP_METERS)] * len(points))
    try:
        reply = requests.get(f"{endpoint}/{service}/v1/driving/{coordinates}", params=params, timeout=(5, 30))
        reply.raise_for_status()
        payload = reply.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValueError("本地驾驶路线服务不可用，请启动 OSRM 后重试。搜索记录已保存。") from exc
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        raise ValueError("有地点无法驾车到达或不在本地路网内，请核对地点与路网覆盖。")
    return payload


def optimize(matrix, closed=False, fixed_end=None):
    size = len(matrix)
    n = size - 1
    if not 1 <= n <= MAX_STOPS or any(len(row) != size for row in matrix):
        raise ValueError("每趟请选择 1–15 个商业区。")
    if any(not finite_number(v) for row in matrix for v in row):
        raise ValueError("驾驶时间矩阵包含不可达或无效的值。")
    if any(matrix[i][i] != 0 for i in range(size)):
        raise ValueError("驾驶时间矩阵格式无效。")
    if fixed_end is not None and (closed or type(fixed_end) is not int or not 1<=fixed_end<=n):
        raise ValueError('固定衔接终点无效。')
    costs = [[round(v * 1000) for v in row] for row in matrix]
    if n <= EXACT_LIMIT:
        @lru_cache(maxsize=None)
        def best(mask,last):
            bit=1 << (last-1)
            if mask==bit:return costs[0][last],(last,)
            prior=mask ^ bit
            return min((best(prior,p)[0]+costs[p][last],best(prior,p)[1]+(last,))
                       for p in range(1,n+1) if prior & (1 << (p-1)))
        ends=[fixed_end] if fixed_end is not None else range(1,n+1)
        _,path=min((best((1<<n)-1,last)[0]+(costs[last][0] if closed else 0),best((1<<n)-1,last)[1]) for last in ends)
        return list(path),True
    end=0 if closed else fixed_end if fixed_end is not None else size
    manager=pywrapcp.RoutingIndexManager(size if closed or fixed_end is not None else size+1,1,[0],[end])
    model=pywrapcp.RoutingModel(manager)
    def transit(a,b):
        source,target=manager.IndexToNode(a),manager.IndexToNode(b)
        return 0 if target==size else costs[source][target]
    model.SetArcCostEvaluatorOfAllVehicles(model.RegisterTransitCallback(transit))
    params=pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds=3
    solution=model.SolveWithParameters(params)
    if solution is None:raise ValueError('未找到可行路线，请减少每趟地点数后重试。')
    cursor=model.Start(0);order=[]
    while not model.IsEnd(cursor):
        node=manager.IndexToNode(cursor)
        if node:order.append(node)
        cursor=solution.Value(model.NextVar(cursor))
    if fixed_end is not None:order.append(fixed_end)
    return order,False


def calculate(config, stops, closed=False, start=None, osrm_url=None, fixed_end_id=None):
    start = dict(start or config.origin.model_dump(mode="json"))
    points = [(start["lng"], start["lat"])] + [(p["lng"], p["lat"]) for p in stops]
    table = request_osrm("table", points, annotations="duration", **({"_base_url": osrm_url} if osrm_url else {}))
    validate_waypoints(table, len(points), ("sources", "destinations"))
    matrix = table.get("durations")
    if not isinstance(matrix, list) or len(matrix) != len(points) or any(not isinstance(r, list) for r in matrix):
        raise ValueError("驾驶时间矩阵格式无效。")
    fixed_end=next((i+1 for i,p in enumerate(stops) if p["id"]==fixed_end_id),None) if fixed_end_id else None
    if fixed_end_id and fixed_end is None:raise ValueError("衔接终点不在本趟中。")
    order, optimal = optimize(matrix, closed, fixed_end=fixed_end) if fixed_end is not None else optimize(matrix,closed)
    line_points = [points[0]] + [points[i] for i in order] + ([points[0]] if closed else [])
    route = request_osrm("route", line_points, overview="full", geometries="geojson", steps="false", continue_straight="false", **({"_base_url": osrm_url} if osrm_url else {}))
    validate_waypoints(route, len(line_points), ("waypoints",))
    routes = route.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        raise ValueError("驾驶服务没有返回路线。")
    road = routes[0]
    geometry = road.get("geometry", {})
    coords = geometry.get("coordinates")
    if geometry.get("type") != "LineString" or not isinstance(coords, list) or len(coords) < 2 or any(
        not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
        or not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in coords):
        raise ValueError("驾驶路线几何信息无效。")
    legs = road.get("legs")
    if not isinstance(legs, list) or len(legs) != len(line_points) - 1 or any(
        not isinstance(leg, dict) or not finite_number(leg.get("duration")) for leg in legs
    ) or not finite_number(road.get("duration")) or not finite_number(road.get("distance")):
        raise ValueError("驾驶服务返回的分段时间无效。")
    ordered = [stops[i - 1] for i in order]
    from sharing import route_links
    return dict(order=[p["id"] for p in ordered], optimal=optimal, return_to_origin=closed,
                start=start, end=dict(start if closed else ordered[-1]),
                google_maps=route_links([start] + ordered + ([start] if closed else [])),
                duration_seconds=road["duration"], distance_meters=road["distance"], geometry=geometry,
                legs_seconds=[leg["duration"] for leg in legs],
                origin_seconds={stop["id"]: matrix[0][i + 1] for i, stop in enumerate(stops)},
                method=("连续路线 · 固定衔接点下精确优化" if fixed_end is not None else "精确最优（矩阵毫秒精度）") if optimal else "3 秒限时优化，未证明最优",
                note="基于本地路网的预计驾驶时间，不含实时交通；地点以边界内点表示，未验证入口。")
