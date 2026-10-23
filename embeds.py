"""Google iframe URLs. Keyless legacy embeds by default; optional official API.

These frames display Google's routes through the saved stop order; they cannot
render OSRM's arbitrary polyline. The exact OSRM preview remains separate.
"""
import os
from urllib.parse import urlencode


def coordinate(point):
    return f"{point['lat']:.7f},{point['lng']:.7f}"


def place(point):
    key=os.environ.get('GOOGLE_MAPS_EMBED_KEY','').strip()
    if key:
        return 'https://www.google.com/maps/embed/v1/place?'+urlencode(dict(key=key,q=coordinate(point),language='zh-CN'))
    return 'https://maps.google.com/maps?'+urlencode(dict(q=coordinate(point),output='embed',hl='zh-CN',z=16))


def route(points):
    result=[]
    key=os.environ.get('GOOGLE_MAPS_EMBED_KEY','').strip()
    # Same safe desktop limit as the existing Maps handoff; never omit stops.
    for start in range(0,len(points)-1,10):
        end=min(start+10,len(points)-1)
        if key:
            params=dict(key=key,origin=coordinate(points[start]),destination=coordinate(points[end]),mode='driving',language='zh-CN')
            if end-start>1:params['waypoints']='|'.join(coordinate(p) for p in points[start+1:end])
            url='https://www.google.com/maps/embed/v1/directions?'+urlencode(params)
        else:
            params=dict(output='embed',hl='zh-CN',saddr=coordinate(points[start]),
                        daddr=' to:'.join(coordinate(p) for p in points[start+1:end+1]),dirflg='d')
            url='https://maps.google.com/maps?'+urlencode(params)
        if len(url)>2048:raise ValueError('Google 路线预览链接过长，请减少地点数。')
        result.append(dict(url=url,from_index=start,to_index=end))
    return result
