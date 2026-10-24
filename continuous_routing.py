"""A bounded whole-area road-time plan, split later into continuous batches.

Uses small OSRM table blocks (works with the default 100-location limit),
then one second of global heuristic improvement. No exhaustive global TSP.
Locations outside the current road graph remain in a clearly flagged suffix.
"""
from concurrent.futures import ThreadPoolExecutor
import math
import time

import requests
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

import routing

MAX_LOCATIONS = 500


def nearest(point, endpoint):
    try:
        response=requests.get(f'{endpoint}/nearest/v1/driving/{point[0]},{point[1]}',params={'number':1},timeout=(2,3))
        response.raise_for_status();payload=response.json()
        waypoint=payload.get('waypoints',[{}])[0]
        return payload.get('code')=='Ok' and routing.finite_number(waypoint.get('distance')) and waypoint['distance']<=routing.SNAP_METERS
    except (requests.RequestException,ValueError,TypeError,IndexError):
        return False


def matrix_blocks(points, endpoint):
    size=len(points);matrix=[[None]*size for _ in points];blocks=[list(range(i,min(i+45,size))) for i in range(0,size,45)]
    def block(pair):
        sources,destinations=pair
        indices=list(dict.fromkeys(sources+destinations));local={index:i for i,index in enumerate(indices)}
        reply=routing.request_osrm('table',[points[i] for i in indices],annotations='duration',
            sources=';'.join(str(local[i]) for i in sources),destinations=';'.join(str(local[i]) for i in destinations),_base_url=endpoint)
        routing.validate_waypoints(reply,len(sources),['sources'])
        routing.validate_waypoints(reply,len(destinations),['destinations'])
        rows=reply.get('durations')
        if not isinstance(rows,list) or len(rows)!=len(sources) or any(not isinstance(row,list) or len(row)!=len(destinations) for row in rows):
            raise ValueError('全区域驾驶时间矩阵不完整，请重试。')
        return sources,destinations,rows
    with ThreadPoolExecutor(max_workers=4) as workers:
        for sources,destinations,rows in workers.map(block,[(a,b) for a in blocks for b in blocks]):
            for i,source in enumerate(sources):
                for j,destination in enumerate(destinations):matrix[source][destination]=rows[i][j]
    return matrix


def solve(matrix, finish=None):
    size=len(matrix)
    if size==1:return []
    costs=[[round(v*1000) for v in row] for row in matrix]
    end=finish if finish is not None else size
    manager=pywrapcp.RoutingIndexManager(size if finish is not None else size+1,1,[0],[end])
    model=pywrapcp.RoutingModel(manager)
    def transit(a,b):
        source,target=manager.IndexToNode(a),manager.IndexToNode(b)
        return 0 if target==size else costs[source][target]
    model.SetArcCostEvaluatorOfAllVehicles(model.RegisterTransitCallback(transit))
    parameters=pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.seconds=1
    solution=model.SolveWithParameters(parameters)
    if not solution:raise ValueError('未找到连续路线，请检查地点与路网。')
    result=[];cursor=model.Start(0)
    while not model.IsEnd(cursor):
        index=manager.IndexToNode(cursor)
        if index:result.append(index)
        cursor=solution.Value(model.NextVar(cursor))
    if finish is not None:result.append(finish)
    return result


def geographic_suffix(points, start):
    remaining=list(points);ordered=[];current=start
    scale=math.cos(math.radians(start['lat']))
    def distance(a,b):return ((a['lng']-b['lng'])*scale)**2+(a['lat']-b['lat'])**2
    while remaining:
        closest=min(range(len(remaining)),key=lambda i:(distance(current,remaining[i]),remaining[i]['id']))
        current=remaining.pop(closest);ordered.append(current)
    return ordered


def build(scan, endpoint):
    started=time.monotonic();places=scan['plazas'];origin=scan['config']['origin']
    if not places:raise ValueError('没有可规划的地点。')
    if len(places)>MAX_LOCATIONS:raise ValueError('快速全区域路线目前最多 500 个地点，请缩小搜索范围。')
    all_points=[origin]+places
    with ThreadPoolExecutor(max_workers=4) as workers:
        covered=list(workers.map(lambda p:nearest((p['lng'],p['lat']),endpoint),all_points))
    if not covered[0]:raise ValueError('起点未在当前可驾驶路网内，请先准备路网或校正起点。')
    known=[p for p,valid in zip(places,covered[1:]) if valid]
    pending=[p for p,valid in zip(places,covered[1:]) if not valid]
    points=[(p['lng'],p['lat']) for p in [origin]+known]
    matrix=matrix_blocks(points,endpoint)
    usable=[0]+[i for i in range(1,len(points)) if routing.finite_number(matrix[0][i]) and routing.finite_number(matrix[i][0])]
    pending += [known[i-1] for i in range(1,len(points)) if i not in usable]
    known=[known[i-1] for i in usable[1:]]
    matrix=[[matrix[i][j] for j in usable] for i in usable]
    if any(not routing.finite_number(v) for row in matrix for v in row):raise ValueError('部分地点的驾驶道路不连通，请核对路网后重试。')
    finish=None
    if pending and known:
        # Finish the covered region near the uncovered geographic group, rather
        # than repeatedly bouncing between graph-covered and uncovered areas.
        center=dict(lat=sum(p['lat'] for p in pending)/len(pending),lng=sum(p['lng'] for p in pending)/len(pending))
        finish=min(range(1,len(known)+1),key=lambda i:(known[i-1]['lat']-center['lat'])**2+(known[i-1]['lng']-center['lng'])**2)
    order=[known[i-1] for i in solve(matrix,finish)]
    order += geographic_suffix(pending,order[-1] if order else origin)
    ids=[p['id'] for p in order]
    if len(ids)!=len(places) or set(ids)!={p['id'] for p in places}:raise ValueError('连续路线地点校验失败，请重试。')
    return dict(mode='continuous',order=ids,optimal=False,method='全区域快速连续优化（未证明全局最优）',
        pending_road_ids=[p['id'] for p in pending],seconds=round(time.monotonic()-started,2),created=time.time())
