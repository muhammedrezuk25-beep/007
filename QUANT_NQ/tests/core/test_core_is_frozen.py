"""
المادة 1 / 41 / 91 / 100 — النواة مجمّدة
==========================================
هذا الاختبار هو الحارس الآلي للتجميد. يفشل فور تعديل أي ملف داخل
`core/` دون إعادة ختم متعمَّدة، فيتحوّل مبدأ "النواة ثابتة" من نيّة
مكتوبة إلى قيد يكسر البناء.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = PROJECT_ROOT / "core" / "CORE.lock"
FREEZE_TOOL = PROJECT_ROOT / "scripts" / "freeze_core.py"


def test_core_lock_exists() -> None:
    assert LOCK_FILE.exists(), (
        "النواة غير مختومة. نفّذ: python scripts/freeze_core.py freeze"
    )


def test_core_matches_its_seal() -> None:
    result = subprocess.run(
        [sys.executable, str(FREEZE_TOOL), "verify"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "خرق ختم تجميد النواة (المادة 1/41/100):\n" + result.stdout + result.stderr
    )


def test_seal_matches_declared_core_version() -> None:
    """الختم وإصدار النواة يتحركان معًا: إصدار جديد يعني ختمًا جديدًا."""
    from core.__version__ import CORE_VERSION

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    assert lock["core_version"] == CORE_VERSION, (
        f"الختم يحمل V{lock['core_version']} بينما النواة تعلن V{CORE_VERSION} — "
        "أعد الختم بـ freeze --reseal"
    )


def test_the_guard_lives_outside_the_thing_it_guards() -> None:
    """أداة الختم يجب أن تبقى خارج core/ — الحارس ليس جزءًا من المحروس،
    وإلا استطاع تعديلٌ واحد أن يزوّر الختم ويجتاز الفحص معًا."""
    assert FREEZE_TOOL.parent.name == "scripts"
    assert not (PROJECT_ROOT / "core" / "freeze_core.py").exists()


def test_every_sealed_file_is_accounted_for() -> None:
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    on_disk = {
        p.relative_to(PROJECT_ROOT).as_posix()
        for p in (PROJECT_ROOT / "core").rglob("*.py")
        if "__pycache__" not in p.parts
    }
    missing = on_disk - set(lock["files"])
    assert not missing, f"ملفات داخل core/ خارج الختم: {sorted(missing)}"
