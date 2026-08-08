"""
Core.contracts.atom
=====================
العقد الوحيد الذي تلتزم به أي ذرة لتعمل مع Core (Article 3: "Core يعرف
فقط العقود العامة"). كل الدوال هنا async — Core مبني على asyncio حتى
يخدم WebSocket/REST API ويتحمل آلاف الذرات في عملية واحدة دون Overhead
خيوط تشغيل تقليدية.

كل ذرة تُصدِّر كلاسًا يرث AtomBase. Core لا يستورد أي كود خاص بذرة
بعينها؛ كل ما يفعله هو استدعاء هذه الدوال المعرَّفة هنا بشكل عام على أي
كائن يطابق العقد (Article 23: لا وصول مباشر بين الذرات، فقط عبر
Interfaces و Services و Event Bus).
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


class AtomState(str, enum.Enum):
    DISCOVERED = "discovered"
    REGISTERED = "registered"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNLOADED = "unloaded"


class HealthState(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HealthStatus:
    state: HealthState
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class LoggerProtocol(Protocol):
    """توصيف بنيوي (structural typing) بدل استيراد core.logger مباشرة —
    يفادي أي اعتمادية دائرية بين الوحدات."""

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]
SubscribeFn = Callable[[str, Callable[..., Any]], None]


@dataclass(frozen=True, slots=True)
class AtomContext:
    """كل ما تحتاجه الذرة من خدمات Core العامة، تُمرَّر مرة واحدة عبر
    initialize. الذرة لا تستورد أي وحدة Core مباشرة (Loose Coupling)."""

    atom_id: int
    config: dict[str, Any]
    logger: LoggerProtocol
    publish: PublishFn
    subscribe: SubscribeFn


class AtomBase(ABC):
    """أي دالة اختيارية لها تنفيذ افتراضي آمن هنا حتى لا تُجبَر كل ذرة
    على تنفيذها (Article 13: يُتجاوز بصمت إن لم تُدعم)."""

    @abstractmethod
    async def initialize(self, context: AtomContext) -> None:
        """تُستدعى مرة واحدة قبل start. تجهيز حالة داخلية فقط — لا عمل
        فعلي هنا."""

    @abstractmethod
    async def start(self) -> None:
        """بدء العمل الفعلي للذرة."""

    @abstractmethod
    async def stop(self) -> None:
        """إيقاف قابل للعكس — يجب أن يسمح بإعادة start() لاحقًا (Health
        Manager يستدعي هذه ثم start() لتنفيذ Restart). لا تُغلق هنا
        مواردَ لن تُحتاج إلا إذا كانت رخيصة إعادة فتحها."""

    async def shutdown(self) -> None:
        """Core V1.2 — قرار من داخل Core نفسها: مرحلة نهائية *غير قابلة
        للعكس*، منفصلة عمدًا عن stop(). تُستدعى مرة واحدة فقط عند إنهاء
        العملية بالكامل (لا أثناء أي دورة Restart — تلك تبقى stop()
        ثم start() فقط، لا تمسّ shutdown() إطلاقًا). هنا يُغلَق أي اتصال
        فعليًا، تُحرَّر أي موارد نهائيًا. الافتراضي: لا شيء — ذرة لا
        تحتاج تمييزًا بين إيقاف عابر وإنهاء فعلي تتجاهل هذه الدالة بأمان
        تامًا (متوافقة خلفيًا مع كل ذرة موجودة الآن)."""

    async def health_check(self) -> HealthStatus:
        """افتراضيًا: أي ذرة تعمل تُعتبر HEALTHY ما لم تُخصِّص هذه الدالة."""
        return HealthStatus(state=HealthState.HEALTHY)

    async def snapshot(self) -> dict[str, Any] | None:
        """إرجاع None يعني: هذه الذرة لا تدعم Snapshot — Snapshot Engine
        يتجاوزها دون خطأ (Article 13)."""
        return None

    async def restore(self, state: dict[str, Any]) -> None:
        """لا تُستدعى إطلاقًا إن كانت snapshot تُرجع None."""
        raise NotImplementedError
