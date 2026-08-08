"""
المادة 14 / 15 / 18 — التعديل والترقية والحذف الجزئي
======================================================
المادة 14 تنص على دعم "إضافة أو حذف أو **تعديل أو ترقية**" أي ذرة أثناء
عمل النظام. الإضافة والحذف وحدهما لا يكفيان.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.contracts.atom import AtomState
from core.event_bus import EventBus
from core.health_manager import HealthManager
from core.hot_reload_service import HotReloadService
from core.journal import Journal
from core.manifest_loader import scan
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom


def build(atoms_root: Path):
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())
    return HotReloadService(atoms_root, registry, bus, health, journal=Journal()), registry, health


# ---------------------------------------------------- الحذف الجزئي --

def test_manifest_without_its_entrypoint_file_is_not_a_valid_atom(atoms_root: Path) -> None:
    """حذف `atom.py` وترك `manifest.yaml` ينتج ذرة **ناقصة** لا صالحة."""
    directory = write_atom(atoms_root, 1)
    (directory / "atom.py").unlink()

    report = scan(atoms_root)
    assert report.atoms == [], "مانيفست بلا ملف كود اعتُبر ذرة صالحة"
    assert len(report.failures) == 1
    assert "نقطة الدخول" in report.failures[0].error


@pytest.mark.asyncio
async def test_deleting_only_the_code_file_unloads_the_running_atom(
    atoms_root: Path,
) -> None:
    """السيناريو الواقعي: المشغّل يحذف ملف الكود وحده أثناء التشغيل.

    قبل هذا الإصلاح كانت الذرة تبقى مسجَّلة وتعمل من الذاكرة إلى الأبد،
    ولا تنكشف الحقيقة إلا عند إعادة تشغيل النواة (المادة 15).
    """
    directory = write_atom(atoms_root, 2)
    service, registry, health = build(atoms_root)

    await service._on_rescan_requested({})
    assert registry.get(2).state == AtomState.RUNNING

    (directory / "atom.py").unlink()
    await service._on_rescan_requested({})

    assert registry.find(2) is None, "بقيت ذرة محذوف كودها تعمل من الذاكرة"
    assert not health.is_watching(2)
    await health.stop()


@pytest.mark.asyncio
async def test_wrong_entrypoint_in_manifest_is_caught_at_scan(atoms_root: Path) -> None:
    directory = write_atom(atoms_root, 3)
    manifest = (directory / "manifest.yaml").read_text(encoding="utf-8")
    (directory / "manifest.yaml").write_text(
        manifest + '\nentrypoint: "does_not_exist:Atom"\n', encoding="utf-8"
    )
    assert scan(atoms_root).atoms == []


# --------------------------------------------------- الترقية الحية --

@pytest.mark.asyncio
async def test_version_bump_triggers_hot_upgrade(atoms_root: Path) -> None:
    """رفع الإصدار في المكان يجب أن يُعيد تحميل الكود الجديد فعلًا."""
    directory = write_atom(atoms_root, 10, version="1.0.0")
    service, registry, health = build(atoms_root)

    await service._on_rescan_requested({})
    assert str(registry.get(10).manifest.version) == "1.0.0"

    shutil.rmtree(directory)
    write_atom(atoms_root, 10, subdir=directory.name, version="2.0.0")
    await service._on_rescan_requested({})

    assert str(registry.get(10).manifest.version) == "2.0.0", (
        "المادة 14: الترقية في المكان لم تُطبَّق — ما زال الإصدار القديم يعمل"
    )
    assert service.last_upgraded == [{"atom_id": 10, "from": "1.0.0", "to": "2.0.0"}]
    assert registry.get(10).state == AtomState.RUNNING
    await health.stop()


@pytest.mark.asyncio
async def test_unchanged_version_is_not_reloaded(atoms_root: Path) -> None:
    """بلا تغيير إصدار = بلا إعادة تحميل. المانيفست هو العقد الحاكم."""
    write_atom(atoms_root, 20, version="1.0.0")
    service, registry, health = build(atoms_root)

    await service._on_rescan_requested({})
    first = registry.get(20).instance
    await service._on_rescan_requested({})

    assert registry.get(20).instance is first, "أُعيد تحميل ذرة لم يتغيّر إصدارها"
    assert service.last_upgraded == []
    await health.stop()


@pytest.mark.asyncio
async def test_failed_upgrade_takes_down_only_that_atom(atoms_root: Path) -> None:
    """المادة 21/81: ترقية فاشلة لا تُسقط النواة ولا الذرات الأخرى."""
    directory = write_atom(atoms_root, 30, version="1.0.0")
    write_atom(atoms_root, 31)
    service, registry, health = build(atoms_root)

    await service._on_rescan_requested({})
    assert len(registry) == 2

    shutil.rmtree(directory)
    write_atom(atoms_root, 30, subdir=directory.name, version="2.0.0", fail_on="start")
    await service._on_rescan_requested({})

    assert registry.find(30) is None, "بقيت ذرة فشلت ترقيتها كسجل ميت"
    assert registry.get(31).state == AtomState.RUNNING, "ترقية فاشلة أسقطت ذرة أخرى"
    await health.stop()
