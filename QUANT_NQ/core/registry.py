"""
Core.registry
==============
Article 6 (الدستور الأول) + Article 9 (الدستور النهائي): يحفظ فقط
Atom ID، الحالة، المرجع، دورة الحياة. لا يحتوي أي منطق أعمال ولا أي
تسجيل يدوي — كل إدخال يأتي من manifest تم اكتشافه تلقائيًا.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.contracts.atom import AtomBase, AtomState, HealthStatus
from core.contracts.manifest import AtomManifest
from core.errors import RegistryError, UnknownAtomError


@dataclass(slots=True)
class AtomRecord:
    """كل ما يعرفه Core عن ذرة واحدة أثناء التشغيل."""

    manifest: AtomManifest
    instance: AtomBase
    state: AtomState = AtomState.DISCOVERED
    last_health: HealthStatus | None = None
    registered_at: float = field(default_factory=time.time)
    restart_count: int = 0
    last_error: str | None = None

    @property
    def id(self) -> int:
        return self.manifest.id

    @property
    def critical(self) -> bool:
        return self.manifest.critical


class Registry:
    """المصدر الوحيد للحقيقة حول أي ذرة قيد التشغيل. مفهرس بـ Atom ID
    فقط — لا اسم ملف، لا اسم مجلد، لا اسم عائلة يُبنى عليه منطق البحث."""

    def __init__(self) -> None:
        self._atoms: dict[int, AtomRecord] = {}

    def register(self, manifest: AtomManifest, instance: AtomBase) -> AtomRecord:
        if manifest.id in self._atoms:
            raise RegistryError(f"Atom ID {manifest.id} مسجَّل مسبقًا")
        record = AtomRecord(manifest=manifest, instance=instance)
        self._atoms[manifest.id] = record
        return record

    def unregister(self, atom_id: int) -> None:
        self._atoms.pop(atom_id, None)

    def get(self, atom_id: int) -> AtomRecord:
        try:
            return self._atoms[atom_id]
        except KeyError:
            raise UnknownAtomError(f"لا توجد ذرة بالمعرّف {atom_id}") from None

    def find(self, atom_id: int) -> AtomRecord | None:
        return self._atoms.get(atom_id)

    def all(self) -> list[AtomRecord]:
        return list(self._atoms.values())

    def by_state(self, state: AtomState) -> list[AtomRecord]:
        return [r for r in self._atoms.values() if r.state == state]

    def set_state(self, atom_id: int, state: AtomState) -> None:
        self.get(atom_id).state = state

    def set_health(self, atom_id: int, health: HealthStatus) -> None:
        self.get(atom_id).last_health = health

    def set_error(self, atom_id: int, error: str | None) -> None:
        self.get(atom_id).last_error = error

    def __len__(self) -> int:
        return len(self._atoms)

    def __contains__(self, atom_id: int) -> bool:
        return atom_id in self._atoms
