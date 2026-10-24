"""Small local measurements for labelled, non-guaranteed phase ETAs."""
import json
import math
from statistics import median
from threading import Lock
import store

_lock=Lock()


def read():
    try:
        data=json.loads((store.DATA/'phase-timings.json').read_text(encoding='utf-8'))
        if not isinstance(data,dict):return {}
        return {key:[v for v in values if type(v) in (int,float) and math.isfinite(v) and v>=0]
                for key,values in data.items() if isinstance(values,list)}
    except (OSError,ValueError):return {}


def expected(key):
    values=read().get(key,[])
    return round(median(values)*1.2) if values else None


def record(key,seconds):
    with _lock:
        data=read();data[key]=(data.get(key,[])+[round(seconds,2)])[-5:]
        try:store.atomic_json(store.DATA/'phase-timings.json',data)
        except OSError:pass  # Optional ETA history must not fail a real search.


def download_eta(done,total,elapsed):
    return round((total-done)*elapsed/done) if total and 0<done<total and elapsed>=2 else None
