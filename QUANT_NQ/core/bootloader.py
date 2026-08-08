"""
Core.bootloader
=================
Article 8 (+ Article 5 في الدستور الأول): ينسّق فقط —
Scan → Load Manifest → Validate → Register → Resolve Dependencies →
Initialize → Start. لا يحتوي أي اسم أو رقم ذرة مكتوب بالكود.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.contracts.atom import AtomBase, AtomContext, AtomState
from core.dependency_resolver import (
    find_cycle_members,
    find_unresolvable_dependencies,
    resolve,
)
from core.errors import AtomInitializationError, CoreError
from core.event_bus import EventBus
from core.health_manager import HealthManager
from core.journal import Journal
from core.logger import get_logger
from core.manifest_loader import DiscoveredAtom, DiscoveryFailure, entrypoint_file, scan
from core.metrics import Metrics
from core.registry import Registry
from core.version_manager import find_core_incompatible_atoms

_log = logging.getLogger("quant_nq.core.bootloader")


@dataclass(slots=True)
class BootReport:
    started_at: float
    finished_at: float
    success: bool
    booted: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    excluded: list[int] = field(default_factory=list)
    scan_failures: list[DiscoveryFailure] = field(default_factory=list)
    abort_reason: str | None = None

    @property
    def duration_s(self) -> float:
        return self.finished_at - self.started_at


class Bootloader:
    """نسخة واحدة لكل عملية Core. لا تُنشئ أكثر من نسخة."""

    def __init__(
        self,
        atoms_root: Path,
        registry: Registry,
        event_bus: EventBus,
        journal: Journal,
        metrics: Metrics,
        *,
        health_manager: HealthManager | None = None,
        atom_boot_timeout_s: float = 30.0,
        logger_factory: Callable[[int], logging.LoggerAdapter] = get_logger,
    ) -> None:
        self._atoms_root = atoms_root
        self._registry = registry
        self._event_bus = event_bus
        self._journal = journal
        self._metrics = metrics
        self._health_manager = health_manager
        self._atom_boot_timeout_s = atom_boot_timeout_s
        self._logger_factory = logger_factory
        self._abort_reason: str | None = None

    async def boot(self) -> BootReport:
        started = time.time()
        excluded: list[int] = []
        critical_failures: list[str] = []

        discovery = scan(self._atoms_root)
        for failure in discovery.failures:
            _log.error("فشل تحميل manifest في %s: %s", failure.path, failure.error)

        working: dict[int, DiscoveredAtom] = {a.manifest.id: a for a in discovery.atoms}

        boot_order = self._resolve_with_graceful_degradation(working, excluded, critical_failures)

        for atom_id in boot_order:
            discovered = working[atom_id]
            try:
                instance = self.instantiate(discovered)
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                # المادة 21 + 81: فشل أي ذرة (حتى الحرجة) لا يوقف Core ولا
                # يوقف إقلاع بقية الذرات. تُستبعد وحدها ويُسجَّل السبب.
                msg = f"تعذّر تحميل الذرة {atom_id}: {exc}"
                if discovered.manifest.critical:
                    critical_failures.append(msg)
                    _log.critical("%s — ذرة حرجة تُستبعد، وCore يواصل الإقلاع (المادة 21)", msg)
                else:
                    _log.error("%s — تُستبعد", msg)
                excluded.append(atom_id)
                continue
            self._registry.register(discovered.manifest, instance)
            self._registry.set_state(atom_id, AtomState.REGISTERED)

        booted: list[int] = []
        failed: list[int] = []
        for atom_id in boot_order:
            if atom_id not in self._registry:
                continue
            ok = await self._initialize_and_start(atom_id)
            if ok:
                booted.append(atom_id)
                continue

            failed.append(atom_id)
            if self._registry.get(atom_id).critical:
                reason = (
                    f"فشلت ذرة حرجة أثناء التشغيل: {atom_id} "
                    f"({self._registry.get(atom_id).last_error})"
                )
                critical_failures.append(reason)
                _log.critical("%s — Core يواصل الإقلاع (المادة 21)", reason)

        self._abort_reason = "؛ ".join(critical_failures) if critical_failures else None
        return BootReport(
            started_at=started, finished_at=time.time(),
            success=not critical_failures,
            booted=booted, failed=failed, excluded=excluded,
            scan_failures=discovery.failures,
            abort_reason=self._abort_reason,
        )

    def _resolve_with_graceful_degradation(
        self,
        working: dict[int, DiscoveredAtom],
        excluded_out: list[int],
        critical_failures_out: list[str],
    ) -> list[int]:
        """يستبعد تدريجيًا كل ذرة غير قابلة للحل (اعتمادية مفقودة، حلقة
        دائرية، أو core_version غير متوافق) حتى يتبقى رسم قابل للحل.

        المادة 21: كون الذرة `critical` لا يمنح أي امتياز داخل Core ولا
        يوقف النواة — الذرة الحرجة غير القابلة للحل تُستبعد هي أيضًا،
        ويُسجَّل ذلك كـ critical failure في التقرير، وCore يكمل إقلاع
        الباقي. يُرجع دائمًا ترتيب إقلاع صالح (وقد يكون فارغًا).
        """
        manifests = [a.manifest for a in working.values()]

        for _ in range(len(manifests) + 1):
            unresolvable = find_unresolvable_dependencies(manifests)
            cycle_members = find_cycle_members(manifests)
            incompatible = find_core_incompatible_atoms(manifests)
            problem_ids = set(unresolvable) | cycle_members | set(incompatible)
            if not problem_ids:
                break

            for aid in sorted(problem_ids):
                reasons: list[str] = []
                if aid in unresolvable:
                    reasons.append(f"اعتماديات غير قابلة للحل: {unresolvable[aid]}")
                if aid in cycle_members:
                    reasons.append(f"ضمن حلقة دائرية: {sorted(cycle_members)}")
                if aid in incompatible:
                    reasons.append(incompatible[aid])
                detail = "؛ ".join(reasons)

                if working[aid].manifest.critical:
                    critical_failures_out.append(f"الذرة الحرجة {aid} — {detail}")
                    _log.critical(
                        "استبعاد الذرة الحرجة %s من الإقلاع (%s) — Core يواصل (المادة 21)",
                        aid, detail,
                    )
                else:
                    _log.warning("استبعاد الذرة غير الحرجة %s من الإقلاع (%s)", aid, detail)

                excluded_out.append(aid)
                del working[aid]
            manifests = [a.manifest for a in working.values()]

        try:
            return resolve(manifests).boot_order
        except CoreError as exc:
            # دفاع من الدرجة الثانية: لا يجوز أن يصل التنفيذ إلى هنا بعد
            # حلقة الاستبعاد أعلاه. حتى لو وصل، Core لا يتوقف.
            critical_failures_out.append(f"تعذّر حل رسم الاعتماديات نهائيًا: {exc}")
            _log.critical("تعذّر حل رسم الاعتماديات نهائيًا: %s — لن تُقلع أي ذرة", exc)
            excluded_out.extend(sorted(working))
            working.clear()
            return []

    @staticmethod
    def instantiate(discovered: DiscoveredAtom) -> AtomBase:
        """⚠️ تطبيق المادة 64: بوابة عامة مشتركة لجميع عمليات استيراد الأكواد وتشكيلها."""
        entrypoint = discovered.manifest.entrypoint
        module_name, _, class_name = entrypoint.partition(":")
        if not module_name or not class_name:
            raise AtomInitializationError(
                f"entrypoint غير صالح: {entrypoint!r} (الصيغة المتوقعة module:ClassName)"
            )

        module_file = entrypoint_file(discovered.manifest, discovered.directory)
        if not module_file.exists():
            raise AtomInitializationError(f"ملف entrypoint غير موجود: {module_file}")

        unique_name = f"_atom_{discovered.manifest.id}_{module_name}"
        spec = importlib.util.spec_from_file_location(unique_name, module_file)
        if spec is None or spec.loader is None:
            raise AtomInitializationError(f"تعذّر تحميل {module_file}")
        module = importlib.util.module_from_spec(spec)
        # تسجيل الموديول قبل التنفيذ: dataclasses/pickle/inspect وبعض
        # المكتبات تبحث عن الموديول في sys.modules أثناء تنفيذه نفسه.
        sys.modules[unique_name] = module

        # الذرة وحدة مكتفية بذاتها وقد تتكوّن من أكثر من ملف
        # (`from my_helper import X`). مجلدها يُضاف لمسار البحث **أثناء
        # التنفيذ فقط**، ثم يُسحب فورًا: لا تلوّث دائم لمسار المفسّر، ولا
        # حاجة لأن تحقن الذرة `sys.path` بنفسها (المادة 1 و 30).
        atom_dir = str(discovered.directory.resolve())
        before = set(sys.modules)
        sys.path.insert(0, atom_dir)
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(unique_name, None)
            raise
        finally:
            try:
                sys.path.remove(atom_dir)
            except ValueError:
                pass
            Bootloader._isolate_sibling_modules(discovered.manifest.id, atom_dir, before)

        cls = getattr(module, class_name, None)
        if not (isinstance(cls, type) and issubclass(cls, AtomBase)):
            raise AtomInitializationError(f"{entrypoint} لا يشير إلى كلاس يرث AtomBase")
        return cls()

    async def _initialize_and_start(self, atom_id: int) -> bool:
        record = self._registry.get(atom_id)
        log = self._logger_factory(atom_id)
        config = record.manifest.config

        context = AtomContext(
            atom_id=atom_id,
            config=config,
            logger=log,
            publish=lambda name, payload=None, _aid=atom_id: self._event_bus.publish(
                name, payload, publisher=str(_aid)
            ),
            subscribe=lambda name, handler, _aid=atom_id: self._event_bus.subscribe(
                name, handler, subscriber=str(_aid)
            ),
        )

        self._registry.set_state(atom_id, AtomState.INITIALIZING)
        try:
            await asyncio.wait_for(record.instance.initialize(context), timeout=self._atom_boot_timeout_s)
            self._registry.set_state(atom_id, AtomState.INITIALIZED)
            self._registry.set_state(atom_id, AtomState.STARTING)
            await asyncio.wait_for(record.instance.start(), timeout=self._atom_boot_timeout_s)
        except Exception as exc:  # noqa: BLE001
            # المادة 16/85/86: ذرة تعثّرت بعد initialize قد تكون فتحت
            # موارد فعلًا. لا نتركها معلّقة — تنظيف بأفضل جهد قبل وسمها
            # FAILED، مهما فشل التنظيف نفسه.
            await self._cleanup_failed_atom(atom_id, record.instance, log)
            self._mark_failed(atom_id, log, exc)
            self._event_bus.unsubscribe_all(str(atom_id))
            await self._event_bus.publish(
                "core.atom.failed", {"atom_id": atom_id, "error": str(exc)}, publisher="core.bootloader"
            )
            return False

        self._registry.set_state(atom_id, AtomState.RUNNING)
        self._journal.record(atom_id, "started")
        self._metrics.increment(atom_id, "lifecycle.start.success")
        if self._health_manager is not None:
            self._health_manager.watch(atom_id)
        await self._event_bus.publish(
            "core.atom.started", {"atom_id": atom_id}, publisher="core.bootloader"
        )
        return True

    @staticmethod
    def _isolate_sibling_modules(atom_id: int, atom_dir: str, before: set[str]) -> None:
        """يمنع تصادم أسماء الملفات المساعدة بين الذرات.

        ذرتان تشحنان ملفًا باسم `client.py` تُنتجان مفتاحًا واحدًا هو
        `client` في `sys.modules`؛ فمن حُمِّلت أولًا يفوز ملفها بالاسم،
        وتستورد الثانية كود الأولى بصمت — تسريب معرفة بين ذرتين محظور
        نصًا (المادة 4 و 6 و 43).

        بعد تنفيذ الذرة تُعاد فهرسة كل موديول جديد جاء من مجلدها تحت
        مفتاح خاص بها، ويُحرَّر الاسم المجرد لمن يأتي بعدها. كائن
        الموديول نفسه يبقى حيًا لأن globals الذرة تحمل مرجعًا له، فلا
        ينكسر شيء.
        """
        for name in set(sys.modules) - before:
            mod = sys.modules.get(name)
            origin = getattr(mod, "__file__", None) or ""
            if not origin or "." in name:
                continue
            try:
                same_dir = os.path.dirname(os.path.realpath(origin)) == os.path.realpath(atom_dir)
            except OSError:
                continue
            if same_dir:
                sys.modules[f"_atom_{atom_id}_sib_{name}"] = mod
                sys.modules.pop(name, None)

    async def _cleanup_failed_atom(
        self, atom_id: int, instance: AtomBase, log: logging.LoggerAdapter
    ) -> None:
        """تحرير موارد ذرة فشلت أثناء الإقلاع — بأفضل جهد، بمهلة، ولا
        يُسمح لأي استثناء من الذرة بالخروج من هنا (المادة 27/81)."""
        for phase, fn in (("stop", instance.stop), ("shutdown", instance.shutdown)):
            try:
                await asyncio.wait_for(fn(), timeout=self._atom_boot_timeout_s)
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                log.warning("فشل %s تنظيفي للذرة %s بعد فشل الإقلاع: %s", phase, atom_id, exc)

    def _mark_failed(self, atom_id: int, log: logging.LoggerAdapter, exc: Exception) -> None:
        self._registry.set_state(atom_id, AtomState.FAILED)
        self._registry.set_error(atom_id, str(exc))
        log.error("فشل بدء الذرة %s: %s", atom_id, exc)
        self._journal.record(atom_id, "start_failed", {"error": str(exc)})
        self._metrics.increment(atom_id, "lifecycle.start.failure")