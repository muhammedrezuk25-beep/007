"""
Core.contracts.services
=========================
المادة 63 — إلزامية وجود واجهة رسمية لكل خدمة عامة:
"تلتزم النواة بتوفير واجهة عامة مجردة (Abstract Interface / Protocol)
لجميع الخدمات العامة المشتركة المسؤولة عن تسييرها، لتفادي الـ Tight
Coupling وحماية النواة والذرات من الكسر."

المادة 70 — هذه الواجهات جزء أصيل من النواة المجمدة، ولا تتغير إلا
بإصدار معماري جديد.

المادة 64/65 — الغرض العملي: يمكن إعادة كتابة `EventBus` أو `Journal`
أو `Metrics` داخليًا بالكامل دون كسر أي ذرة، ما دامت التوقيعات هنا
ثابتة. كل ما تراه الذرة من Core موصوف هنا وهنا فقط.

هذه توصيفات بنيوية (`typing.Protocol`) لا كلاسات أساس: لا تفرض وراثة،
ولا تُستورد داخل الذرات وقت التشغيل، وتُستخدم للتحقق الثابت (mypy)
ولتوثيق العقد. التنفيذ الفعلي في `core/*.py` يطابقها دون أن يرث منها
(Duck Typing مُوثَّق).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from core.contracts.atom import AtomState, HealthStatus

EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


@runtime_checkable
class EventBusProtocol(Protocol):
    """ناقل الأحداث العام (المادة 32/33/34/89)."""

    def subscribe(
        self, event_name: str, handler: EventHandler, *, subscriber: str = ""
    ) -> None: ...

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None: ...

    def unsubscribe_all(self, subscriber: str) -> int: ...

    async def publish(
        self, event_name: str, payload: dict[str, Any] | None = None, *, publisher: str = ""
    ) -> None: ...

    def event_names(self) -> list[str]: ...

    def subscriber_count(self, event_name: str) -> int: ...


@runtime_checkable
class JournalProtocol(Protocol):
    """سجل append-only مفهرس بـ Atom ID (المادة 83)."""

    def record(
        self, atom_id: int, action: str, payload: dict[str, Any] | None = None
    ) -> Any: ...

    def for_atom(self, atom_id: int) -> list[Any]: ...

    def tail(self, n: int = 100) -> list[Any]: ...


@runtime_checkable
class MetricsProtocol(Protocol):
    """مقاييس عامة بمفتاح (Atom ID + اسم) فقط."""

    def increment(self, atom_id: int, name: str, value: int = 1) -> None: ...

    def gauge(self, atom_id: int, name: str, value: float) -> None: ...

    def timer(self, atom_id: int, name: str, seconds: float) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...

    def for_atom(self, atom_id: int) -> dict[str, Any]: ...


@runtime_checkable
class RegistryProtocol(Protocol):
    """سجل الحالة — منطقة محرَّمة على الذرات (المادة 90). الواجهة معلنة
    هنا لأن خدمات Core الداخلية تتخاطب معها، لا الذرات."""

    def register(self, manifest: Any, instance: Any) -> Any: ...

    def unregister(self, atom_id: int) -> None: ...

    def find(self, atom_id: int) -> Any | None: ...

    def all(self) -> list[Any]: ...

    def by_state(self, state: AtomState) -> list[Any]: ...

    def set_state(self, atom_id: int, state: AtomState) -> None: ...

    def set_health(self, atom_id: int, health: HealthStatus) -> None: ...

    def set_error(self, atom_id: int, error: str | None) -> None: ...


@runtime_checkable
class HealthManagerProtocol(Protocol):
    """مراقبة دورية وإعادة تشغيل معزولة (المادة 13/84)."""

    def watch(self, atom_id: int) -> None: ...

    def unwatch(self, atom_id: int) -> None: ...

    def is_watching(self, atom_id: int) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


__all__ = [
    "EventBusProtocol",
    "EventHandler",
    "HealthManagerProtocol",
    "JournalProtocol",
    "MetricsProtocol",
    "RegistryProtocol",
]
