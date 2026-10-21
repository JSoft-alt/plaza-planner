"""Input contracts and local metric geometry. Coordinates are always [longitude, latitude]."""
import math
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform

MILE = 1609.344
MAX_STOPS = 15


def projection(lat, lon):
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m")
    return (Transformer.from_crs(4326, crs, always_xy=True).transform,
            Transformer.from_crs(crs, 4326, always_xy=True).transform)


class Origin(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lat: float = Field(ge=-85, le=85)
    lng: float = Field(ge=-180, le=180)
    label: str = Field(min_length=1, max_length=300)
    confirmed: Literal[True]
    source: Literal["address", "device", "pin"]
    accuracy_m: float | None = Field(default=None, gt=0, le=1000000)

    @field_validator("label")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请确认起点地址。")
        return value.strip()


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    origin: Origin
    mode: Literal["circle", "polygon"] = "circle"
    diameter_miles: float = Field(default=10, ge=.1, le=30)
    polygon: list[tuple[float, float]] = Field(default_factory=list, max_length=100)
    min_shops: int = Field(default=3, ge=1, le=100, strict=True)
    batch_size: int = Field(default=5, ge=1, le=MAX_STOPS, strict=True)
    baseline: float = Field(default=0, ge=0, lt=360)

    @model_validator(mode="after")
    def valid_area(self):
        if any(not all(math.isfinite(v) for v in pt) or not -180 <= pt[0] <= 180
               or not -85 <= pt[1] <= 85 for pt in self.polygon):
            raise ValueError("polygon: 边界坐标无效。")
        if self.mode == "polygon":
            if len(set(self.polygon)) < 3:
                raise ValueError("polygon: 请画出至少三个不同顶点。")
            shape = Polygon(self.polygon)
            if not shape.is_valid:
                raise ValueError("polygon: 边界不能自相交。")
            if not shape.covers(Point(self.origin.lng, self.origin.lat)):
                raise ValueError("polygon: 起点必须位于绘制区域内或边界上，请移动起点或修改边界。")
            forward, _ = projection(self.origin.lat, self.origin.lng)
            metric = transform(forward, shape)
            if not math.isfinite(metric.area) or not 100 <= metric.area <= 2000e6:
                raise ValueError("polygon: 面积须为 100 平方米至 2000 平方公里。")
            x0, y0, x1, y1 = metric.bounds
            if max(x1 - x0, y1 - y0) > 80000 or metric.distance(Point(0, 0)) > 200000:
                raise ValueError("polygon: 边界跨度不能超过 80 公里，且须在起点 200 公里内。")
            # Dateline-spanning polygons need explicit spherical handling; reject them.
            if shape.bounds[2] - shape.bounds[0] > 180:
                raise ValueError("polygon: 暂不支持跨越日期变更线的边界。")
        return self

    def area(self):
        forward, inverse = projection(self.origin.lat, self.origin.lng)
        if self.mode == "polygon":
            return transform(forward, Polygon(self.polygon)), forward, inverse
        return Point(0, 0).buffer(self.diameter_miles * MILE / 2, quad_segs=96), forward, inverse


def sweep(plazas, config):
    forward, _ = projection(config.origin.lat, config.origin.lng)
    for plaza in plazas:
        x, y = forward(plaza["lng"], plaza["lat"])
        plaza["distance_miles"] = math.hypot(x, y) / MILE
        # Projection roundoff must not turn exact north into 359.999999999°.
        plaza["angle"] = round((math.degrees(math.atan2(x, y)) - config.baseline) % 360, 7) % 360
    plazas.sort(key=lambda p: (round(p["angle"], 9), p["distance_miles"], p["id"]))
    for rank, plaza in enumerate(plazas, 1):
        plaza["rank"] = rank
    return plazas


def cron_trigger(expression, timezone):
    """Five-field cron; numeric weekdays follow UNIX Sunday=0 (also 7)."""
    ZoneInfo(timezone)
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("请填写五段 cron：分 时 日 月 周。")
    if fields[4] != "*":
        days = set()
        names = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
        def day_number(value):
            number = int(value) if value.isdigit() else names.index(value)
            if not 0 <= number <= 7:
                raise ValueError("星期须为 0–7 或 mon–sun。")
            return number
        for item in fields[4].lower().split(","):
            base, *step_part = item.split("/")
            step = int(step_part[0]) if len(step_part) == 1 else 1
            if len(step_part)>1 or step < 1 or step > 7:
                raise ValueError("星期步长须为 1–7。")
            if base == "*":
                start, end = 0, 6
            elif "-" in base:
                a, b = base.split("-")
                start, end = day_number(a), day_number(b)
            else:
                start = end = day_number(base)
            if start > end:
                raise ValueError("星期范围起点不能晚于终点。")
            days.update(names[number % 7] for number in range(start, end + 1, step))
        fields[4] = ",".join(sorted(days))
    return CronTrigger.from_crontab(" ".join(fields), timezone=ZoneInfo(timezone))


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    config: SearchInput
    cron: str = Field(min_length=9, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_cron(self):
        from datetime import datetime, timezone
        try:
            trigger = cron_trigger(self.cron, self.timezone)
            if trigger.get_next_fire_time(None, datetime.now(timezone.utc)) is None:
                raise ValueError("表达式没有未来执行时间。")
        except Exception as exc:
            raise ValueError(f"cron: 定时设置无效：{exc}") from exc
        return self
