"""
core/hot_reload_service.py — Runtime Discovery Engine: اكتشاف حي
(Hot Plug / Hot Unplug) لمجلد atoms/ أثناء تشغيل Core، بدون إعادة
تشغيله (المادة 14 و 46).

الضمانات المحفوظة هنا:
  * المادة 15 — الإزالة الصامتة تُطهّر Registry و Health Manager و
    Event Bus معًا، ولا تترك أي أثر في الذاكرة.
  * المادة 20 — لا تُحمَّل حياً أي ذرة غير متوافقة مع إصدار Core.
  * المادة 21 — فشل أي ذرة (ولو حرجة) لا يوقف الفحص ولا Core.
  * المادة 11 — ذرة تعثّرت أثناء التحميل الحي تُنظَّف وتُلغى تسجيلها
    بالكامل بدل بقائها كسجل ميت في Registry.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Protocol

from core.contracts.atom import AtomState

_log = logging.getLogger("quant_nq.core.hot_reload")


class RegistryProtocol(Protocol):
    def register(self, manifest: Any, instance: Any) -> Any: ...
    def unregister(self, atom_id: int) -> None: ...
    def all(self) -> list[Any]: ...
    def find(self, atom_id: int) -> Any | None: ...
    def set_state(self, atom_id: int, state: AtomState) -> None: ...


class HealthManagerProtocol(Protocol):
    def watch(self, atom_id: int) -> None: ...
    def unwatch(self, atom_id: int) -> None: ...


class EventBusProtocol(Protocol):
    def subscribe(self, event_name: str, handler, *, subscriber: str = "") -> None: ...
    def unsubscribe_all(self, subscriber: str) -> int: ...
    async def publish(self, event_name: str, payload: dict, *, publisher: str = "") -> None: ...


class HotReloadService:
    def __init__(
        self,
        atoms_root: Path,
        registry: RegistryProtocol,
        event_bus: EventBusProtocol,
        health_manager: HealthManagerProtocol,
        journal: Any = None,
    ) -> None:
        self._atoms_root = atoms_root
        self._registry = registry
        self._event_bus = event_bus
        self._health_manager = health_manager
        self._journal = journal
        self.rescan_count = 0
        self._periodic_task: asyncio.Task | None = None
        self.last_loaded: list[int] = []
        self.last_unloaded: list[int] = []
        self.last_upgraded: list[dict] = []
        # قفل إعادة الدخول: الفحص الدوري و POST /api/rescan يستطيعان
        # الانطلاق في نفس اللحظة. بدون هذا القفل تُسجَّل الذرة الجديدة
        # مرتين وتنفجر RegistryError، أو تُحمَّل نسختان منها في الذاكرة.
        self._scan_lock = asyncio.Lock()

    def register(self) -> None:
        self._event_bus.subscribe(
            "core.system.rescan_requested", self._on_rescan_requested, subscriber="core.hot_reload"
        )

    async def start_periodic(self, interval_s: float = 5.0) -> None:
        self._periodic_task = asyncio.create_task(self._periodic_loop(interval_s))

    async def stop_periodic(self) -> None:
        if self._periodic_task is not None:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None

    async def _periodic_loop(self, interval_s: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_s)
                await self._on_rescan_requested({})
        except asyncio.CancelledError:
            pass

    async def _on_rescan_requested(self, payload: dict) -> None:
        """نقطة الدخول الوحيدة لأي فحص حي. محمية بقفل يمنع تداخل
        الفحص الدوري مع الفحص اليدوي (POST /api/rescan)."""
        async with self._scan_lock:
            await self._rescan_once()

    async def _rescan_once(self) -> None:
        from core.dependency_resolver import find_unresolvable_dependencies, resolve
        from core.manifest_loader import scan
        from core.version_manager import find_core_incompatible_atoms

        discovery = scan(self._atoms_root)
        discovered_by_id = {a.manifest.id: a for a in discovery.atoms}
        registered_ids = {r.id for r in self._registry.all()}

        newly_discovered = set(discovered_by_id) - registered_ids
        rejected: list[tuple[int, str]] = []

        # المادة 14 تُلزم بدعم "تعديل أو ترقية" ذرة أثناء التشغيل، لا
        # الإضافة والحذف فقط. الذرة التي رُفع إصدارها في مكانها تُعامَل
        # كسحب ثم تحميل: تُفرَّغ حالتها، تُطهَّر بالكامل، ثم يُحمَّل
        # الكود الجديد. المقارنة بالإصدار المعلن حصرًا — لا ببصمة الملف —
        # لأن المانيفست هو العقد الحاكم (المادة 18/68).
        upgrades: list[tuple[int, str, str]] = []
        for record in self._registry.all():
            found = discovered_by_id.get(record.id)
            if found is None:
                continue
            old_v, new_v = str(record.manifest.version), str(found.manifest.version)
            if old_v != new_v:
                upgrades.append((record.id, old_v, new_v))

        # المادة 21 من دستور المانيفست: startup_mode يحدّد من يُشغَّل
        # تلقائيًا. MANUAL/LAZY تُكتشف وتُترك، ولا تُقحَم في التشغيل.
        for atom_id in sorted(newly_discovered):
            mode = getattr(discovered_by_id[atom_id].manifest.startup_mode, "value", "auto")
            if mode != "auto":
                rejected.append((atom_id, f"startup_mode={mode} — لا تُشغَّل تلقائيًا"))
        newly_discovered -= {aid for aid, _ in rejected}

        # المادة 20: لا تُحمَّل حياً ذرة غير متوافقة مع إصدار Core.
        incompatible = find_core_incompatible_atoms(
            [discovered_by_id[aid].manifest for aid in newly_discovered]
        )
        for atom_id, reason in sorted(incompatible.items()):
            rejected.append((atom_id, reason))
            _log.warning("رفض Hot-Load للذرة %s: %s", atom_id, reason)
        newly_discovered -= set(incompatible)

        # اعتماديات غير قابلة للحل مقابل الحالة الحية الفعلية للنظام.
        live_manifests = [r.manifest for r in self._registry.all()]
        candidate_manifests = live_manifests + [
            discovered_by_id[aid].manifest for aid in newly_discovered
        ]
        unresolvable = find_unresolvable_dependencies(candidate_manifests)
        for atom_id in sorted(newly_discovered & set(unresolvable)):
            reason = f"اعتماديات غير محلولة {unresolvable[atom_id]}"
            rejected.append((atom_id, reason))
            _log.warning("استبعاد الذرة الجديدة %s من Hot-Load: %s", atom_id, reason)
        newly_discovered -= set(unresolvable)

        load_order = self._load_order(
            newly_discovered, candidate_manifests, resolve
        )

        upgraded: list[dict] = []
        for atom_id, old_v, new_v in sorted(upgrades):
            try:
                await self._hot_unload(atom_id)
                await self._hot_load(discovered_by_id[atom_id])
                upgraded.append({"atom_id": atom_id, "from": old_v, "to": new_v})
                _log.info("رُقّيت الذرة %s حياً: %s ← %s", atom_id, old_v, new_v)
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                # المادة 21/81: فشل الترقية يُسقط الذرة وحدها. النسخة
                # القديمة أُوقفت فعلًا، لذا تبقى الذرة خارج الخدمة حتى
                # يُصلح المشغّل كودها ويلتقطها الفحص التالي.
                _log.error(
                    "فشلت ترقية الذرة %s (%s ← %s): %s — الذرة خارج الخدمة الآن",
                    atom_id, old_v, new_v, exc,
                )

        loaded: list[int] = []
        load_failed: list[tuple[int, str]] = []
        for atom_id in load_order:
            try:
                await self._hot_load(discovered_by_id[atom_id])
                loaded.append(atom_id)
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                load_failed.append((atom_id, str(exc)))
                _log.error("فشل Hot-Load للذرة %s: %s — نُظّفت بالكامل", atom_id, exc)

        # الإزالة تسير بعكس ترتيب الاعتماديات: المُعتمِد قبل المُعتمَد
        # عليه، حتى لا تُسحب ذرة من تحت ذرة ما زالت تستخدمها.
        newly_missing = registered_ids - set(discovered_by_id)
        unload_order = self._unload_order(newly_missing)

        unloaded: list[int] = []
        unload_failed: list[tuple[int, str]] = []
        for atom_id in unload_order:
            try:
                await self._hot_unload(atom_id)
                unloaded.append(atom_id)
            except Exception as exc:  # noqa: BLE001
                unload_failed.append((atom_id, str(exc)))
                _log.error("فشل Hot-Unload للذرة %s: %s", atom_id, exc)

        self.rescan_count += 1
        self.last_loaded, self.last_unloaded = loaded, unloaded
        self.last_upgraded = upgraded

        await self._event_bus.publish(
            "hot_reload.completed",
            {
                "loaded": loaded, "load_failed": load_failed,
                "unloaded": unloaded, "unload_failed": unload_failed,
                "upgraded": upgraded,
                "dependency_blocked": rejected,
                "scan_failures": [{"path": str(f.path), "error": f.error} for f in discovery.failures],
            },
            publisher="core.hot_reload",
        )
        _log.info(
            "Hot-Reload: حُمِّلت=%s أُزيلت=%s رُقّيت=%s مرفوضة=%s",
            loaded, unloaded, [u["atom_id"] for u in upgraded], rejected,
        )

    @staticmethod
    def _load_order(new_ids: set[int], candidates: list, resolve) -> list[int]:  # noqa: ANN001
        """ترتيب تحميل يحترم الاعتماديات: ذرتان جديدتان تصلان معًا
        وإحداهما تعتمد على الأخرى يجب أن تُحمّلا بالترتيب الصحيح، لا
        بترتيب المعرّف الرقمي."""
        if not new_ids:
            return []
        try:
            full_order = resolve(candidates).boot_order
        except Exception:  # noqa: BLE001 — رسم غير قابل للحل: ارجع لترتيب مستقر
            return sorted(new_ids)
        return [aid for aid in full_order if aid in new_ids]

    def _unload_order(self, missing_ids: set[int]) -> list[int]:
        """يُخرج المُعتمِدين أولًا. أي ذرة باقية تعتمد على ذرة مسحوبة
        يُنبَّه عنها صراحةً — المشغّل سحب اعتمادية من تحت ذرة حية."""
        if not missing_ids:
            return []
        depends_on: dict[int, set[int]] = {}
        for record in self._registry.all():
            depends_on[record.id] = {d.id for d in record.manifest.dependencies}

        for atom_id, deps in depends_on.items():
            if atom_id in missing_ids:
                continue
            orphaned = deps & missing_ids
            if orphaned:
                _log.warning(
                    "الذرة %s تعتمد على ذرة/ذرات تُسحب الآن %s — قد تتدهور صحتها؛ "
                    "Health Manager سيرصد ذلك (المادة 83)",
                    atom_id, sorted(orphaned),
                )

        ordered = sorted(
            missing_ids,
            key=lambda aid: len([o for o in missing_ids if aid in depends_on.get(o, set())]),
        )
        return ordered

    async def _hot_load(self, discovered) -> None:  # noqa: ANN001
        from core.bootloader import Bootloader  # بوابة التشكيل الرسمية الموحدة
        from core.contracts.atom import AtomContext
        from core.logger import get_logger

        atom_id = discovered.manifest.id
        # المادة 63/64: الاعتماد على بوابة التشكيل الرسمية بدلاً من كتابة
        # كود استيراد موازٍ داخل هذه الخدمة.
        instance = Bootloader.instantiate(discovered)
        self._registry.register(discovered.manifest, instance)
        self._registry.set_state(atom_id, AtomState.REGISTERED)

        context = AtomContext(
            atom_id=atom_id,
            config=discovered.manifest.config,
            logger=get_logger(atom_id),
            publish=self._make_bound_publish(atom_id),
            subscribe=self._make_bound_subscribe(atom_id),
        )

        try:
            self._registry.set_state(atom_id, AtomState.INITIALIZING)
            await instance.initialize(context)
            self._registry.set_state(atom_id, AtomState.INITIALIZED)
            self._registry.set_state(atom_id, AtomState.STARTING)
            await instance.start()
        except BaseException:
            # المادة 11 + 15 + 86: ذرة تعثّرت أثناء التحميل الحي يجب ألا
            # تبقى سجلًا ميتًا في Registry ولا اشتراكًا معلّقًا في الناقل
            # ولا موردًا مفتوحًا. تنظيف كامل، ثم يُعاد رفع الاستثناء
            # ليسجّله المستدعي.
            await self._purge_failed_load(atom_id, instance)
            raise

        self._registry.set_state(atom_id, AtomState.RUNNING)
        self._health_manager.watch(atom_id)
        if self._journal is not None:
            self._journal.record(atom_id, "hot_loaded")
        await self._event_bus.publish(
            "core.atom.started", {"atom_id": atom_id}, publisher="core.hot_reload"
        )

    async def _purge_failed_load(self, atom_id: int, instance) -> None:  # noqa: ANN001
        """تنظيف بأفضل جهد لذرة فشل تحميلها الحي. لا يُسمح لأي استثناء
        بالخروج من هنا — وإلا حجب سبب الفشل الأصلي."""
        for phase, fn in (("stop", instance.stop), ("shutdown", instance.shutdown)):
            try:
                await fn()
            except Exception as exc:  # noqa: BLE001
                _log.warning("فشل %s تنظيفي للذرة %s: %s", phase, atom_id, exc)
        try:
            self._health_manager.unwatch(atom_id)
            self._event_bus.unsubscribe_all(str(atom_id))
            self._registry.unregister(atom_id)
        except Exception as exc:  # noqa: BLE001
            _log.error("فشل تطهير سجلات الذرة %s بعد فشل التحميل: %s", atom_id, exc)
        if self._journal is not None:
            self._journal.record(atom_id, "hot_load_failed")

    async def _hot_unload(self, atom_id: int) -> None:
        record = self._registry.find(atom_id)
        if record is None:
            return
        self._health_manager.unwatch(atom_id)
        # المادة 15: التطهير مضمون حتى لو رمت الذرة استثناءً في stop()
        # أو shutdown() — لا يجوز أن يترك سلوك ذرة سيئة أثرًا في Registry
        # أو في الناقل.
        try:
            try:
                await record.instance.stop()
            finally:
                await record.instance.shutdown()
        except Exception as exc:  # noqa: BLE001
            _log.error("خطأ أثناء إيقاف/إنهاء الذرة %s عند السحب الحي: %s", atom_id, exc)
        finally:
            self._registry.unregister(atom_id)
            removed_subs = self._event_bus.unsubscribe_all(str(atom_id))
        if self._journal is not None:
            self._journal.record(atom_id, "hot_unloaded", {"subscriptions_cleared": removed_subs})
        await self._event_bus.publish("core.atom.unloaded", {"atom_id": atom_id}, publisher="core.hot_reload")

    def _make_bound_publish(self, atom_id: int):
        async def _publish(name: str, payload: dict | None = None) -> None:
            await self._event_bus.publish(name, payload, publisher=str(atom_id))
        return _publish

    def _make_bound_subscribe(self, atom_id: int):
        def _subscribe(name: str, handler) -> None:
            self._event_bus.subscribe(name, handler, subscriber=str(atom_id))
        return _subscribe