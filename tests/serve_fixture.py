"""Disposable browser QA server. Synthetic destinations, separate temporary DB.

Run from project root: .venv/bin/python tests/serve_fixture.py
Never used by run.sh or the production app.
"""
import atexit
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('OSRM_AUTO_DOWNLOAD','0')
import app
import routing
import store
from models import SearchInput

temp = tempfile.TemporaryDirectory(prefix="plaza-planner-qa-")
atexit.register(temp.cleanup)
store.DATA = Path(temp.name)
store.init()
config = SearchInput(origin=dict(lat=40.758, lng=-73.9855, label="时代广场 · 界面测试起点", confirmed=True, source="address"),
                     min_shops=2,batch_size=8)
record = store.insert_scan(config.model_dump(mode="json"))
names = ["北侧商业中心（测试）", "东街购物广场（测试）", "河畔零售中心（测试）", "西区购物广场（测试）", "长名称商业中心与购物广场（测试）"]
plazas = [dict(id=f"way:{i}",name=name,address="测试街道 123 号",lat=40.758+i*.0005,lng=-73.9855+i*.0003,
               rank=i,angle=i*50,distance_miles=.5+i*.3) for i,name in enumerate((names*4)[:17],1)]
store.update_scan(record["id"],status="complete",plazas=plazas,message="界面测试数据")


# Office mode shares the same durable queue/routing contract; UI-only examples.
office_config=config.model_copy(update=dict(discovery_kind='offices',min_companies=3))
office_record=store.insert_scan(office_config.model_dump(mode='json'))
office_names=['牙科与律师办公楼（测试）','公司办公园区（测试）','医院与医疗中心（测试）']
office_plazas=[p | dict(name=office_names[(i-1)%3],company_count=3+i,entity_kind='office_zone' if i%3==2 else 'building') for i,p in enumerate(plazas[:10],1)]
store.update_scan(office_record['id'],status='complete',plazas=office_plazas,message='办公 / 医疗界面测试数据')


def fake_osrm(service, points, **params):
    n=len(points)
    if service=="table":
        return dict(code="Ok",durations=[[0 if i==j else 50+abs(i-j)*20 for j in range(n)] for i in range(n)],
                    sources=[{"distance":3}]*n,destinations=[{"distance":3}]*n)
    return dict(code="Ok",waypoints=[{"distance":3}]*n,routes=[dict(duration=70*(n-1),distance=1000*(n-1),
                geometry=dict(type="LineString",coordinates=[list(p) for p in points]),legs=[{"duration":70}]*(n-1))])


if os.environ.get('PLANNER_QA_REAL_OSRM') != '1':
    routing.request_osrm = fake_osrm
if __name__ == "__main__":
    import uvicorn
    print("DISPOSABLE UI TEST DATA ONLY; SCAN", record["id"], flush=True)
    uvicorn.run(app.app,host="127.0.0.1",port=8012)
