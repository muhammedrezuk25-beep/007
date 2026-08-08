#!/usr/bin/env python3
"""
scripts/test_atoms.py — تشغيل اختبارات كل ذرة معزولة (المادة 37/73/80)
=======================================================================
المادة 6: "يتم تشغيلها منفردة، واختبار وظيفتها منفردة".
المادة 37: كل ذرة تملك حزمة اختبارات خاصة **دون أي اعتماد على اختبارات
النواة** — ولا على اختبارات ذرة أخرى.

لماذا لا يكفي `pytest atoms/` مباشرة: كل ذرة تسمّي ملف اختباراتها
`test_atom.py`، فيرى pytest تسعةً وتسعين ملفًا بنفس اسم الوحدة ويرفض
جمعها معًا (`import file mismatch`). هذا ليس عطلًا يُصلَح بإعادة تسمية
الملفات — تسمية موحّدة هي بالضبط ما تطلبه المادة 37 — بل نتيجة طبيعية
لكون الذرات معزولة فعلًا. الحل: عملية pytest مستقلة لكل ذرة، وهو ما
يطابق نص الدستور حرفيًا بدل الالتفاف عليه.

الاستعمال:
    python3 scripts/test_atoms.py              # كل الذرات
    python3 scripts/test_atoms.py 610 619      # ذرات بعينها
    python3 scripts/test_atoms.py --failed-only
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ATOMS_ROOT = PROJECT_ROOT / "atoms"

_COUNT = re.compile(r"(\d+) (passed|failed|error|skipped)")


@dataclass
class Result:
    name: str
    returncode: int
    passed: int
    failed: int
    errors: int
    skipped: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def has_tests(self) -> bool:
        return self.passed + self.failed + self.errors + self.skipped > 0


def _atom_dirs(selection: list[str]) -> list[Path]:
    directories = sorted(d for d in ATOMS_ROOT.iterdir() if d.is_dir() and (d / "manifest.yaml").exists())
    if not selection:
        return directories
    return [d for d in directories if any(s in d.name for s in selection)]


def _run_one(directory: Path) -> Result:
    """عملية pytest مستقلة تمامًا لذرة واحدة."""
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(directory), "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    output = proc.stdout + proc.stderr
    counts = {kind: int(number) for number, kind in _COUNT.findall(output)}
    return Result(
        name=directory.name,
        returncode=proc.returncode,
        passed=counts.get("passed", 0),
        failed=counts.get("failed", 0),
        errors=counts.get("error", 0),
        skipped=counts.get("skipped", 0),
        output=output,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="اختبارات الذرات، كل ذرة معزولة")
    parser.add_argument("atoms", nargs="*", help="أجزاء من أسماء ذرات لتصفيتها")
    parser.add_argument("--failed-only", action="store_true", help="اعرض تفاصيل الفاشلة فقط")
    parser.add_argument("--jobs", type=int, default=8, help="عدد العمليات المتوازية")
    args = parser.parse_args()

    directories = _atom_dirs(args.atoms)
    if not directories:
        print("لا توجد ذرات مطابقة.")
        return 1

    print(f"تشغيل اختبارات {len(directories)} ذرة، كل واحدة في عملية مستقلة…\n")

    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(_run_one, directories):
            results.append(result)
            if result.ok:
                mark = "✓" if result.has_tests else "·"
                detail = f"{result.passed} اختبار" if result.has_tests else "بلا اختبارات"
                print(f"  {mark} {result.name:<42} {detail}")
            else:
                print(f"  ✗ {result.name:<42} فشل={result.failed} خطأ={result.errors}")

    broken = [r for r in results if not r.ok]
    empty = [r for r in results if r.ok and not r.has_tests]
    total_passed = sum(r.passed for r in results)

    print()
    print("─" * 68)
    print(f"  ذرات سليمة : {len(results) - len(broken)}/{len(results)}")
    print(f"  اختبارات ناجحة: {total_passed}")
    if empty:
        # المادة 73/80: ذرة بلا اختبارات لا تُعتمد.
        print(f"  بلا اختبارات  : {len(empty)} — {', '.join(r.name for r in empty)}")
    if broken:
        print(f"  ذرات فاشلة  : {len(broken)}")

    if broken and not args.failed_only:
        for result in broken:
            print()
            print("═" * 68)
            print(f"  {result.name}")
            print("═" * 68)
            print(result.output[-2500:])

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
