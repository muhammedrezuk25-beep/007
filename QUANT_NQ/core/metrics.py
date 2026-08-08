"""
Core.metrics
=============
Article 16: أي ذرة جديدة تظهر تلقائيًا داخل Metrics — لا تسجيل يدوي،
المفتاح دائمًا (Atom ID + اسم المقياس) فقط.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

# سعة نافذة العيّنات لكل مقياس زمني. عدّاد `count` و`avg_ms` يبقيان
# دقيقين على كامل العمر (مجموع تراكمي)، بينما `p95_ms` يُحسب على آخر
# TIMER_WINDOW عيّنة فقط — نافذة ثابتة تمنع نمو الذاكرة بلا حد مهما طال
# التشغيل (المادة 86: أي تسريب ذاكرة عيب برمجي جسيم).
TIMER_WINDOW = 1024


@dataclass(slots=True)
class _Timer:
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=TIMER_WINDOW))
    total_seconds: float = 0.0
    total_count: int = 0

    def record(self, seconds: float) -> None:
        self.samples.append(seconds)
        self.total_seconds += seconds
        self.total_count += 1

    @property
    def avg_ms(self) -> float:
        return (self.total_seconds / self.total_count * 1000) if self.total_count else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
        return ordered[idx] * 1000

    @property
    def count(self) -> int:
        return self.total_count


class Metrics:
    """عدادات/مقاييس عامة بمفتاح (atom_id, name) فقط. محمية بـ lock
    بسيط — الحجم المتوقَّع يبقى ضمن آلاف المفاتيح مع Article 28."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[int, str], int] = defaultdict(int)
        self._gauges: dict[tuple[int, str], float] = {}
        self._timers: dict[tuple[int, str], _Timer] = defaultdict(_Timer)

    def increment(self, atom_id: int, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[(atom_id, name)] += value

    def gauge(self, atom_id: int, name: str, value: float) -> None:
        with self._lock:
            self._gauges[(atom_id, name)] = value

    def timer(self, atom_id: int, name: str, seconds: float) -> None:
        with self._lock:
            self._timers[(atom_id, name)].record(seconds)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {f"{a}:{n}": v for (a, n), v in self._counters.items()},
                "gauges": {f"{a}:{n}": v for (a, n), v in self._gauges.items()},
                "timers": {
                    f"{a}:{n}": {"avg_ms": t.avg_ms, "p95_ms": t.p95_ms, "count": t.count}
                    for (a, n), t in self._timers.items()
                },
            }

    def forget_atom(self, atom_id: int) -> int:
        """يحذف كل مقاييس ذرة أُزيلت من النظام — المادة 15 تُلزم بألا
        يبقى أي أثر برمجي في الذاكرة بعد سحب الذرة."""
        with self._lock:
            removed = 0
            for store in (self._counters, self._gauges, self._timers):
                for key in [k for k in store if k[0] == atom_id]:
                    del store[key]
                    removed += 1
            return removed

    def for_atom(self, atom_id: int) -> dict[str, Any]:
        full = self.snapshot()
        prefix = f"{atom_id}:"
        return {
            kind: {k[len(prefix):]: v for k, v in group.items() if k.startswith(prefix)}
            for kind, group in full.items()
        }
