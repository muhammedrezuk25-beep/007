"""
security/interfaces.py
======================
العقد المجرد لمزوّد الأسرار — معزول تماماً عن النواة.

`core/` لا يعرف أن هذه الحزمة موجودة، ولا يستوردها، ولا يُذكر اسمها في
أي ملف مختوم. هذا شرط المادة 41: القدرة الجديدة تُضاف بالإضافة، لا
بتعديل النواة.

الفلسفة (مطابقة للمادة 81/87): مزوّد الأسرار **لا يرمي استثناءً**
للمستهلك ولا يُسقط النظام. يسجّل، ويدخل حالة معلنة، ويُرجع `None`.
الذرة هي التي تقرر ماذا تفعل بغياب سرها — وهذا قرارها وحدها (المادة 27).
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import Any


class SecretProviderState(str, enum.Enum):
    """حالة معلنة يمكن رصدها، بدل استثناء يكسر التشغيل."""

    UNINITIALIZED = "uninitialized"   # لم يُفتح بعد
    AVAILABLE = "available"           # جاهز، الأسرار في الذاكرة
    LOCKED = "locked"                 # المخزن موجود لكن المفتاح غير متاح
    UNAVAILABLE = "unavailable"       # لا مخزن، أو تعذّرت قراءته
    CLEARED = "cleared"               # فُرِّغ عمداً بعد الإيقاف


class ISecretProvider(ABC):
    """الواجهة الوحيدة التي تراها الذرة.

    تعمّد ألا تتضمن أي إشارة إلى Fernet أو AES أو ملفات أو متغيرات
    بيئة: استبدال التنفيذ بـ DPAPI أو Azure Key Vault أو HashiCorp
    Vault لا يمسّ سطراً واحداً في أي ذرة.
    """

    @property
    @abstractmethod
    def state(self) -> SecretProviderState: ...

    @property
    @abstractmethod
    def name(self) -> str:
        """اسم قصير للتشخيص — لا يكشف أي محتوى."""

    @abstractmethod
    def get_secret(self, key: str, default: Any = None) -> Any:
        """يُرجع السر أو `default`. لا يرمي أبداً."""

    @abstractmethod
    def has_secret(self, key: str) -> bool: ...

    @abstractmethod
    def available_keys(self) -> list[str]:
        """أسماء المفاتيح فقط — لا قيمها. للتشخيص والفحص الصحي."""

    @abstractmethod
    def clear(self) -> None:
        """تفريغ الذاكرة من كل أثر للأسرار."""

    def health(self) -> dict[str, Any]:
        """تقرير آمن للعرض في اللوحة — بلا أي قيمة سرية."""
        return {
            "provider": self.name,
            "state": self.state.value,
            "key_count": len(self.available_keys()),
        }
