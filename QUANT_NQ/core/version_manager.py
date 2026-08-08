"""
Core.version_manager
======================
Article 20: كل Manifest يحتوي core_version لمنع تشغيل ذرة غير متوافقة،
وكل اعتمادية تحمل قيد إصدار خاص بها.

يستخدم packaging.specifiers (معيار PEP 440، متوافق عمليًا مع صياغات
semver الشائعة مثل ">=1.0.0,<2.0.0") بدل كتابة مُحلِّل يدوي عرضة للخطأ.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from core.__version__ import CORE_VERSION
from core.errors import VersionIncompatibleError

if TYPE_CHECKING:
    from core.contracts.manifest import AtomManifest

__all__ = [
    "CORE_VERSION", "is_compatible", "check_core_compatibility",
    "find_core_incompatible_atoms",
]


def is_compatible(actual_version: str, constraint: str) -> bool:
    """actual_version: إصدار فعلي مثل "1.2.0".
    constraint: قيد مثل ">=1.0.0,<2.0.0"، أو "*"/"" لأي إصدار."""

    if constraint.strip() in ("*", ""):
        return True
    try:
        return Version(actual_version) in SpecifierSet(constraint)
    except (InvalidVersion, InvalidSpecifier) as exc:
        raise VersionIncompatibleError(
            f"تعذّر تفسير الإصدار/القيد: actual={actual_version!r} constraint={constraint!r} ({exc})"
        ) from exc


def check_core_compatibility(atom_id: int, core_version_constraint: str) -> None:
    """يُستدعى لكل ذرة أثناء Bootloader.Validate قبل Register."""
    if not is_compatible(CORE_VERSION, core_version_constraint):
        raise VersionIncompatibleError(
            f"الذرة {atom_id} تتطلب core_version={core_version_constraint!r}، "
            f"لكن Core الحالي هو {CORE_VERSION}"
        )


def find_core_incompatible_atoms(manifests: list["AtomManifest"]) -> dict[int, str]:
    """نسخة غير رافعة للاستثناء من check_core_compatibility، لكل ذرة في
    القائمة دفعة واحدة — تُستخدم في حلقة الاستبعاد التدريجي بـ
    Bootloader، تمامًا مثل find_unresolvable_dependencies. لا ترفع حتى
    لو كان core_version نفسه نصًا فاسدًا (يُعامَل كعدم توافق، وليس عطلًا
    يُسقط الإقلاع بالكامل)."""
    result: dict[int, str] = {}
    for m in manifests:
        try:
            compatible = is_compatible(CORE_VERSION, m.core_version)
        except VersionIncompatibleError as exc:
            result[m.id] = str(exc)
            continue
        if not compatible:
            result[m.id] = (
                f"core_version={m.core_version!r} لا يوافق Core الحالي {CORE_VERSION}"
            )
    return result
