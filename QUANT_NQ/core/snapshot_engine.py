"""
Core.snapshot_engine
======================
Article 13 (الدستور الأول والثاني): أي ذرة تدعم Snapshot (تُنفِّذ
snapshot() وتُرجع غير None) تُلتقط تلقائيًا. أي ذرة لا تدعمه (تُرجع
None أو ترث التنفيذ الافتراضي) تُتجاوز دون خطأ — لا حاجة لأي تسجيل
يدوي مسبق.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from core.registry import Registry

_log = logging.getLogger("quant_nq.core.snapshot_engine")

# مهلة قصوى لكل استدعاء snapshot()/restore() داخل ذرة. بدونها تستطيع
# ذرة واحدة معلّقة أن تجمّد إيقاف Core بأكمله (المادة 81/89).
DEFAULT_ATOM_TIMEOUT_S = 10.0


@dataclass(slots=True)
class SnapshotReport:
    captured: list[int] = field(default_factory=list)
    restored: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


class SnapshotEngine:
    def __init__(
        self, registry: Registry, storage_dir: Path, *,
        atom_timeout_s: float = DEFAULT_ATOM_TIMEOUT_S,
    ) -> None:
        self._registry = registry
        self._storage_dir = storage_dir
        self._atom_timeout_s = atom_timeout_s
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    async def snapshot_all(self) -> SnapshotReport:
        report = SnapshotReport()
        for record in self._registry.all():
            try:
                state = await asyncio.wait_for(
                    record.instance.snapshot(), timeout=self._atom_timeout_s
                )
                if state is None:
                    report.skipped.append(record.id)
                    continue
                self._write(record.id, state)
            except NotImplementedError:
                report.skipped.append(record.id)
                continue
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي، أو state غير قابل للتسلسل JSON، أو خطأ قرص
                _log.error("فشل snapshot للذرة %s: %s", record.id, exc)
                report.failed.append(record.id)
                continue

            report.captured.append(record.id)
        return report

    async def restore_all(self) -> SnapshotReport:
        report = SnapshotReport()
        for record in self._registry.all():
            try:
                state = self._read(record.id)
            except Exception as exc:  # noqa: BLE001 — ملف snapshot تالف أو JSON غير صالح
                _log.error("فشل قراءة snapshot المخزَّن للذرة %s: %s", record.id, exc)
                report.failed.append(record.id)
                continue

            if state is None:
                report.skipped.append(record.id)
                continue
            try:
                await asyncio.wait_for(
                    record.instance.restore(state), timeout=self._atom_timeout_s
                )
                report.restored.append(record.id)
            except NotImplementedError:
                report.skipped.append(record.id)
            except Exception as exc:  # noqa: BLE001
                _log.error("فشل استعادة snapshot للذرة %s: %s", record.id, exc)
                report.failed.append(record.id)
        return report

    def discard(self, atom_id: int) -> bool:
        """يحذف لقطة ذرة أُزيلت نهائيًا — يمنع تراكم لقطات يتيمة
        لذرات لم تعد موجودة."""
        path = self._path_for(atom_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def has_snapshot(self, atom_id: int) -> bool:
        return self._path_for(atom_id).exists()

    def _write(self, atom_id: int, state: dict) -> None:
        """كتابة ذرّية: نُسلسل أولًا (فشل التسلسل لا يمسّ الملف القديم)،
        نكتب لملف مؤقت في نفس المجلد، ثم `os.replace` — عملية استبدال
        ذرّية على مستوى نظام الملفات. انقطاع الكهرباء وسط الكتابة يترك
        اللقطة السابقة سليمة بدل ملف JSON مبتور."""
        data = json.dumps(state, ensure_ascii=False)
        target = self._path_for(atom_id)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._storage_dir, prefix=f".{atom_id}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _read(self, atom_id: int) -> dict | None:
        path = self._path_for(atom_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _path_for(self, atom_id: int) -> Path:
        return self._storage_dir / f"{atom_id}.json"
