"""
414 — تقييم الاستراتيجية (Strategy Evaluator)
يستلم الإشارات المدمجة ويقيم جودتها بناءً على الأداء التاريخي والتقييم المعياري.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus


@dataclass(slots=True, frozen=True)
class QualityScoreResult:
    symbol: str
    direction: str
    base_confidence: float
    performance_multiplier: float
    final_score: float
    evaluated_at: float


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._default_score: float = 50.0
        self._weight_performance: float = 0.5
        
        # تخزين مؤقت لمقاييس الأداء (مفتاح: الرمز، القيمة: معامل الأداء)
        self._performance_metrics: dict[str, float] = {}
        self._evaluated_count: int = 0
        self._last_evaluated_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        config = context.config
        self._default_score = float(config.get("default_score", 50.0))
        self._weight_performance = float(config.get("weight_performance", 0.5))

        # الاشتراك الآمن في الأحداث عبر واجهات العقد
        context.subscribe("strategy.merged_signal", self._on_merged_signal)
        context.subscribe("strategy.performance_tracked", self._on_performance_tracked)

        context.logger.info("StrategyEvaluator تمت تهيئته بنجاح (default_score=%.1f)", self._default_score)

    async def start(self) -> None:
        if self._context:
            self._context.logger.info("StrategyEvaluator بدأ العمل وجاهز لتقييم الإشارات")

    async def stop(self) -> None:
        if self._context:
            self._context.logger.info("StrategyEvaluator توقف بنجاح وتم تحرير الموارد")

    async def shutdown(self) -> None:
        # الملحق P: تصفية الذاكرة قسرياً لمنع تسريب الموارد
        self._performance_metrics.clear()

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            state=HealthState.HEALTHY,
            message=f"evaluated_count={self._evaluated_count}, tracked_symbols={len(self._performance_metrics)}"
        )

    async def snapshot(self) -> dict[str, Any]:
        return {
            "performance_metrics": self._performance_metrics,
            "evaluated_count": self._evaluated_count,
            "last_evaluated_at": self._last_evaluated_at,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        self._performance_metrics = state.get("performance_metrics", {})
        self._evaluated_count = state.get("evaluated_count", 0)
        self._last_evaluated_at = state.get("last_evaluated_at")

    # ------------------------------------------------------------------ معالجة الأحداث --

    async def _on_merged_signal(self, payload: dict[str, Any]) -> None:
        """معالجة الإشارة المدمجة الواردة من الذرة 413 وتقييم جودتها مع أمان الأنواع"""
        try:
            symbol = payload.get("symbol", "UNKNOWN")
            direction = payload.get("direction", "neutral")
            
            raw_confidence = payload.get("confidence")
            confidence = float(raw_confidence) if raw_confidence is not None else self._default_score

            # جلب معامل الأداء التاريخي (افتراضي 1.0 إذا لم يتوفر سجل)
            perf_multiplier = self._performance_metrics.get(symbol, 1.0)

            # معادلة التقييم: دمج الثقة الأساسية مع الأداء التاريخي
            normalized_confidence = confidence if confidence <= 1.0 else confidence / 100.0
            
            base_score = normalized_confidence * 100.0
            final_score = (base_score * (1.0 - self._weight_performance)) + ((base_score * perf_multiplier) * self._weight_performance)
            
            # تقييد النتيجة بين 0 و 100
            final_score = max(0.0, min(100.0, final_score))

            self._evaluated_count += 1
            self._last_evaluated_at = time.time()

            result = QualityScoreResult(
                symbol=symbol,
                direction=direction,
                base_confidence=confidence,
                performance_multiplier=perf_multiplier,
                final_score=final_score,
                evaluated_at=self._last_evaluated_at
            )

            if self._context:
                await self._context.publish("strategy.quality_scored", {
                    "symbol": result.symbol,
                    "direction": result.direction,
                    "base_confidence": result.base_confidence,
                    "performance_multiplier": result.performance_multiplier,
                    "final_score": result.final_score,
                    "evaluated_at": result.evaluated_at
                })

        except Exception as exc:  # noqa: BLE001
            # الملحق G و المادة 27: عزل تام للإستثناءات وعدم تسريبها للنواة
            if self._context:
                self._context.logger.error("خطأ أثناء تقييم الإشارة المدمجة: %s", exc)

    async def _on_performance_tracked(self, payload: dict[str, Any]) -> None:
        """تحديث مقاييس الأداء بناءً على تقارير الذرة 416 مع حماية نوع البيانات"""
        try:
            symbol = payload.get("symbol")
            raw_win_rate = payload.get("win_rate")
            
            if symbol and raw_win_rate is not None:
                win_rate = float(raw_win_rate)
                multiplier = win_rate * 2.0
                self._performance_metrics[str(symbol)] = max(0.1, min(2.0, multiplier))
        except Exception as exc:  # noqa: BLE001
            if self._context:
                self._context.logger.error("خطأ أثناء تحديث الأداء التاريخي: %s", exc)