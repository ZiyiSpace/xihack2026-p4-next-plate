"""FastAPI 入口：三份数据契约的 HTTP 面 + 回放 + 看板。

启动：cd backend && ../.venv/bin/uvicorn app.main:app --port 8000
文档：http://127.0.0.1:8000/docs   看板：http://127.0.0.1:8000/
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .core import HotpotService
from .replay import ReplayRunner
from .schemas import (CoversIn, DishConfigIn, OperationIn, StationEventIn,
                      TaskUpdateIn)

app = FastAPI(title="旋转小火锅 · 感知与补菜后端", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

service = HotpotService()
replay_runner = ReplayRunner(service)

_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@app.get("/")
def index():
    return FileResponse(os.path.join(_STATIC, "index.html"))


@app.get("/api/health")
def health():
    return service.health()


# ------------------------------------------------ 契约 1：站点事件


@app.post("/api/events/station")
def post_station_event(ev: StationEventIn):
    image_bytes = None
    if ev.image_b64:
        image_bytes = base64.b64decode(ev.image_b64)
    return service.ingest_station_event(ev, image_bytes)


@app.post("/api/events/station/upload")
async def post_station_event_upload(
    file: UploadFile = File(...),
    plate_id: str = Form(...),
    station_id: str = Form(...),
    observed_at: str = Form(...),
    event_id: Optional[str] = Form(None),
    net_weight_g: Optional[float] = Form(None),
    gross_weight_g: Optional[float] = Form(None),
    tare_g: Optional[float] = Form(None),
    dish_id: Optional[str] = Form(None),
    quality: str = Form("normal"),
    source: str = Form("station"),
):
    image_bytes = await file.read()
    try:
        ev = StationEventIn(
            event_id=event_id, plate_id=plate_id, station_id=station_id,
            observed_at=observed_at, net_weight_g=net_weight_g,
            gross_weight_g=gross_weight_g, tare_g=tare_g, dish_id=dish_id,
            quality=quality, source=source,
            image_b64=base64.b64encode(image_bytes).decode() if image_bytes else None,
        )
    except Exception as e:  # pydantic 校验（时间格式等）
        raise HTTPException(422, str(e)) from e
    return service.ingest_station_event(ev, image_bytes)


@app.post("/api/events/operation")
def post_operation(op: OperationIn):
    return service.ingest_operation(op)


@app.get("/api/events")
def list_events(plate_id: Optional[str] = None, limit: int = 200):
    return db.get_events(plate_id, limit)


# ------------------------------------------------ 绑定与菜品配置


@app.post("/api/bindings")
def bind(plate_id: str = Form(...), dish_id: str = Form(...),
         recorded_net_g: Optional[float] = Form(None),
         tare_g: Optional[float] = Form(None)):
    """上盘绑定 / 换菜：开启新 serving，历史不串盘。"""
    from datetime import datetime
    op = OperationIn(timestamp=datetime.now().astimezone(), plate_id=plate_id,
                     op_type="rebind", dish_id=dish_id,
                     recorded_net_g=recorded_net_g, tare_g=tare_g, source="manual")
    return service.ingest_operation(op)


@app.get("/api/dishes")
def get_dishes():
    return service.dish_summary()


@app.get("/api/dishes/config")
def get_dish_config():
    from . import db
    return db.get_dishes()


@app.put("/api/dishes/{dish_id}")
def put_dish_config(dish_id: str, d: DishConfigIn):
    if d.dish_id != dish_id:
        raise HTTPException(422, "path 与 body 的 dish_id 不一致")
    return service.upsert_dish_config(d)


@app.post("/api/dishes/{dish_id}/resume-dispatch")
def resume_dispatch(dish_id: str):
    return service.resume_dispatch(dish_id)


# ------------------------------------------------ 契约 2：状态快照


@app.get("/api/plates")
def get_plates():
    return service.plates()


@app.get("/api/plates/{plate_id}/history")
def plate_history(plate_id: str):
    from . import db
    events = [e for e in db.get_events(plate_id=plate_id, limit=1000)]
    ops = db.get_ops(plate_id=plate_id, limit=1000)
    return {"plate_id": plate_id, "events": events, "operations": ops}


# ------------------------------------------------ 契约 3：补菜任务


@app.get("/api/tasks")
def get_tasks(status: Optional[str] = None):
    return service.tasks(status)


@app.post("/api/tasks/{task_id}")
def update_task(task_id: str, upd: TaskUpdateIn):
    try:
        t = service.update_task(task_id, upd)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if t is None:
        raise HTTPException(404, f"task {task_id} 不存在")
    return t


# ------------------------------------------------ 经营分析


@app.get("/api/analytics/summary")
def analytics_summary():
    return service.summary()


@app.get("/api/analytics/waste")
def analytics_waste():
    return service.waste()


@app.post("/api/analytics/covers")
def add_covers(c: CoversIn):
    return service.add_covers(c)


# ------------------------------------------------ 回放（演示）


@app.post("/api/replay")
def start_replay(mode: str = "offline", reset: bool = True):
    if mode == "online" and service.jev.offline:
        return JSONResponse(status_code=409, content={
            "error": "online 模式需要配置 JEV_API_KEY（当前为离线规则模式）"})
    return replay_runner.start(mode=mode, reset=reset)


@app.get("/api/replay/status")
def replay_status():
    return replay_runner.status
