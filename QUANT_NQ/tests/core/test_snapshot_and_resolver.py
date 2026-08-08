"""لقطات الحالة، محلّل الاعتماديات، ومدير الصحة."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.contracts.atom import AtomBase, AtomState, HealthState, HealthStatus
from core.contracts.manifest import AtomManifest
from core.dependency_resolver import resolve
from core.errors import CircularDependencyError, MissingDependencyError, VersionIncompatibleError
from core.event_bus import EventBus
from core.health_manager import HealthManager
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from core.snapshot_engine import SnapshotEngine


def manifest(atom_id: int, deps: list[dict] | None = None, version: str = "1.0.0") -> AtomManifest:
    return AtomManifest.model_validate({
        "id": atom_id, "name": f"a{atom_id}", "version": version,
        "core_version": ">=1.0.0", "dependencies": deps or [],
    })


# ------------------------------------------------------------- Resolver --

def test_version_mismatch_raises_its_own_error_type() -> None:
    """عدم توافق الإصدار ليس اعتمادية مفقودة — رمزان مختلفان (2103/2101)
    حتى يميّز بينهما أي راصد آلي."""
    manifests = [manifest(1, [{"id": 2, "version": ">=2.0.0"}]), manifest(2, version="1.0.0")]
    with pytest.raises(VersionIncompatibleError) as exc:
        resolve(manifests)
    assert exc.value.code == 2103


def test_missing_dependency_raises_missing_error() -> None:
    with pytest.raises(MissingDependencyError) as exc:
        resolve([manifest(1, [{"id": 404}])])
    assert exc.value.code == 2101


def test_circular_dependency_detected() -> None:
    with pytest.raises(CircularDependencyError):
        resolve([manifest(1, [{"id": 2}]), manifest(2, [{"id": 1}])])


def test_boot_order_respects_dependencies() -> None:
    graph = resolve([manifest(3, [{"id": 1}]), manifest(1), manifest(2, [{"id": 3}])])
    assert graph.boot_order.index(1) < graph.boot_order.index(3) < graph.boot_order.index(2)


def test_resolver_handles_large_graph_without_recursion_limit() -> None:
    """المادة 91: النواة يجب أن تصمد لآلاف الذرات — خوارزمية Kahn
    تكرارية لا عوديّة."""
    manifests = [manifest(1)] + [manifest(i, [{"id": i - 1}]) for i in range(2, 5001)]
    assert len(resolve(manifests).boot_order) == 5000


# ------------------------------------------------------------ Snapshots --

class _StatefulAtom(AtomBase):
    def __init__(self, state: dict | None = None) -> None:
        self.state = state if state is not None else {"n": 1}
        self.restored: dict | None = None

    async def initialize(self, context) -> None: pass  # noqa: ANN001
    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def snapshot(self) -> dict | None: return self.state
    async def restore(self, state: dict) -> None: self.restored = state


class _StatelessAtom(AtomBase):
    async def initialize(self, context) -> None: pass  # noqa: ANN001
    async def start(self) -> None: pass
    async def stop(self) -> None: pass


class _HangingAtom(_StatelessAtom):
    async def snapshot(self) -> dict | None:
        await asyncio.sleep(3600)
        return {}


@pytest.mark.asyncio
async def test_snapshot_write_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    registry = Registry()
    registry.register(manifest(1), _StatefulAtom({"balance": 42}))
    engine = SnapshotEngine(registry, tmp_path)

    await engine.snapshot_all()
    assert json.loads((tmp_path / "1.json").read_text(encoding="utf-8")) == {"balance": 42}
    assert list(tmp_path.glob("*.tmp")) == [], "بقيت ملفات مؤقتة بعد الكتابة"


@pytest.mark.asyncio
async def test_failed_serialization_preserves_previous_snapshot(tmp_path: Path) -> None:
    """حالة غير قابلة للتسلسل يجب ألا تُتلف اللقطة السليمة السابقة."""
    registry = Registry()
    atom = _StatefulAtom({"ok": 1})
    registry.register(manifest(1), atom)
    engine = SnapshotEngine(registry, tmp_path)
    await engine.snapshot_all()

    atom.state = {"bad": object()}  # لا يمكن تحويله إلى JSON
    report = await engine.snapshot_all()

    assert report.failed == [1]
    assert json.loads((tmp_path / "1.json").read_text(encoding="utf-8")) == {"ok": 1}, (
        "أُتلفت اللقطة السابقة بمحاولة كتابة فاشلة"
    )
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_hanging_atom_cannot_freeze_snapshot_of_the_system(tmp_path: Path) -> None:
    """المادة 81: ذرة معلّقة لا يجوز أن تجمّد إيقاف Core كله."""
    registry = Registry()
    registry.register(manifest(1), _HangingAtom())
    registry.register(manifest(2), _StatefulAtom({"x": 1}))
    engine = SnapshotEngine(registry, tmp_path, atom_timeout_s=0.1)

    report = await asyncio.wait_for(engine.snapshot_all(), timeout=3.0)
    assert report.failed == [1]
    assert report.captured == [2], "ذرة معلّقة منعت التقاط لقطة ذرة سليمة"


@pytest.mark.asyncio
async def test_atoms_without_snapshot_support_are_skipped(tmp_path: Path) -> None:
    registry = Registry()
    registry.register(manifest(1), _StatelessAtom())
    engine = SnapshotEngine(registry, tmp_path)
    report = await engine.snapshot_all()
    assert report.skipped == [1] and report.failed == []


@pytest.mark.asyncio
async def test_restore_round_trip(tmp_path: Path) -> None:
    registry = Registry()
    atom = _StatefulAtom({"v": 7})
    registry.register(manifest(1), atom)
    engine = SnapshotEngine(registry, tmp_path)

    await engine.snapshot_all()
    atom.state = {"v": 0}
    report = await engine.restore_all()

    assert report.restored == [1]
    assert atom.restored == {"v": 7}


@pytest.mark.asyncio
async def test_discard_removes_orphan_snapshot(tmp_path: Path) -> None:
    registry = Registry()
    registry.register(manifest(1), _StatefulAtom())
    engine = SnapshotEngine(registry, tmp_path)
    await engine.snapshot_all()

    assert engine.discard(1) is True
    assert engine.has_snapshot(1) is False
    assert engine.discard(1) is False


# -------------------------------------------------------- HealthManager --

class _FlakyAtom(_StatelessAtom):
    def __init__(self) -> None:
        self.healthy = False
        self.starts = 0

    async def start(self) -> None:
        self.starts += 1

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            state=HealthState.HEALTHY if self.healthy else HealthState.UNHEALTHY
        )


@pytest.mark.asyncio
async def test_unhealthy_atom_is_restarted_in_isolation() -> None:
    """المادة 84: إعادة تشغيل الذرة التالفة معزولة عن بقية الذرات."""
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())

    m = AtomManifest.model_validate({
        "id": 1, "name": "flaky", "version": "1.0.0", "core_version": ">=1.0.0",
        "health": {"interval_ms": 100, "timeout_ms": 50, "failure_threshold": 1,
                    "restart_on_failure": True, "max_restarts": 3, "restart_backoff_ms": 0},
    })
    atom = _FlakyAtom()
    registry.register(m, atom)
    registry.set_state(1, AtomState.RUNNING)

    health.watch(1)
    await asyncio.sleep(0.5)
    await health.stop()

    assert atom.starts >= 1, "لم تُعَد الذرة غير الصحية للتشغيل"


@pytest.mark.asyncio
async def test_watch_task_is_cleaned_up_when_atom_leaves_running() -> None:
    """مهمة مراقبة منتهية يجب ألا تبقى مسجَّلة، وإلا تعذّرت إعادة
    مراقبة نفس الذرة بعد إعادة تشغيلها."""
    registry = Registry()
    health = HealthManager(registry, EventBus(), Journal(), Metrics())
    registry.register(manifest(1), _StatelessAtom())
    registry.set_state(1, AtomState.STOPPED)

    health.watch(1)
    await asyncio.sleep(0.05)

    assert not health.is_watching(1), "بقيت مهمة مراقبة منتهية عالقة في _tasks"
    await health.stop()
