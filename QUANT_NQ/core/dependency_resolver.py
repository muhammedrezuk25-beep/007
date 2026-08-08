"""
Core.dependency_resolver
==========================
Article 11: يبني Dependency Graph من Manifest فقط. لا يعتمد على أسماء
الملفات أو المجلدات أو ترتيب المشروع.

يُخرج ترتيب إقلاع (topological order عبر خوارزمية Kahn) ويكتشف الحلقات
الدائرية والاعتماديات المفقودة وعدم توافق الإصدارات.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from core.contracts.manifest import AtomManifest
from core.errors import CircularDependencyError, MissingDependencyError, VersionIncompatibleError
from core.version_manager import is_compatible


@dataclass(frozen=True, slots=True)
class ResolvedGraph:
    boot_order: list[int]           # Atom IDs بترتيب الإقلاع الصحيح
    edges: dict[int, list[int]]     # atom_id -> قائمة معرّفات يعتمد عليها


def resolve(manifests: list[AtomManifest]) -> ResolvedGraph:
    by_id = {m.id: m for m in manifests}
    edges: dict[int, list[int]] = {m.id: [dep.id for dep in m.dependencies] for m in manifests}

    _check_missing(by_id, edges)
    _check_version_compat(by_id)
    order = _topological_sort(edges)

    return ResolvedGraph(boot_order=order, edges=edges)


def _check_missing(by_id: dict[int, AtomManifest], edges: dict[int, list[int]]) -> None:
    for atom_id, deps in edges.items():
        for dep_id in deps:
            if dep_id not in by_id:
                raise MissingDependencyError(
                    f"الذرة {atom_id} تعتمد على {dep_id} غير الموجود في المشروع"
                )


def _check_version_compat(by_id: dict[int, AtomManifest]) -> None:
    for manifest in by_id.values():
        for dep in manifest.dependencies:
            target = by_id[dep.id]
            if not is_compatible(target.version, dep.version):
                # عدم توافق إصدار ليس اعتمادية مفقودة — لكل حالة نوع
                # خطأ ورمز رقمي خاص بها (2103 مقابل 2101) حتى يستطيع أي
                # راصد آلي التمييز بينهما.
                raise VersionIncompatibleError(
                    f"الذرة {manifest.id} تطلب {dep.id} بإصدار {dep.version}، "
                    f"لكن الموجود هو {target.version}"
                )


def _kahn_order(edges: dict[int, list[int]]) -> tuple[list[int], set[int]]:
    """خوارزمية Kahn الأساسية — تفادي recursion عميق قد يفشل مع آلاف
    الذرات (Article 28: حتى 10000). يُرجع (الترتيب المحلول، الذرات
    العالقة في حلقة دائرية إن وُجدت) دون رفع أي استثناء."""

    dependents: dict[int, list[int]] = {node: [] for node in edges}
    indegree: dict[int, int] = {}
    for node, deps in edges.items():
        # اعتمادية على معرّف خارج الرسم تُتجاهَل هنا: `_check_missing`
        # هو المسؤول عن الإبلاغ عنها. هذه الدالة يجب أن تبقى صالحة
        # للاستدعاء على أي رسم جزئي دون KeyError.
        known = [d for d in deps if d in dependents]
        indegree[node] = len(known)
        for dep_id in known:
            dependents[dep_id].append(node)

    ready = deque(sorted(n for n, d in indegree.items() if d == 0))
    order: list[int] = []

    while ready:
        node = ready.popleft()
        order.append(node)
        newly_ready = []
        for dependent in dependents[node]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                newly_ready.append(dependent)
        ready.extend(sorted(newly_ready))

    stuck = set(edges) - set(order)
    return order, stuck


def _topological_sort(edges: dict[int, list[int]]) -> list[int]:
    order, stuck = _kahn_order(edges)
    if stuck:
        raise CircularDependencyError(f"حلقة دائرية تشمل الذرات: {sorted(stuck)}")
    return order


def find_unresolvable_dependencies(manifests: list[AtomManifest]) -> dict[int, list[int]]:
    """يرجع {atom_id: [dep_ids غير قابلة للحل]} لأي اعتمادية غير موجودة
    أو غير متوافقة الإصدار، دون رفع أي استثناء — حتى لو كان قيد الإصدار
    نفسه نصًا فاسدًا غير قابل للتفسير (يُعامَل كعدم توافق، وليس عطلًا
    يُسقط استدعاء هذه الدالة بأكمله). يستخدمها Bootloader لاستبعاد
    الأطراف غير الحرجة تدريجيًا بدل إسقاط الإقلاع بالكامل بسبب ذرة واحدة
    (Fault Tolerant)."""

    by_id = {m.id: m for m in manifests}
    result: dict[int, list[int]] = {}
    for m in manifests:
        bad = []
        for dep in m.dependencies:
            target = by_id.get(dep.id)
            if target is None:
                bad.append(dep.id)
                continue
            try:
                if not is_compatible(target.version, dep.version):
                    bad.append(dep.id)
            except VersionIncompatibleError:
                # قيد إصدار فاسد فعليًا: تحقُّق Manifest يمنع هذا عادةً،
                # لكن هذه الدالة يجب أن تبقى غير رافعة على الإطلاق مهما
                # كان مصدر البيانات — دفاع من الدرجة الثانية.
                bad.append(dep.id)
        if bad:
            result[m.id] = bad
    return result


def find_cycle_members(manifests: list[AtomManifest]) -> set[int]:
    """يرجع كل الذرات المتورطة في أي حلقة دائرية دون رفع استثناء."""
    ids = {m.id for m in manifests}
    edges = {m.id: [dep.id for dep in m.dependencies if dep.id in ids] for m in manifests}
    _, stuck = _kahn_order(edges)
    return stuck
