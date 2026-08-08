"""
المادة 1 / 4 / 6 / 43 — الذرة متعددة الملفات
==============================================
الذرة وحدة مكتفية بذاتها، وقد تتكوّن من أكثر من ملف واحد. يجب أن تستطيع
كتابة `from my_helper import X` بشكل طبيعي، دون أن تُجبَر على حقن
`sys.path` بنفسها — فذلك تلوّث دائم لمسار المفسّر تمنعه المادة 30.

وفي الوقت نفسه: ذرتان تشحنان ملفًا بنفس الاسم يجب أن تبقيا معزولتين
تمامًا (المادة 4: حظر المعرفة المتبادلة).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.bootloader import Bootloader
from core.event_bus import EventBus
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom

HELPER = "VALUE = {value!r}\n"

MULTIFILE_ATOM = '''
from core.contracts.atom import AtomBase, AtomContext
from helper import VALUE


class Atom(AtomBase):
    identity = VALUE

    async def initialize(self, context: AtomContext) -> None: pass
    async def start(self) -> None: pass
    async def stop(self) -> None: pass
'''


def _make(root: Path, atom_id: int, value: str) -> None:
    directory = write_atom(root, atom_id)
    (directory / "atom.py").write_text(MULTIFILE_ATOM, encoding="utf-8")
    (directory / "helper.py").write_text(HELPER.format(value=value), encoding="utf-8")


@pytest.mark.asyncio
async def test_atom_can_import_its_own_helper_module(atoms_root: Path) -> None:
    """لا حاجة لأي حيلة داخل الذرة — الاستيراد الطبيعي يعمل."""
    _make(atoms_root, 1, "alpha")

    registry = Registry()
    report = await Bootloader(
        atoms_root, registry, EventBus(), Journal(), Metrics()
    ).boot()

    assert report.booted == [1], f"فشل تحميل ذرة متعددة الملفات: {report.excluded}"
    assert registry.get(1).instance.identity == "alpha"


@pytest.mark.asyncio
async def test_two_atoms_with_same_helper_filename_stay_isolated(
    atoms_root: Path,
) -> None:
    """المادة 4/6/43: ذرتان تشحنان `helper.py` لا يجوز أن ترى إحداهما
    كود الأخرى لمجرد أنها حُمِّلت أولًا."""
    _make(atoms_root, 10, "alpha")
    _make(atoms_root, 20, "beta")

    registry = Registry()
    report = await Bootloader(
        atoms_root, registry, EventBus(), Journal(), Metrics()
    ).boot()

    assert sorted(report.booted) == [10, 20]
    assert registry.get(10).instance.identity == "alpha"
    assert registry.get(20).instance.identity == "beta", (
        "تسرّب كود ذرة إلى أخرى عبر تصادم اسم ملف مساعد"
    )


@pytest.mark.asyncio
async def test_sys_path_is_not_polluted_permanently(atoms_root: Path) -> None:
    """المادة 30: لا تلوّث دائم لبيئة المفسّر بعد التحميل."""
    _make(atoms_root, 30, "gamma")
    before = list(sys.path)

    await Bootloader(atoms_root, Registry(), EventBus(), Journal(), Metrics()).boot()

    assert sys.path == before, "بقي مجلد الذرة في sys.path بعد انتهاء التحميل"


@pytest.mark.asyncio
async def test_bare_helper_name_is_released_for_later_atoms(atoms_root: Path) -> None:
    """الاسم المجرد لا يبقى محجوزًا في sys.modules بعد تحميل الذرة."""
    _make(atoms_root, 40, "delta")
    await Bootloader(atoms_root, Registry(), EventBus(), Journal(), Metrics()).boot()

    assert "helper" not in sys.modules, "بقي اسم موديول مساعد محجوزًا عالميًا"
    assert "_atom_40_sib_helper" in sys.modules


@pytest.mark.asyncio
async def test_broken_helper_import_fails_cleanly(atoms_root: Path) -> None:
    """ذرة تستورد ملفًا غير موجود تُستبعد وحدها، والباقي يُقلع."""
    directory = write_atom(atoms_root, 50)
    (directory / "atom.py").write_text(MULTIFILE_ATOM, encoding="utf-8")  # بلا helper.py
    write_atom(atoms_root, 51)

    report = await Bootloader(
        atoms_root, Registry(), EventBus(), Journal(), Metrics()
    ).boot()

    assert 50 in report.excluded
    assert report.booted == [51]
