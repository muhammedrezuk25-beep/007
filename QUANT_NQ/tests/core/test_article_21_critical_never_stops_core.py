"""
المادة 21 — حظر الـ Latch على الـ Core بذريعة الأهمية
=======================================================
"يحدد حقل critical سياسة تشغيل الذرة حصرًا عند الإقلاع أو الفشل، ولا
يمنحها أي امتياز خاص داخل Core، **ولا يوقف النواة إطلاقًا**... يمنع
تشغيل الذرة الحرجة أو يوقفها، بينما يستمر Core بالعمل."

هذه الاختبارات هي الحارس المباشر لهذه المادة. كانت **تفشل** على النسخة
السابقة، لأن Bootloader كان يُرجع مبكرًا (`return BootReport(...)`) عند
أول فشل لذرة حرجة، فيحرم كل الذرات التالية في ترتيب الإقلاع من التشغيل.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.bootloader import Bootloader
from core.event_bus import EventBus
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom


def build(atoms_root: Path) -> tuple[Bootloader, Registry]:
    registry = Registry()
    return (
        Bootloader(atoms_root, registry, EventBus(), Journal(), Metrics()),
        registry,
    )


@pytest.mark.asyncio
async def test_critical_atom_failure_does_not_stop_other_atoms(atoms_root: Path) -> None:
    """ذرة حرجة تفشل في start() — بقية الذرات يجب أن تُقلع كلها."""
    write_atom(atoms_root, 10, critical=True, fail_on="start")
    write_atom(atoms_root, 20)
    write_atom(atoms_root, 30)

    bootloader, registry = build(atoms_root)
    report = await bootloader.boot()

    assert 10 in report.failed
    assert sorted(report.booted) == [20, 30], "المادة 21: Core أوقف إقلاع ذرات سليمة"
    assert report.abort_reason is not None
    assert len(registry) == 3


@pytest.mark.asyncio
async def test_critical_atom_failure_at_initialize_does_not_stop_boot(atoms_root: Path) -> None:
    write_atom(atoms_root, 1, critical=True, fail_on="initialize")
    write_atom(atoms_root, 2)

    bootloader, _ = build(atoms_root)
    report = await bootloader.boot()

    assert report.booted == [2]
    assert report.failed == [1]
    assert report.success is False


@pytest.mark.asyncio
async def test_critical_atom_with_missing_dependency_is_excluded_not_fatal(
    atoms_root: Path,
) -> None:
    """ذرة حرجة تعتمد على ذرة غير موجودة: تُستبعد وحدها، والباقي يُقلع."""
    write_atom(atoms_root, 5, critical=True, dependencies=[{"id": 999}])
    write_atom(atoms_root, 6)

    bootloader, _ = build(atoms_root)
    report = await bootloader.boot()

    assert 5 in report.excluded
    assert report.booted == [6], "المادة 21: اعتمادية مفقودة لذرة حرجة أجهضت الإقلاع"
    assert report.success is False


@pytest.mark.asyncio
async def test_critical_atom_in_cycle_is_excluded_not_fatal(atoms_root: Path) -> None:
    write_atom(atoms_root, 7, critical=True, dependencies=[{"id": 8}])
    write_atom(atoms_root, 8, dependencies=[{"id": 7}])
    write_atom(atoms_root, 9)

    bootloader, _ = build(atoms_root)
    report = await bootloader.boot()

    assert {7, 8} <= set(report.excluded)
    assert report.booted == [9]


@pytest.mark.asyncio
async def test_critical_atom_incompatible_core_version_excluded_not_fatal(
    atoms_root: Path,
) -> None:
    write_atom(atoms_root, 11, critical=True, core_version=">=99.0.0")
    write_atom(atoms_root, 12)

    bootloader, _ = build(atoms_root)
    report = await bootloader.boot()

    assert 11 in report.excluded
    assert report.booted == [12]


@pytest.mark.asyncio
async def test_all_atoms_healthy_reports_success(atoms_root: Path) -> None:
    write_atom(atoms_root, 100, critical=True)
    write_atom(atoms_root, 200)

    bootloader, _ = build(atoms_root)
    report = await bootloader.boot()

    assert report.success is True
    assert report.abort_reason is None
    assert sorted(report.booted) == [100, 200]
