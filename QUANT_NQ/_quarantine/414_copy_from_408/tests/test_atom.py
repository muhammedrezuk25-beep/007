import asyncio
import os
import sys
from typing import Any

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
from atom import Atom  # noqa: E402


class _NullLogger:
    """⚠️ تطبيق المادة 28: التوافق الكامل مع بروتوكول LoggerProtocol المعتمد بالنواة"""
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: pass
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: pass
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: pass
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: pass
    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None: pass


def make_context(config: dict | None = None):
    published = []

    async def publish(name, payload):
        published.append((name, payload))

    return AtomContext(
        atom_id=414, 
        config=config or {"accuracy_precision": 4}, 
        logger=_NullLogger(), 
        publish=publish, 
        subscribe=lambda n, h: None
    ), published


async def test_correct_entry_tracked():
    print("\n--- test_correct_entry_tracked ---")
    context, published = make_context()
    atom = Atom()
    await atom.initialize(context)
    await atom._on_entry_confirmed({"symbol": "NQ", "direction": "BUY", "agreement_count": 2})
    await atom._on_outcome({"symbol": "NQ", "actual_direction": "up"})
    events = [p for n, p in published if n == "strategy.evaluation_updated"]
    assert events[0]["correct"] is True and events[0]["accuracy"] == 1.0
    print(f"OK — دخول BUY + نتيجة up = صحيح: {events[0]}")


async def test_incorrect_entry_tracked():
    print("\n--- test_incorrect_entry_tracked ---")
    context, published = make_context()
    atom = Atom()
    await atom.initialize(context)
    await atom._on_entry_confirmed({"symbol": "NQ", "direction": "BUY", "agreement_count": 2})
    await atom._on_outcome({"symbol": "NQ", "actual_direction": "down"})
    events = [p for n, p in published if n == "strategy.evaluation_updated"]
    assert events[0]["correct"] is False and events[0]["accuracy"] == 0.0
    print(f"OK — دخول BUY + نتيجة down = خطأ: {events[0]}")


async def test_outcome_without_entry_ignored():
    print("\n--- test_outcome_without_entry_ignored ---")
    context, published = make_context()
    atom = Atom()
    await atom.initialize(context)
    await atom._on_outcome({"symbol": "NQ", "actual_direction": "up"})
    events = [p for n, p in published if n == "strategy.evaluation_updated"]
    assert len(events) == 0
    print("OK — نتيجة بلا دخول مؤكَّد سابق → صفر تقييم")


async def test_snapshot_restore():
    print("\n--- test_snapshot_restore ---")
    context, _ = make_context()
    atom = Atom()
    await atom.initialize(context)
    await atom._on_entry_confirmed({"symbol": "NQ", "direction": "BUY", "agreement_count": 2})
    await atom._on_outcome({"symbol": "NQ", "actual_direction": "up"})
    snap = await atom.snapshot()
    atom2 = Atom()
    await atom2.restore(snap)
    assert atom2._correct_count == 1 and atom2._total_count == 1
    print("OK — restore() نقل عدّادات الدقة بدقّة")


async def test_defensive_error_handling():
    print("\n--- test_defensive_error_handling ---")
    context, published = make_context()
    atom = Atom()
    await atom.initialize(context)
    # ⚠️ اختبار المادة 32: إرسال أحداث تالفة أو ناقصة للتأكد من عدم انهيار الذرة وحصر الأخطاء داخلياً بنجاح
    await atom._on_entry_confirmed({}) 
    await atom._on_outcome({"symbol": "NQ"})  # يفتقر actual_direction
    assert len(published) == 0
    print("OK — إرسال أحداث تالفة لم يتسبب في انهيار الذرة وتم حصر الأخطاء بنجاح")


async def main():
    tests = [
        test_correct_entry_tracked, 
        test_incorrect_entry_tracked, 
        test_outcome_without_entry_ignored, 
        test_snapshot_restore,
        test_defensive_error_handling
    ]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"❌ FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"❌ ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"✅ نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())