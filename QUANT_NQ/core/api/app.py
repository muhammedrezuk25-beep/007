"""
Core.api.app
=============
طبقة REST + WebSocket + Dashboard (Web) المذكورة في مواصفات المشروع
(الواجهات: Dashboard؛ البروتوكولات: WebSocket, REST API, JSON).

قراءة فقط: تعرض حالة Registry/EventBus/Metrics/Journal للمراقبة، ولا
تتحكم بالذرات ولا تحتوي أي منطق أعمال يتجاوز عرض الحالة (Article 30 —
Core مسؤول عن التشغيل فقط). لا اسم ولا رقم ذرة مكتوب هنا بالكود؛ كل ما
يُعرض يأتي من Registry ديناميكيًا.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.__version__ import CORE_VERSION
from core.bootloader import BootReport
from core.event_bus import EventBus
from core.journal import Journal
from core.metrics import Metrics
from core.registry import AtomRecord, Registry
from core.logger import current_trace_id  # ⚠️ استيراد حاوية التتبع السياقية
from core.contracts.atom import AtomState

_log = logging.getLogger("quant_nq.core.api")

_LIFECYCLE_EVENTS = [
    "core.atom.started",
    "core.atom.stopped",
    "core.atom.failed",
    "core.atom.unhealthy",
    "core.critical_atom.unhealthy",
    "core.atom.restarted",
    "core.atom.restart_failed",
    "core.atom.restarts_exhausted",
    "core.atom.unloaded",
    "hot_reload.completed",
]

_DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"


class _DroppingQueue:
    def __init__(self, maxsize: int = 500) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: dict) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def get(self) -> dict:
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()


def _serialize_atom(record: AtomRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.manifest.name,
        "version": record.manifest.version,
        "critical": record.critical,
        "state": record.state.value,
        "health": (
            {"state": record.last_health.state.value, "message": record.last_health.message}
            if record.last_health else None
        ),
        "restart_count": record.restart_count,
        "last_error": record.last_error,
        "metadata": record.manifest.metadata,
    }


class TraceIdMiddleware(BaseHTTPMiddleware):
    """⚠️ تطبيق المادة 30 و 35: حقن معرّف التتبع لكل طلب API لربط سجلات النواة بالطلبات الخارجية"""
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
        token = current_trace_id.set(trace_id)
        try:
            response = await call_next(request)
            response.headers["x-trace-id"] = trace_id
            return response
        finally:
            current_trace_id.reset(token)


def create_app(
    registry: Registry,
    event_bus: EventBus,
    metrics: Metrics,
    journal: Journal,
    get_boot_report: Callable[[], BootReport | None] = lambda: None,
    api_key: str | None = None,
    health_manager: Any = None,
) -> FastAPI:
    
    app = FastAPI(title="QUANT_NQ Core", version=CORE_VERSION)
    app.add_middleware(TraceIdMiddleware)

    async def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if api_key is None:
            return
        if x_api_key != api_key:
            # ⚠️ تطبيق المادة 89: أخطاء مهيكلة برموز معيارية
            raise HTTPException(401, detail={"code": 4001, "message": "مفتاح API غير صحيح أو مفقود (ترويسة X-API-Key)"})

    _auth = Depends(_require_api_key)

    @app.get("/api/health", dependencies=[_auth])
    async def health() -> dict:
        return {"status": "ok", "core_version": CORE_VERSION, "atom_count": len(registry)}

    @app.get("/api/boot-report", dependencies=[_auth])
    async def boot_report() -> dict:
        report = get_boot_report()
        if report is None:
            raise HTTPException(404, detail={"code": 1000, "message": "لم يكتمل الإقلاع بعد"})
        data = asdict(report)
        data["scan_failures"] = [
            {"path": str(f.path), "error": f.error} for f in report.scan_failures
        ]
        return data

    @app.get("/api/atoms", dependencies=[_auth])
    async def list_atoms() -> list[dict]:
        return [_serialize_atom(r) for r in registry.all()]

    @app.get("/api/atoms/{atom_id}", dependencies=[_auth])
    async def get_atom(atom_id: int) -> dict:
        record = registry.find(atom_id)
        if record is None:
            raise HTTPException(404, detail={"code": 3201, "message": f"لا توجد ذرة بالمعرّف {atom_id}"})
        return _serialize_atom(record)

    @app.get("/api/metrics", dependencies=[_auth])
    async def get_metrics() -> dict:
        return metrics.snapshot()

    @app.get("/api/journal", dependencies=[_auth])
    async def get_journal(n: int = 100) -> list[dict]:
        n = max(1, min(n, 1000))
        return [asdict(e) for e in journal.tail(n)]

    @app.post("/api/atoms/{atom_id}/stop", dependencies=[_auth])
    async def stop_atom(atom_id: int) -> dict:
        record = registry.find(atom_id)
        if record is None:
            raise HTTPException(404, detail={"code": 3201, "message": f"لا توجد ذرة بالمعرّف {atom_id}"})
        
        if record.state != AtomState.RUNNING:
            raise HTTPException(409, detail={
                "code": 3003,
                "message": f"الذرة {atom_id} ليست في حالة running (الحالة: {record.state.value})",
            })

        # المادة 11: إيقاف الذرة يجب أن يوقف مراقبتها في نفس اللحظة —
        # وإلا رأى Health Manager ذرة غير RUNNING فأنهى حلقته، أو أسوأ:
        # حاول إعادة تشغيل ذرة أوقفها المشغّل عمدًا.
        if health_manager is not None:
            health_manager.unwatch(atom_id)
        try:
            await record.instance.stop()
        except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
            registry.set_state(atom_id, AtomState.FAILED)
            registry.set_error(atom_id, str(exc))
            await event_bus.publish(
                "core.atom.failed", {"atom_id": atom_id, "error": str(exc)}, publisher="core.api"
            )
            raise HTTPException(
                500, detail={"code": 3003, "message": f"فشل إيقاف الذرة: {exc}"}
            ) from exc

        registry.set_state(atom_id, AtomState.STOPPED)
        await event_bus.publish("core.atom.stopped", {"atom_id": atom_id}, publisher="core.api")
        return {"status": "stopped", "atom_id": atom_id}

    @app.post("/api/rescan", dependencies=[_auth])
    async def trigger_rescan() -> dict:
        await event_bus.publish("core.system.rescan_requested", {}, publisher="core.api")
        return {"status": "rescan_requested"}

    @app.get("/")
    async def dashboard() -> FileResponse:
        if not _DASHBOARD_HTML.exists():
            raise HTTPException(404, detail={"code": 1000, "message": "ملف لوحة المراقبة غير موجود"})
        return FileResponse(_DASHBOARD_HTML)

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        if api_key is not None and websocket.headers.get("x-api-key") != api_key:
            await websocket.close(code=4401, reason="مفتاح API غير صحيح أو مفقود")
            return
        await websocket.accept()
        queue = _DroppingQueue(maxsize=500)

        def make_forwarder(event_name: str):
            async def _forward(payload: dict) -> None:
                await queue.put({"type": "event", "name": event_name, "payload": payload})
            return _forward

        forwarders = [make_forwarder(name) for name in _LIFECYCLE_EVENTS]
        for name, fwd in zip(_LIFECYCLE_EVENTS, forwarders, strict=True):
            event_bus.subscribe(name, fwd, subscriber="api.websocket")

        async def snapshot_message() -> dict:
            return {
                "type": "snapshot",
                "atoms": [_serialize_atom(r) for r in registry.all()],
                "metrics": metrics.snapshot(),
            }

        try:
            await websocket.send_json(await snapshot_message())
        except Exception:  # noqa: BLE001 — العميل انقطع قبل أول رسالة
            for name, fwd in zip(_LIFECYCLE_EVENTS, forwarders):
                event_bus.unsubscribe(name, fwd)
            return

        async def periodic_snapshots() -> None:
            while True:
                await asyncio.sleep(3)
                await queue.put(await snapshot_message())

        snapshot_task = asyncio.create_task(periodic_snapshots())
        try:
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 — انقطاع مفاجئ للقناة
            # المادة 81: انقطاع عميل مراقبة لا يجوز أن يترك مهامًا أو
            # اشتراكات معلّقة في الناقل.
            _log.debug("انتهت قناة WebSocket بخطأ: %s", exc)
        finally:
            snapshot_task.cancel()
            try:
                await snapshot_task
            except asyncio.CancelledError:
                pass
            for name, fwd in zip(_LIFECYCLE_EVENTS, forwarders):
                event_bus.unsubscribe(name, fwd)

    return app