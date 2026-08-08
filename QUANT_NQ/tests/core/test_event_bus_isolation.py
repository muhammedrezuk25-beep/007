"""
المادة 89 / 35 / 30 / 31 — عزل ناقل الأحداث
=============================================
المادة 89: "يُمنع على أي ذرة تالفة أو معطلة أو بطيئة التأثير على سرعة
بث وتوجيه الأحداث داخل الـ Event Bus؛ فجميع اشتراكات الأحداث معزولة
ومحمية برمجياً ضد الحجب والتعطيل."

المادة 31: الحمولة تحمل المصدر ونوع الحدث والبيانات.
"""

from __future__ import annotations

import asyncio

import pytest

from core.event_bus import EventBus


@pytest.mark.asyncio
async def test_hanging_subscriber_cannot_freeze_the_bus() -> None:
    """مشترك معلّق إلى الأبد: النشر يجب أن يعود ضمن المهلة، والمشترك
    السليم يجب أن يستلم حدثه."""
    bus = EventBus(dispatch_timeout_s=0.1)
    received: list[dict] = []

    async def hanging(_payload: dict) -> None:
        await asyncio.sleep(3600)

    async def healthy(payload: dict) -> None:
        received.append(payload)

    bus.subscribe("tick", hanging, subscriber="99")
    bus.subscribe("tick", healthy, subscriber="1")

    await asyncio.wait_for(bus.publish("tick", {"n": 1}, publisher="7"), timeout=2.0)
    assert len(received) == 1, "المادة 89: مشترك معلّق حجب البث عن مشترك سليم"


@pytest.mark.asyncio
async def test_raising_subscriber_does_not_affect_others() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def bad(_payload: dict) -> None:
        raise RuntimeError("ذرة تالفة")

    async def good(_payload: dict) -> None:
        seen.append("ok")

    bus.subscribe("e", bad, subscriber="1")
    bus.subscribe("e", good, subscriber="2")
    await bus.publish("e", {}, publisher="0")
    assert seen == ["ok"]


@pytest.mark.asyncio
async def test_publish_does_not_mutate_caller_payload() -> None:
    """حمولة المستدعي ملكه: Core لا يحقن فيها شيئًا في مكانها."""
    bus = EventBus()
    original = {"price": 100}
    await bus.publish("e", original, publisher="5")
    assert original == {"price": 100}, "publish عدّل قاموس المستدعي"


@pytest.mark.asyncio
async def test_each_subscriber_gets_an_independent_copy() -> None:
    """المادة 30/35: مشترك لا يستطيع تغيير ما يراه مشترك آخر."""
    bus = EventBus()
    second_view: list[dict] = []

    async def vandal(payload: dict) -> None:
        payload["price"] = 0
        payload["injected"] = True

    async def victim(payload: dict) -> None:
        second_view.append(dict(payload))

    bus.subscribe("e", vandal, subscriber="1")
    bus.subscribe("e", victim, subscriber="2")
    await bus.publish("e", {"price": 100}, publisher="0")

    assert second_view[0]["price"] == 100, "مشترك عدّل حمولة مشترك آخر"
    assert "injected" not in second_view[0]


@pytest.mark.asyncio
async def test_standard_fields_are_injected() -> None:
    """المادة 31: source + trace_id + timestamp تصل للمشترك تلقائيًا."""
    bus = EventBus()
    got: list[dict] = []
    bus.subscribe("e", lambda p: got.append(p), subscriber="1")
    await bus.publish("e", {"x": 1}, publisher="42")

    payload = got[0]
    assert payload["source"] == "42", "المادة 31: المصدر لم يصل للمشترك"
    assert isinstance(payload["trace_id"], str) and payload["trace_id"]
    assert isinstance(payload["timestamp"], float)
    assert payload["x"] == 1


@pytest.mark.asyncio
async def test_caller_supplied_standard_fields_are_preserved() -> None:
    bus = EventBus()
    got: list[dict] = []
    bus.subscribe("e", lambda p: got.append(p), subscriber="1")
    await bus.publish("e", {"trace_id": "T-1", "source": "custom"}, publisher="9")
    assert got[0]["trace_id"] == "T-1"
    assert got[0]["source"] == "custom"


def test_unsubscribe_leaves_no_phantom_event_names() -> None:
    """المادة 15: لا أثر في الذاكرة بعد إلغاء الاشتراك."""
    bus = EventBus()

    def handler(_p: dict) -> None:
        return None

    bus.unsubscribe("never_subscribed", handler)
    assert bus.event_names() == [], "أُنشئ مفتاح وهمي لحدث لم يُشترك فيه"

    bus.subscribe("real", handler, subscriber="1")
    bus.unsubscribe("real", handler)
    assert bus.event_names() == []


def test_unsubscribe_all_purges_every_trace_of_an_atom() -> None:
    bus = EventBus()
    for name in ("a", "b", "c"):
        bus.subscribe(name, lambda _p: None, subscriber="77")
    bus.subscribe("a", lambda _p: None, subscriber="88")

    removed = bus.unsubscribe_all("77")
    assert removed == 3
    assert bus.event_names() == ["a"]
    assert bus.subscriber_count("a") == 1


@pytest.mark.asyncio
async def test_sync_handlers_are_supported() -> None:
    bus = EventBus()
    seen: list[int] = []
    bus.subscribe("e", lambda p: seen.append(p["v"]), subscriber="1")
    await bus.publish("e", {"v": 3}, publisher="0")
    assert seen == [3]
