#!/usr/bin/env python3
"""
scripts/freeze_core.py — ختم وتجميد النواة (المادة 1 / 41 / 91 / 100)
=======================================================================
المادة 100: "يبقى الـ Core ثابتاً ومجمداً دائماً، وتبقى الذرات متغيرة
ومتحركة أبداً."

هذه الأداة تحوّل هذا المبدأ من نيّة مكتوبة إلى **قيد قابل للتحقق آليًا**:

  freeze   يحسب بصمة SHA-256 لكل ملف داخل `core/` ويكتب `core/CORE.lock`
           موقّعًا ببصمة جامعة واحدة (Merkle-style) تمثل النواة كاملة.
  verify   يعيد الحساب ويقارن. أي تعديل، إضافة، أو حذف لملف داخل
           `core/` يجعل هذه العملية تفشل برمز خروج غير صفري.
  status   عرض بشري موجز لحالة الختم.

الحارس المحلي: `scripts/install_guard.sh` يركّب هذا الفحص في
`.git/hooks/pre-commit`، فيرفض أي commit يلمس `core/` على جهازك مباشرة —
بلا خادم ولا حساب ولا إنترنت. وحين تقرر أنت عمدًا إصدارًا معماريًا
جديدًا: ارفع `CORE_VERSION` ثم `freeze --reseal`.

هذه الأداة تعيش في `scripts/` لا في `core/`: النواة لا تحرس نفسها،
والحارس ليس جزءًا من المحروس.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
LOCK_FILE = CORE_DIR / "CORE.lock"

# ما يُختم فعلًا: كل مصدر بايثون وكل أصل ثابت تعتمد عليه النواة.
SEALED_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".yaml", ".yml"}
# ما يُستبعد: مخرجات مؤقتة لا تمثل الكود المصدري.
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def _sealed_files() -> list[Path]:
    """كل ملف خاضع للختم، بترتيب مستقر لا يتأثر بنظام الملفات."""
    files = [
        path
        for path in CORE_DIR.rglob("*")
        if path.is_file()
        and path.suffix in SEALED_SUFFIXES
        and path.name != LOCK_FILE.name
        and not EXCLUDED_PARTS.intersection(path.parts)
    ]
    return sorted(files, key=lambda p: p.relative_to(PROJECT_ROOT).as_posix())


def _file_digest(path: Path) -> str:
    """بصمة محتوى الملف، محصّنة ضد اختلاف نهايات الأسطر بين المنصّات
    (CRLF على ويندوز مقابل LF) حتى لا يُبلَّغ عن خرق كاذب."""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def compute_manifest() -> dict:
    entries: dict[str, str] = {}
    for path in _sealed_files():
        entries[path.relative_to(PROJECT_ROOT).as_posix()] = _file_digest(path)

    joined = "\n".join(f"{name}:{digest}" for name, digest in entries.items())
    return {
        "files": entries,
        "file_count": len(entries),
        "root_digest": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
    }


def _core_version() -> str:
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.__version__ import CORE_VERSION

    return CORE_VERSION


def freeze(reseal: bool = False) -> int:
    if LOCK_FILE.exists() and not reseal:
        print("النواة مختومة بالفعل. استخدم --reseal لإعادة الختم عمدًا بعد إصدار معماري جديد.")
        print(f"   الملف: {LOCK_FILE.relative_to(PROJECT_ROOT)}")
        return 1

    manifest = compute_manifest()
    version = _core_version()
    previous = None
    if LOCK_FILE.exists():
        previous = json.loads(LOCK_FILE.read_text(encoding="utf-8")).get("root_digest")

    lock = {
        "_": "ختم تجميد النواة — المادة 1/41/100. لا يُعدَّل يدويًا إطلاقًا.",
        "core_version": version,
        "sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "algorithm": "sha256",
        "root_digest": manifest["root_digest"],
        "file_count": manifest["file_count"],
        "previous_root_digest": previous,
        "files": manifest["files"],
    }
    LOCK_FILE.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"تم ختم النواة بنجاح — Core V{version}")
    print(f"   الملفات المختومة : {manifest['file_count']}")
    print(f"   البصمة الجامعة   : {manifest['root_digest']}")
    if previous:
        print(f"   البصمة السابقة   : {previous}")
    return 0


def verify(quiet: bool = False) -> int:
    if not LOCK_FILE.exists():
        print("خطأ: النواة غير مختومة — لا يوجد core/CORE.lock. نفّذ: python scripts/freeze_core.py freeze")
        return 2

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    current = compute_manifest()
    recorded: dict[str, str] = lock["files"]

    modified = sorted(
        name for name, digest in current["files"].items()
        if name in recorded and recorded[name] != digest
    )
    added = sorted(set(current["files"]) - set(recorded))
    removed = sorted(set(recorded) - set(current["files"]))

    if not (modified or added or removed):
        if not quiet:
            print(f"النواة سليمة ومطابقة للختم — Core V{lock['core_version']}")
            print(f"   {current['file_count']} ملف، البصمة {current['root_digest'][:16]}…")
        return 0

    print("خرق ختم التجميد — النواة تغيّرت بعد اعتمادها (المادة 1/41/100)")
    print(f"   البصمة المختومة : {lock['root_digest']}")
    print(f"   البصمة الحالية  : {current['root_digest']}")
    for name in modified:
        print(f"   [مُعدَّل] {name}")
    for name in added:
        print(f"   [مُضاف ] {name}")
    for name in removed:
        print(f"   [محذوف] {name}")
    print()
    print("المادة 41: الحاجة لتعديل ملف داخل core/ لتشغيل ذرة = فشل معماري كامل.")
    print("أعد تصميم الذرة بدل تعديل النواة. وإن كان هذا إصدارًا معماريًا")
    print("جديدًا معتمدًا: ارفع CORE_VERSION ثم نفّذ freeze --reseal.")
    return 1


def status() -> int:
    if not LOCK_FILE.exists():
        print("الحالة: غير مختومة")
        return 2
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    intact = verify(quiet=True) == 0
    print(f"الحالة        : {'مختومة وسليمة' if intact else 'مختومة ومُنتهَكة'}")
    print(f"إصدار النواة  : {lock['core_version']}")
    print(f"تاريخ الختم   : {lock['sealed_at']}")
    print(f"عدد الملفات   : {lock['file_count']}")
    print(f"البصمة الجامعة: {lock['root_digest']}")
    return 0 if intact else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ختم وتجميد نواة QUANT_NQ")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze_cmd = sub.add_parser("freeze", help="ختم النواة الآن")
    freeze_cmd.add_argument(
        "--reseal", action="store_true",
        help="إعادة ختم متعمَّدة بعد اعتماد إصدار معماري جديد",
    )
    verify_cmd = sub.add_parser("verify", help="التحقق من سلامة الختم")
    verify_cmd.add_argument("--quiet", action="store_true")
    sub.add_parser("status", help="عرض حالة الختم")

    args = parser.parse_args()
    if args.command == "freeze":
        return freeze(reseal=args.reseal)
    if args.command == "verify":
        return verify(quiet=args.quiet)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
