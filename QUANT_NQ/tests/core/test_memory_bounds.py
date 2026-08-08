"""
المادة 86 — أي تسريب ذاكرة عيب برمجي جسيم
===========================================
النواة مصممة للعمل شهورًا دون إعادة تشغيل (المادة 91/97). أي بنية
تنمو مع الزمن بلا سقف داخل Core هي تسريب مؤجَّل.
"""

from __future__ import annotations

from pathlib import Path

from core.journal import Journal
from core.metrics import TIMER_WINDOW, Metrics


def test_timer_samples_are_bounded() -> None:
    metrics = Metrics()
    for i in range(TIMER_WINDOW * 5):
        metrics.timer(1, "latency", 0.001 * (i % 7))

    timer = metrics._timers[(1, "latency")]
    assert len(timer.samples) <= TIMER_WINDOW, "عيّنات المقياس الزمني تنمو بلا سقف"
    assert timer.count == TIMER_WINDOW * 5, "العدّاد الكلي يجب أن يبقى دقيقًا"


def test_timer_average_stays_accurate_over_full_lifetime() -> None:
    metrics = Metrics()
    for _ in range(TIMER_WINDOW * 3):
        metrics.timer(1, "t", 0.010)
    assert abs(metrics.snapshot()["timers"]["1:t"]["avg_ms"] - 10.0) < 0.001


def test_journal_memory_is_bounded_but_disk_is_complete(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = Journal(path=path, memory_capacity=100)
    for i in range(1000):
        journal.record(1, "tick", {"i": i})

    assert len(journal) == 100, "سجل الذاكرة ينمو بلا سقف"
    assert journal.total_recorded == 1000
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1000, (
        "القرص هو المصدر الكامل للحقيقة ويجب ألا يفقد أي إدخال"
    )


def test_journal_tail_returns_most_recent(tmp_path: Path) -> None:
    journal = Journal(memory_capacity=10)
    for i in range(50):
        journal.record(1, "a", {"i": i})
    tail = journal.tail(3)
    assert [e.payload["i"] for e in tail] == [47, 48, 49]


def test_journal_survives_unwritable_path(tmp_path: Path) -> None:
    """المادة 81: قرص ممتلئ أو صلاحيات ناقصة لا يجوز أن تُسقط مسار
    تشغيل الذرة عبر استثناء مرتد من السجل."""
    directory = tmp_path / "ro"
    directory.mkdir()
    journal = Journal(path=directory / "j.jsonl")
    directory.chmod(0o500)
    try:
        entry = journal.record(1, "still_works")
        assert entry.action == "still_works"
        assert len(journal) == 1
    finally:
        directory.chmod(0o700)


def test_metrics_forget_atom_clears_everything() -> None:
    """المادة 15: لا أثر في الذاكرة بعد إزالة الذرة."""
    metrics = Metrics()
    metrics.increment(9, "c")
    metrics.gauge(9, "g", 1.0)
    metrics.timer(9, "t", 0.5)
    metrics.increment(8, "c")

    assert metrics.forget_atom(9) == 3
    snap = metrics.snapshot()
    assert not any(k.startswith("9:") for group in snap.values() for k in group)
    assert "8:c" in snap["counters"]
