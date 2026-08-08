"""
security — مخزن الأسرار المشفّر لـ QUANT_NQ
============================================
حزمة خارجية مستقلة تماماً عن النواة. `core/` لا يستوردها ولا يذكر
اسمها في أي ملف مختوم، ويمكن حذف هذا المجلد كاملاً فتعمل النواة كما هي.

المشكلة التي حلّها هذا الملف
-----------------------------
الخطة الأصلية افترضت "حقن `FileSecretProvider` عند إقلاع الذرة من
الخارج" — لكن **نقطة الحقن غير موجودة**: `AtomContext` عقد مجمّد
حقوله (`atom_id`, `config`, `logger`, `publish`, `subscribe`) وليس
فيها مكان لمزوّد أسرار، وإضافة حقل تعني فتح النواة المختومة وكسر كل
ذرة قائمة (المادة 41/64).

الحل الذي يحترم التجميد: **مُفرد على مستوى العملية**. المُشغِّل
(`scripts/run_core.py`، وهو خارج الختم) يهيّئ المزوّد **قبل** الإقلاع،
والذرة تطلبه عند الحاجة:

    from security import get_secret_provider

    token = get_secret_provider().get_secret("telegram_bot_token")

النواة لا تعلم بشيء من هذا، والذرة لا تعرف أي تنفيذ يخدمها.

بديل أنظف للذرات الجديدة: مرِّر السر عبر `config` في المانيفست بعد أن
يحقنه المُشغِّل. لكن ذلك يضع السر في سجلات اللوحة و`/api/atoms`، لذا
النداء المباشر أأمن للأسرار الحقيقية.
"""

from __future__ import annotations

import logging
import threading

from security.interfaces import ISecretProvider, SecretProviderState
from security.providers import (
    ChainSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    NullSecretProvider,
)

_log = logging.getLogger("quant_nq.security")

_lock = threading.Lock()
_provider: ISecretProvider | None = None


def set_secret_provider(provider: ISecretProvider) -> None:
    """يُثبّت المزوّد لهذه العملية. يُستدعى مرة واحدة عند الإقلاع."""
    global _provider
    with _lock:
        if _provider is not None:
            _log.warning("استُبدل مزوّد الأسرار أثناء التشغيل (%s ← %s)",
                         _provider.name, provider.name)
        _provider = provider
        _log.info("مزوّد الأسرار: %s [%s]", provider.name, provider.state.value)


def get_secret_provider() -> ISecretProvider:
    """يُرجع المزوّد الحالي، أو مزوّداً فارغاً إن لم يُهيّأ.

    لا يرمي أبداً: ذرة تطلب سراً في بيئة بلا مخزن تحصل على `None`
    وتتصرّف بحسب سياستها هي (المادة 27).
    """
    with _lock:
        if _provider is None:
            return NullSecretProvider()
        return _provider


def reset_secret_provider() -> None:
    """يفرّغ المزوّد ويزيله — للإيقاف النظيف وللاختبارات."""
    global _provider
    with _lock:
        if _provider is not None:
            _provider.clear()
        _provider = None


__all__ = [
    "ChainSecretProvider",
    "EnvSecretProvider",
    "FileSecretProvider",
    "ISecretProvider",
    "NullSecretProvider",
    "SecretProviderState",
    "get_secret_provider",
    "reset_secret_provider",
    "set_secret_provider",
]
