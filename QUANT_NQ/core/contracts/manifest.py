"""
Core.contracts.manifest
=========================
مخطط Manifest القياسي — Article 7 من CORE_CONSTITUTION_V1.1.

هذا الملف هو التعريف الوحيد لشكل manifest.yaml في كامل المشروع. أي حقل
يُضاف هنا يعني أن كل ذرة قادمة يمكنها استخدامه فورًا دون لمس Core
(Article 2 + Article 5). لا تُكتب هنا أي قيمة خاصة بذرة بعينها.

المدخلات: قاموس Python محمَّل من YAML (عبر core.manifest_loader).
المخرجات: AtomManifest مُتحقَّق منه بالكامل، أو pydantic.ValidationError.
"""

from __future__ import annotations

from enum import Enum

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


def _validate_config_against_schema(config: dict, schema: dict) -> None:
    """يتحقق من config مقابل config_schema المعلنَين معًا داخل نفس
    Manifest — لا ملف خارجي بأي اسم. المستخدم الجديد صريح: "أي جزء
    داخل Core يعتمد على اسم ملف... يعتبر مخالفة معمارية". config.yaml
    كان بالضبط هذا الاعتماد (اسم ملف ثابت)؛ أُزيل، وأصبح config جزءًا
    من نفس عقد Manifest الوحيد، يُتحقق منه أثناء Validate (Article 4/8)
    فور تحميل الملف، بدل انتظار خطوة Initialize لاحقًا."""
    if not schema:
        return
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(config), key=lambda e: list(e.path))
    except SchemaError as exc:
        raise ValueError(f"config_schema غير صالح بنيويًا: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — jsonschema قد يرفع أنواعًا داخلية غير SchemaError لمخطط شاذ
        raise ValueError(f"config_schema غير قابل للتطبيق: {exc}") from exc

    if errors:
        details = "؛ ".join(f"{'.'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors)
        raise ValueError(f"config لا يطابق config_schema المعلن داخل نفس الذرة: {details}")


def _require_valid_specifier(value: str) -> str:
    """يتحقق أن value قيد إصدار صالح (PEP 440) أو '*'/'' لأي إصدار.
    يُستدعى أثناء Validate (تحميل Manifest نفسه) بدل السماح لقيد فاسد
    بالمرور إلى أن ينهار لاحقًا وسط Dependency Resolver — نفس الخطأ لكن
    برسالة تُحدَّد فورًا بملف manifest.yaml المسؤول عنها."""
    if value.strip() in ("", "*"):
        return value
    try:
        SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ValueError(f"قيد إصدار غير صالح: {value!r} ({exc})") from exc
    return value


def _require_valid_version(value: str) -> str:
    """يتحقق أن value إصدار فعلي قابل للمقارنة (وليس مجرد نص عشوائي)."""
    try:
        Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"إصدار غير صالح: {value!r} ({exc})") from exc
    return value


class _StrictModel(BaseModel):
    """يرفض أي حقل غير معروف (يكشف أخطاء الكتابة في YAML فورًا برسالة
    واضحة)، يقلّم المسافات البيضاء الطرفية تلقائيًا (يمنع مرور قيم مثل
    اسم مكوَّن من مسافة واحدة فقط رغم min_length=1)، ويمنع التعديل بعد
    التحميل — Manifest عقد ثابت بعد التحقق."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class StartupMode(str, Enum):
    AUTO = "auto"       # تُشغَّل تلقائيًا ضمن ترتيب الإقلاع المحسوب
    MANUAL = "manual"   # تُسجَّل فقط، تُشغَّل عبر طلب صريح لاحقًا
    LAZY = "lazy"        # تُشغَّل عند أول اعتماد فعلي من ذرة أخرى


class AtomDependency(_StrictModel):
    """اعتمادية معلنة — Dependency Resolver يبني الرسم البياني من هذا
    الحقل حصرًا (Article 11)."""

    id: int = Field(..., ge=1, description="Atom ID الخاص بالاعتمادية")
    version: str = Field(
        default="*",
        description='قيد إصدار مثل ">=1.0.0,<2.0.0"، أو "*" لأي إصدار',
    )

    @field_validator("version")
    @classmethod
    def _validate_version_constraint(cls, v: str) -> str:
        return _require_valid_specifier(v)


class HealthConfig(_StrictModel):
    """إعدادات المراقبة — Health Manager يقرأها تلقائيًا دون أي تسجيل
    يدوي (Article 12 و 16)."""

    interval_ms: int = Field(default=5000, ge=100)
    timeout_ms: int = Field(default=2000, ge=10)
    failure_threshold: int = Field(
        default=3, ge=1,
        description="فحوصات فاشلة متتالية قبل اعتبار الذرة UNHEALTHY",
    )
    restart_on_failure: bool = Field(default=False)
    max_restarts: int = Field(default=3, ge=0)
    restart_backoff_ms: int = Field(default=1000, ge=0)

    @field_validator("timeout_ms")
    @classmethod
    def _timeout_below_interval(cls, v: int, info: ValidationInfo) -> int:
        interval = info.data.get("interval_ms")
        if interval is not None and v >= interval:
            raise ValueError("timeout_ms يجب أن يكون أصغر من interval_ms")
        return v


class AtomManifest(_StrictModel):
    """التعريف الكامل والوحيد لأي ذرة (Article 5 + Article 7). لا يُكتب
    أي جزء من هذا التعريف داخل Core لأي ذرة بعينها."""

    id: int = Field(..., ge=1, description="Atom ID — لا يتغيّر إطلاقًا (Article 6)")
    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1, description="إصدار الذرة نفسها")
    core_version: str = Field(..., min_length=1, description="قيد توافق مع Core")
    entrypoint: str = Field(
        default="atom:Atom",
        description=(
            "module:ClassName نسبةً لمجلد الذرة — Bootloader يحمّله ديناميكيًا. "
            "غير مذكور صراحةً في نص الدستور، أُضيف لأن Article 13 (الدستور الأول) "
            "ينص أن الذرة تأتي بكامل تعريفها داخل Manifest، وهذا يشمل مكان الكود الفعلي."
        ),
    )
    critical: bool = Field(
        default=False,
        description="true يمنع الإقلاع عند الفشل، false يستمر النظام (Article 21)",
    )
    dependencies: list[AtomDependency] = Field(default_factory=list)
    publishes: list[str] = Field(default_factory=list)
    subscribes: list[str] = Field(default_factory=list)
    startup_mode: StartupMode = Field(default=StartupMode.AUTO)
    health: HealthConfig = Field(default_factory=HealthConfig)
    config_schema: dict = Field(
        default_factory=dict,
        description="JSON Schema تتحقق منه القيمة config أدناه، داخل نفس الملف",
    )
    config: dict = Field(
        default_factory=dict,
        description=(
            "قيم إعداد الذرة الفعلية — داخل Manifest نفسه، وليست في ملف "
            "منفصل بأي اسم (لا config.yaml ولا غيره). Core لا يقرأ معنى أي "
            "حقل بداخله؛ فقط يتحقق بنيويًا من مطابقته لـ config_schema أعلاه "
            "فور تحميل هذا الملف (Article 4/8: Validate)."
        ),
    )
    metadata: dict = Field(
        default_factory=dict,
        description=(
            "Article 7 (V1.1): معلومات تنظيمية حرة فقط (تصنيف، فريق، وسوم...). "
            "Core لا يقرأها ولا يعتمد عليها إطلاقًا — لا فلترة ولا بحث بها داخل "
            "Registry. حلّت محل حقل family الذي كان مخصصًا في V1.0؛ لو أردت "
            "تصنيفًا شبيهًا بالعائلة اليوم ضعه هنا كأي مفتاح حر: "
            "metadata: {family: risk} مثلاً — Core يتعامل معه كأي مفتاح آخر، "
            "بلا أي معنى خاص، وبلا أي تعلّق ببنية مجلدات."
        ),
    )

    @field_validator("version")
    @classmethod
    def _validate_own_version(cls, v: str) -> str:
        return _require_valid_version(v)

    @field_validator("core_version")
    @classmethod
    def _validate_core_version_constraint(cls, v: str) -> str:
        return _require_valid_specifier(v)

    @field_validator("dependencies")
    @classmethod
    def _reject_duplicate_dependency_ids(cls, v: list[AtomDependency]) -> list[AtomDependency]:
        seen: set[int] = set()
        for dep in v:
            if dep.id in seen:
                raise ValueError(f"اعتمادية مكررة على نفس Atom ID داخل نفس الذرة: {dep.id}")
            seen.add(dep.id)
        return v

    @model_validator(mode="after")
    def _validate_config_matches_schema(self) -> "AtomManifest":
        _validate_config_against_schema(self.config, self.config_schema)
        return self
