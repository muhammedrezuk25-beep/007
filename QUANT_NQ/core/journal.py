"""
Core.journal
=============
Article 15: يسجل جميع العمليات باستخدام Atom ID فقط — سجل append-only
عام، لا يعرف معنى العملية، فقط يحفظها.

ملاحظة أداء: الكتابة إلى الملف هنا متزامنة (sync) لأنها مصممة لمعدلات
تسجيل معتدلة. عند الحاجة لمعدلات عالية جدًا مع آلاف الذرات، يمكن
استبدال الكتابة بـ buffered/async I/O دون تغيير الواجهة العامة —
تحسين أداء داخلي مسموح بعد تجميد V1 (Article 1).
"""

from __future__ import annotations

import logging

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# السجل على القرص (JSONL) هو المصدر الكامل والدائم للحقيقة. النسخة في
# الذاكرة نافذة حديثة محدودة السعة فقط، تخدم `tail()` وواجهة المراقبة —
# قائمة غير محدودة هنا تعني نموًا بلا سقف في تشغيل يمتد لأشهر
# (المادة 86).
DEFAULT_MEMORY_CAPACITY = 10_000

_log = logging.getLogger("quant_nq.core.journal")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    ts: float
    atom_id: int
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


class Journal:
    """في الذاكرة + كتابة اختيارية إلى ملف append-only بصيغة JSONL. لا
    منطق أعمال هنا — فقط تسجيل حرفي لما تُرسله الذرات أو Core."""

    def __init__(
        self, path: Path | None = None, *, memory_capacity: int = DEFAULT_MEMORY_CAPACITY
    ) -> None:
        self._path = path
        self._entries: deque[JournalEntry] = deque(maxlen=memory_capacity)
        self._total_recorded = 0
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, atom_id: int, action: str, payload: dict[str, Any] | None = None) -> JournalEntry:
        entry = JournalEntry(ts=time.time(), atom_id=atom_id, action=action, payload=payload or {})
        self._entries.append(entry)
        self._total_recorded += 1
        if self._path is not None:
            # المادة 81: تعذّر الكتابة على القرص (قرص ممتلئ، صلاحيات)
            # لا يجوز أن يرتد كاستثناء يُسقط مسار تشغيل الذرة.
            try:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")
            except OSError as exc:
                _log.error("تعذّرت كتابة السجل إلى %s: %s", self._path, exc)
        return entry

    def for_atom(self, atom_id: int) -> list[JournalEntry]:
        return [e for e in self._entries if e.atom_id == atom_id]

    def tail(self, n: int = 100) -> list[JournalEntry]:
        if n <= 0:
            return []
        entries = list(self._entries)
        return entries[-n:]

    @property
    def total_recorded(self) -> int:
        """العدد الكلي منذ بدء العملية — لا يتأثر بسقف الذاكرة."""
        return self._total_recorded

    def __len__(self) -> int:
        return len(self._entries)
