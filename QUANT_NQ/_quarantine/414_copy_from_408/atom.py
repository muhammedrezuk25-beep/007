"""
414 — تقييم الاستراتيجية (Strategy Evaluation)

⚠️ نفس قيد 360 حرفيًا — يحتاج حدث "نتيجة فعلية" غير مبني بعد. يشترك:
strategy.entry_confirmed (401 — حقيقية) + market.outcome.realized
(افتراضي، نفس افتراض 360). ينشر: strategy.evaluation_updated —
دقة تراكمية لقرارات الدخول المؤكَّدة (401)، لا استراتيجية فردية —
401 نفسها تجمع عدة استراتيجيات، فالتقييم هنا لقرار الدخول المُجمَّع.
"""

from __future__ import annotations

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._last_direction: dict[str, str] = {}
        self._correct_count = 0
        self._total_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe("strategy.entry_confirmed", self._on_entry_confirmed)
        context.subscribe("market.outcome.realized", self._on_outcome)
        context.logger.info("StrategyEvaluation(414) تمت تهيئته — ⚠️ market.outcome.realized افتراضي غير مبني")

    async def start(self) -> None:
        if self._context is not None:
            self._context.logger.info("StrategyEvaluation(414) بدأ التقييم")

    async def stop(self) -> None:
        # ⚠️ تطبيق المادة 16: تطهير كاش الحالات الداخلي عند الإيقاف الساخن لمنع تسريب الذاكرة
        self._last_direction.clear()
        self._correct_count = 0
        self._total_count = 0

    async def shutdown(self) -> None:
        # ⚠️ تطبيق المادة 16: تطهير كامل للحالات عند الإغلاق النهائي
        self._last_direction.clear()
        self._correct_count = 0
        self._total_count = 0

    async def health_check(self) -> HealthStatus:
        accuracy = (self._correct_count / self._total_count) if self._total_count > 0 else None
        return HealthStatus(state=HealthState.HEALTHY, message=f"دقة={accuracy} ({self._correct_count}/{self._total_count})")

    async def snapshot(self) -> dict:
        return {"last_direction": self._last_direction, "correct_count": self._correct_count, "total_count": self._total_count}

    async def restore(self, state: dict) -> None:
        self._last_direction = state.get("last_direction", {})
        self._correct_count = state.get("correct_count", 0)
        self._total_count = state.get("total_count", 0)

    # ------------------------------------------------------------------

    async def _on_entry_confirmed(self, payload: dict) -> None:
        if self._context is None:
            return
        # ⚠️ تطبيق المادة 32: حصر ومعالجة الاستثناءات داخلياً لتفادي تسريب الأخطاء لناقل الأحداث
        try:
            symbol = payload.get("symbol")
            direction = payload.get("direction")
            if not symbol or not direction:
                self._context.logger.warning("StrategyEvaluation(414) تلقى حدث دخول غير مكتمل البيانات")
                return
            self._last_direction[symbol] = direction
        except Exception as exc:
            self._context.logger.error("خطأ غير متوقع أثناء معالجة strategy.entry_confirmed: %s", exc, exc_info=True)

    async def _on_outcome(self, payload: dict) -> None:
        if self._context is None:
            return
        # ⚠️ تطبيق المادة 32: حصر ومعالجة الاستثناءات داخلياً لتفادي تسريب الأخطاء لناقل الأحداث
        try:
            symbol = payload.get("symbol")
            if not symbol:
                return
            direction = self._last_direction.get(symbol)
            if direction is None:
                return

            actual = payload.get("actual_direction")
            if not actual:
                self._context.logger.warning("StrategyEvaluation(414) تلقى حدث نتيجة يفتقد actual_direction")
                return

            predicted = "up" if direction == "BUY" else "down"
            is_correct = predicted == actual

            self._total_count += 1
            if is_correct:
                self._correct_count += 1

            # ⚠️ تطبيق المادة 31: سحب دقة التقريب من الإعدادات ديناميكياً لتفادي القيم الثابتة يدوياً
            precision = self._context.config.get("accuracy_precision", 4)
            accuracy = round(self._correct_count / self._total_count, precision)

            await self._context.publish(
                "strategy.evaluation_updated",
                {"symbol": symbol, "correct": is_correct, "accuracy": accuracy},
            )
        except Exception as exc:
            self._context.logger.error("خطأ غير متوقع أثناء معالجة market.outcome.realized: %s", exc, exc_info=True)