"""
Core.event_bus
================
Article 10 (+ Article 7 في الدستور الأول): يعرف الأحداث فقط، لا الذرات.
لا يسمح بالتواصل المباشر بين الذرات (Article 23) — كل تواصل يمر من هنا.

ضمانات المادة 89 (حظر تعطيل الـ Event Bus المشترك):
  * كل معالج يُنفَّذ معزولًا: استثناؤه يُلتقط ولا يمسّ بقية المشتركين.
  * كل معالج محكوم بمهلة قصوى (`dispatch_timeout_s`) — مشترك بطيء أو
    معلّق لا يستطيع تجميد الناشر ولا بقية المشتركين.
  * كل مشترك يستلم **نسخته الخاصة** من الحمولة — لا يستطيع أي مشترك
    تعديل ما يراه غيره (المادة 30/35).

ضمان المادة 31 (الشكل القياسي الموحد لمحتوى الحدث): تُحقن الحقول
المعيارية `source` و`trace_id` و`timestamp` تلقائيًا إن غابت، فتصل كل
حمولة حاملةً مصدرها ومعرّف تتبّعها وزمنها دون أن تكتب الذرة ذلك يدويًا.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.logger import current_trace_id  # يُستخدم لحقن التتبع ديناميكياً

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]

_log = logging.getLogger("quant_nq.core.event_bus")

DEFAULT_DISPATCH_TIMEOUT_S = 30.0


@dataclass(slots=True)
class _Subscription:
    handler: Handler
    subscriber: str = ""


class EventBus:
    def __init__(self, *, dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S) -> None:
        self._subscribers: dict[str, list[_Subscription]] = defaultdict(list)
        self._dispatch_timeout_s = dispatch_timeout_s

    def subscribe(self, event_name: str, handler: Handler, *, subscriber: str = "") -> None:
        self._subscribers[event_name].append(_Subscription(handler=handler, subscriber=subscriber))

    def unsubscribe(self, event_name: str, handler: Handler) -> None:
        subs = self._subscribers.get(event_name)
        if subs is None:
            return  # لا نُنشئ مفتاحًا وهميًا لحدث لم يُشترك فيه قط
        kept = [s for s in subs if s.handler is not handler]
        if kept:
            self._subscribers[event_name] = kept
        else:
            del self._subscribers[event_name]

    def unsubscribe_all(self, subscriber: str) -> int:
        """يزيل كل اشتراكات مشترك واحد ولا يترك أي أثر في الذاكرة
        (المادة 15: تطهير كامل للـ Event Bus عند إزالة الذرة)."""
        removed = 0
        for event_name in list(self._subscribers):
            subs = self._subscribers[event_name]
            kept = [s for s in subs if s.subscriber != subscriber]
            removed += len(subs) - len(kept)
            if kept:
                self._subscribers[event_name] = kept
            else:
                del self._subscribers[event_name]
        return removed

    async def publish(
        self, event_name: str, payload: dict[str, Any] | None = None, *, publisher: str = ""
    ) -> None:
        # نسخة مستقلة: لا نعدّل قاموس المستدعي إطلاقًا.
        base: dict[str, Any] = dict(payload or {})

        # المادة 31: الحقول المعيارية تُحقن صمتًا لحماية الذرات القديمة.
        base.setdefault("source", publisher)
        base.setdefault("trace_id", str(uuid.uuid4()))
        base.setdefault("timestamp", time.time())

        subs = list(self._subscribers.get(event_name, ()))
        _log.debug(
            "نشر '%s' من '%s' إلى %d مشترك(ين) (trace_id: %s)",
            event_name, publisher or "؟", len(subs), base["trace_id"],
        )
        if not subs:
            return

        results = await asyncio.gather(
            *(self._invoke(sub, dict(base)) for sub in subs),
            return_exceptions=True,
        )
        for sub, result in zip(subs, results):
            if isinstance(result, asyncio.TimeoutError):
                _log.error(
                    "تجاوز مستمع للحدث '%s' (ناشر=%s) من '%s' المهلة %.1fث — عُزل ولم يُعطّل الناقل (المادة 89)",
                    event_name, publisher or "؟", sub.subscriber, self._dispatch_timeout_s,
                )
            elif isinstance(result, BaseException):
                _log.error(
                    "فشل مستمع للحدث '%s' (ناشر=%s) من '%s': %s",
                    event_name, publisher or "؟", sub.subscriber, result,
                    exc_info=result,
                )

    async def _invoke(self, sub: _Subscription, payload: dict[str, Any]) -> None:
        # ربط معرّف التتبع ببيئة التنفيذ (ContextVar) لتسجّله النواة في
        # كل رسالة Log تصدر عن هذا المعالج.
        token = current_trace_id.set(payload.get("trace_id"))
        try:
            result = sub.handler(payload)
            if asyncio.iscoroutine(result):
                # المادة 89: مهلة قصوى لكل معالج على حدة.
                await asyncio.wait_for(result, timeout=self._dispatch_timeout_s)
        finally:
            current_trace_id.reset(token)

    def event_names(self) -> list[str]:
        return list(self._subscribers.keys())

    def subscriber_count(self, event_name: str) -> int:
        return len(self._subscribers.get(event_name, ()))
