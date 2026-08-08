"""
المادة 14 / 20 / 46 — محرك الاكتشاف الحي
==========================================
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from core.contracts.atom import AtomState
from core.event_bus import EventBus
from core.health_manager import HealthManager
from core.hot_reload_service import HotReloadService
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom


def build(atoms_root: Path):
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())
    service = HotReloadService(atoms_root, registry, bus, health, journal=Journal())
    return service, registry, bus, health


@pytest.mark.asyncio
async def test_new_atoms_load_in_dependency_order(atoms_root: Path) -> None:
    """ذرتان جديدتان تصلان معًا وإحداهما تعتمد على الأخرى: الترتيب
    يجب أن يتبع الاعتماديات، لا المعرّف الرقمي."""
    write_atom(atoms_root, 900)                                # يعتمد عليه
    write_atom(atoms_root, 100, dependencies=[{"id": 900}])    # يعتمد على 900

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert service.last_loaded == [900, 100], (
        f"ترتيب التحميل الحي تجاهل الاعتماديات: {service.last_loaded}"
    )
    assert registry.get(100).state == AtomState.RUNNING
    await health.stop()


@pytest.mark.asyncio
async def test_incompatible_core_version_is_not_hot_loaded(atoms_root: Path) -> None:
    """المادة 20: لا تُشغَّل حياً ذرة غير متوافقة مع إصدار النواة."""
    write_atom(atoms_root, 55, core_version=">=99.0.0")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(55) is None, "حُمّلت ذرة غير متوافقة مع إصدار Core"
    await health.stop()


@pytest.mark.asyncio
async def test_manual_startup_mode_is_not_auto_started(atoms_root: Path) -> None:
    """startup_mode=manual يعني الاكتشاف دون تشغيل تلقائي."""
    write_atom(atoms_root, 60, startup_mode="manual")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(60) is None, "شُغّلت ذرة معلنة startup_mode=manual تلقائيًا"
    await health.stop()


@pytest.mark.asyncio
async def test_concurrent_rescans_do_not_double_register(atoms_root: Path) -> None:
    """الفحص الدوري و POST /api/rescan قد ينطلقان معًا: القفل يمنع
    تسجيل الذرة مرتين."""
    write_atom(atoms_root, 70)
    write_atom(atoms_root, 71)

    service, registry, _, health = build(atoms_root)
    await asyncio.gather(*(service._on_rescan_requested({}) for _ in range(6)))

    assert len(registry) == 2, f"تسجيل مزدوج نتيجة فحوصات متزامنة: {len(registry)}"
    await health.stop()


@pytest.mark.asyncio
async def test_atom_moved_to_deeper_folder_keeps_working(atoms_root: Path) -> None:
    """المادة 10/45: نقل مجلد الذرة لأي عمق لا يغيّر شيئًا."""
    write_atom(atoms_root, 80, subdir="family_a/atom_80")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})
    assert registry.get(80).state == AtomState.RUNNING

    shutil.move(str(atoms_root / "family_a"), str(atoms_root / "family_b"))
    await service._on_rescan_requested({})
    assert registry.get(80).state == AtomState.RUNNING, (
        "نقل الذرة بين العائلات كسر تشغيلها"
    )
    await health.stop()


@pytest.mark.asyncio
async def test_broken_manifest_does_not_stop_discovery(atoms_root: Path) -> None:
    """المادة 81: مانيفست تالف واحد لا يمنع اكتشاف البقية."""
    write_atom(atoms_root, 90)
    broken = atoms_root / "broken"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("id: [هذا ليس رقمًا\n", encoding="utf-8")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(90) is not None, "مانيفست تالف أوقف اكتشاف الذرات السليمة"
    await health.stop()
