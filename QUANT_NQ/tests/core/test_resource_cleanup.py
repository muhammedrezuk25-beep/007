"""
المادة 15 / 16 / 85 / 86 — تحرير الموارد وعدم ترك أي أثر
=========================================================
المادة 86: "أي تسريب للذاكرة، أو بقاء لمهام خلفية معلقة، أو ملفات
مفتوحة بعد انتهاء إيقاف الذرة يُعتبر عيباً برمجياً جسيماً."
المادة 15: الإزالة تُطهّر Registry و Health Manager و Event Bus معًا.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.bootloader import Bootloader
from core.contracts.atom import AtomState
from core.event_bus import EventBus
from core.health_manager import HealthManager
from core.hot_reload_service import HotReloadService
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom

CLEANUP_ATOM = '''
from core.contracts.atom import AtomBase, AtomContext

TRACE = []


class Atom(AtomBase):
    async def initialize(self, context: AtomContext) -> None:
        TRACE.append("initialize")
        context.subscribe("some.event", lambda p: None)

    async def start(self) -> None:
        TRACE.append("start")
        raise RuntimeError("فشل مُتعمَّد بعد نجاح initialize")

    async def stop(self) -> None:
        TRACE.append("stop")

    async def shutdown(self) -> None:
        TRACE.append("shutdown")
'''


@pytest.mark.asyncio
async def test_failed_start_still_calls_stop_and_shutdown(atoms_root: Path) -> None:
    """ذرة نجح initialize لها وفشل start: قد تكون فتحت موارد فعلًا.
    Core ملزم باستدعاء stop() ثم shutdown() لتحريرها (المادة 16/85)."""
    directory = write_atom(atoms_root, 1)
    (directory / "atom.py").write_text(CLEANUP_ATOM, encoding="utf-8")

    registry = Registry()
    bootloader = Bootloader(atoms_root, registry, EventBus(), Journal(), Metrics())
    report = await bootloader.boot()

    assert report.failed == [1]
    instance = registry.get(1).instance
    trace = type(instance).__module__
    module = __import__("sys").modules[trace]
    assert module.TRACE == ["initialize", "start", "stop", "shutdown"], (
        f"المادة 16/85: لم تُحرَّر موارد ذرة فشلت بعد initialize — {module.TRACE}"
    )


@pytest.mark.asyncio
async def test_failed_start_purges_event_subscriptions(atoms_root: Path) -> None:
    """المادة 15: اشتراكات ذرة فشلت لا يجوز أن تبقى معلّقة في الناقل."""
    directory = write_atom(atoms_root, 2)
    (directory / "atom.py").write_text(CLEANUP_ATOM, encoding="utf-8")

    bus = EventBus()
    bootloader = Bootloader(atoms_root, Registry(), bus, Journal(), Metrics())
    await bootloader.boot()

    assert bus.subscriber_count("some.event") == 0, (
        "بقي اشتراك معلّق في الناقل لذرة فشل إقلاعها"
    )


@pytest.mark.asyncio
async def test_hot_load_failure_leaves_no_zombie_in_registry(atoms_root: Path) -> None:
    """ذرة فشل تحميلها حياً يجب ألا تبقى سجلًا ميتًا يمنع إعادة
    المحاولة لاحقًا (المادة 11/15)."""
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())
    service = HotReloadService(atoms_root, registry, bus, health, journal=Journal())

    directory = write_atom(atoms_root, 3, fail_on="start")
    await service._on_rescan_requested({})

    assert registry.find(3) is None, "بقيت ذرة فاشلة مسجَّلة في Registry (زومبي)"
    assert not health.is_watching(3)

    # وبعد إصلاح الذرة، الفحص التالي يجب أن ينجح في تحميلها
    write_atom(atoms_root, 3, subdir=directory.name)
    await service._on_rescan_requested({})
    assert registry.find(3) is not None
    assert registry.get(3).state == AtomState.RUNNING


@pytest.mark.asyncio
async def test_hot_unload_purges_registry_health_and_bus(atoms_root: Path) -> None:
    """المادة 15: سحب مجلد الذرة حياً يطهّر السجلات الثلاثة معًا."""
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())
    service = HotReloadService(atoms_root, registry, bus, health, journal=Journal())

    directory = write_atom(atoms_root, 4)
    await service._on_rescan_requested({})
    assert registry.find(4) is not None
    bus.subscribe("x", lambda p: None, subscriber="4")

    import shutil

    shutil.rmtree(directory)
    await service._on_rescan_requested({})

    assert registry.find(4) is None
    assert not health.is_watching(4)
    assert bus.subscriber_count("x") == 0
    await health.stop()


@pytest.mark.asyncio
async def test_hot_unload_purges_even_when_atom_raises_on_stop(atoms_root: Path) -> None:
    """ذرة سيئة السلوك ترمي في stop(): التطهير يجب أن يتم رغم ذلك."""
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())
    service = HotReloadService(atoms_root, registry, bus, health, journal=Journal())

    directory = write_atom(atoms_root, 5)
    (directory / "atom.py").write_text(
        '''
from core.contracts.atom import AtomBase, AtomContext


class Atom(AtomBase):
    async def initialize(self, context: AtomContext) -> None: pass
    async def start(self) -> None: pass
    async def stop(self) -> None: raise RuntimeError("ذرة سيئة")
    async def shutdown(self) -> None: raise RuntimeError("ذرة سيئة")
''',
        encoding="utf-8",
    )
    await service._on_rescan_requested({})
    assert registry.find(5) is not None

    import shutil

    shutil.rmtree(directory)
    await service._on_rescan_requested({})
    assert registry.find(5) is None, "استثناء من الذرة منع تطهير Registry"
    await health.stop()
