"""
Core.health_manager
=====================
Article 12 (+ Article 18 في الدستور الأول: "Health Manager مسؤول عن
اكتشاف الأعطال، وليس Bootloader"): يراقب أي ذرة تُسجَّل تلقائيًا دون أي
تسجيل يدوي، باستخدام إعدادات health المعلنة في manifest كل ذرة على
حدة.

فشل ذرة حرجة في الفحص الصحي **لا يوقف عملية Core** في هذه الوحدة (خلافًا
لفشلها أثناء الإقلاع في Bootloader) — إسقاط العملية بأكملها بسبب تدهور
لاحق لذرة واحدة يتعارض مع "لا يُسمح بانهيار النظام بسبب ذرة واحدة"
ويضرّ هدف 99.99%. بدلاً من ذلك: تنبيه critical واضح + حدث مخصص، يترك
القرار (إعادة تشغيل يدوي، تنبيه بشري، إيقاف مخطَّط) لطبقة تشغيلية أعلى.
راجع docs/core/ARCHITECTURE.md للتفصيل الكامل.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from core.contracts.atom import AtomState, HealthState, HealthStatus
from core.event_bus import EventBus
from core.journal import Journal
from core.logger import get_logger
from core.metrics import Metrics
from core.registry import AtomRecord, Registry

_log = logging.getLogger("quant_nq.core.health_manager")

# عدد الفحوصات الصحية المتتالية الناجحة التي تُجدَّد بعدها ميزانية
# إعادة التشغيل لذرة تعافت واستقرت فعلًا.
_RESTART_BUDGET_RESET_CHECKS = 10


class HealthManager:
    """نسخة واحدة لكل عملية Core."""

    def __init__(
        self,
        registry: Registry,
        event_bus: EventBus,
        journal: Journal,
        metrics: Metrics,
        *,
        logger_factory: Callable[[int], logging.LoggerAdapter] = get_logger,
    ) -> None:
        self._registry = registry
        self._event_bus = event_bus
        self._journal = journal
        self._metrics = metrics
        self._logger_factory = logger_factory
        self._tasks: dict[int, asyncio.Task] = {}
        self._failure_counts: dict[int, int] = {}
        self._healthy_streak: dict[int, int] = {}
        self._restarts_exhausted_announced: set[int] = set()

    async def start(self) -> None:
        """يبدأ مراقبة كل ذرة RUNNING حاليًا — لا حاجة لأي تسجيل يدوي
        لذرة جديدة تُضاف لاحقًا؛ استدعِ watch() لها فقط (Bootloader
        يفعل هذا تلقائيًا بعد كل start ناجح)."""
        for record in self._registry.by_state(AtomState.RUNNING):
            self.watch(record.id)

    def watch(self, atom_id: int) -> None:
        if atom_id in self._tasks:
            return
        self._failure_counts[atom_id] = 0
        self._tasks[atom_id] = asyncio.create_task(self._loop(atom_id))

    def unwatch(self, atom_id: int) -> None:
        task = self._tasks.pop(atom_id, None)
        if task:
            task.cancel()
        self._failure_counts.pop(atom_id, None)
        self._healthy_streak.pop(atom_id, None)
        self._restarts_exhausted_announced.discard(atom_id)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._failure_counts.clear()
        self._healthy_streak.clear()
        self._restarts_exhausted_announced.clear()

    def is_watching(self, atom_id: int) -> bool:
        return atom_id in self._tasks

    # ------------------------------------------------------------ internals --

    async def _loop(self, atom_id: int) -> None:
        """حلقة مراقبة ذرة واحدة. تُنظّف نفسها من `_tasks` عند الخروج،
        وإلا بقيت المهمة المنتهية مسجَّلة و`watch()` يرفض إعادة مراقبة
        نفس الذرة بعد إعادة تشغيلها لاحقًا."""
        try:
            while True:
                record = self._registry.find(atom_id)
                if record is None or record.state not in (AtomState.RUNNING,):
                    return

                health_cfg = record.manifest.health
                await asyncio.sleep(health_cfg.interval_ms / 1000)

                record = self._registry.find(atom_id)
                if record is None or record.state != AtomState.RUNNING:
                    return

                status = await self._check_once(record)
                self._registry.set_health(atom_id, status)
                if status.state == HealthState.HEALTHY:
                    self._healthy_streak[atom_id] = self._healthy_streak.get(atom_id, 0) + 1
                    if (
                        record.restart_count
                        and self._healthy_streak[atom_id] >= _RESTART_BUDGET_RESET_CHECKS
                    ):
                        # تعافت الذرة واستقرت: ميزانية إعادة التشغيل
                        # تُجدَّد. بدون هذا تُستهلك الميزانية مرة واحدة
                        # مدى عمر العملية، فتفقد الذرة الحماية لاحقًا.
                        _log.info("الذرة %s استقرت — تجديد ميزانية إعادة التشغيل", atom_id)
                        record.restart_count = 0
                        self._healthy_streak[atom_id] = 0
                        self._restarts_exhausted_announced.discard(atom_id)
                else:
                    self._healthy_streak[atom_id] = 0
                self._metrics.gauge(
                    atom_id, "health.is_healthy", 1.0 if status.state == HealthState.HEALTHY else 0.0
                )

                if status.state == HealthState.UNHEALTHY:
                    self._failure_counts[atom_id] = self._failure_counts.get(atom_id, 0) + 1
                else:
                    self._failure_counts[atom_id] = 0

                if self._failure_counts[atom_id] >= health_cfg.failure_threshold:
                    self._failure_counts[atom_id] = 0
                    await self._handle_unhealthy(atom_id, record)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — المادة 81: حلقة مراقبة
            # واحدة لا يجوز أن تسقط بصمت وتُفقد المراقبة دون أثر.
            _log.error("توقفت حلقة مراقبة الذرة %s بخطأ غير متوقَّع: %s", atom_id, exc, exc_info=exc)
        finally:
            if self._tasks.get(atom_id) is asyncio.current_task():
                self._tasks.pop(atom_id, None)
                self._failure_counts.pop(atom_id, None)

    async def _check_once(self, record: AtomRecord) -> HealthStatus:
        try:
            return await asyncio.wait_for(
                record.instance.health_check(), timeout=record.manifest.health.timeout_ms / 1000
            )
        except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
            return HealthStatus(state=HealthState.UNHEALTHY, message=str(exc))

    async def _handle_unhealthy(self, atom_id: int, record: AtomRecord) -> None:
        health_cfg = record.manifest.health
        _log.error("الذرة %s غير صحية بعد %d فحوصات فاشلة متتالية", atom_id, health_cfg.failure_threshold)
        self._journal.record(atom_id, "unhealthy")
        await self._event_bus.publish("core.atom.unhealthy", {"atom_id": atom_id}, publisher="core.health_manager")

        if record.manifest.critical:
            _log.critical("ذرة حرجة أصبحت غير صحية: %s — لا إيقاف تلقائي لـ Core، انظر ARCHITECTURE.md", atom_id)
            await self._event_bus.publish("core.critical_atom.unhealthy", {"atom_id": atom_id}, publisher="core.health_manager")

        if health_cfg.restart_on_failure and record.restart_count < health_cfg.max_restarts:
            await self._restart(atom_id, record)
        elif health_cfg.restart_on_failure and atom_id not in self._restarts_exhausted_announced:
            self._restarts_exhausted_announced.add(atom_id)
            _log.critical(
                "الذرة %s استنفدت محاولات إعادة التشغيل (%d/%d) وما زالت غير صحية",
                atom_id, record.restart_count, health_cfg.max_restarts,
            )
            self._journal.record(atom_id, "restarts_exhausted")
            await self._event_bus.publish("core.atom.restarts_exhausted", {"atom_id": atom_id}, publisher="core.health_manager")

    async def _restart(self, atom_id: int, record: AtomRecord) -> None:
        health_cfg = record.manifest.health
        backoff_s = (health_cfg.restart_backoff_ms / 1000) * (record.restart_count + 1)
        _log.info("إعادة تشغيل الذرة %s بعد %.1f ثانية (محاولة %d/%d)",
                   atom_id, backoff_s, record.restart_count + 1, health_cfg.max_restarts)
        await asyncio.sleep(backoff_s)

        # الذرة قد تكون أُزيلت من Registry أثناء فترة الانتظار أعلاه —
        # لا نعتمد على المرجع القديم دون تحقق (دفاع ضد تزامن مستقبلي،
        # حتى لو كان unregister/unwatch غير مستخدَمين حاليًا في أي مسار
        # تشغيلي فعلي).
        current = self._registry.find(atom_id)
        if current is None:
            _log.warning("إلغاء إعادة تشغيل الذرة %s: لم تعد مسجَّلة", atom_id)
            return

        current.restart_count += 1
        try:
            await current.instance.stop()
        except Exception:  # noqa: BLE001 — إيقاف بأفضل جهد فقط
            pass

        try:
            await current.instance.start()
        except Exception as exc:  # noqa: BLE001
            if self._registry.find(atom_id) is None:
                return  # أُزيلت أثناء start() نفسها — لا شيء نحدّثه بأمان
            self._registry.set_state(atom_id, AtomState.FAILED)
            self._registry.set_error(atom_id, str(exc))
            self._metrics.increment(atom_id, "lifecycle.restart.failure")
            self._journal.record(atom_id, "restart_failed", {"error": str(exc)})
            await self._event_bus.publish("core.atom.restart_failed", {"atom_id": atom_id, "error": str(exc)}, publisher="core.health_manager")
            return

        if self._registry.find(atom_id) is None:
            return  # أُزيلت أثناء start() نفسها — لا شيء نحدّثه بأمان

        self._registry.set_state(atom_id, AtomState.RUNNING)
        self._metrics.increment(atom_id, "lifecycle.restart.success")
        self._journal.record(atom_id, "restarted")
        await self._event_bus.publish("core.atom.restarted", {"atom_id": atom_id}, publisher="core.health_manager")
