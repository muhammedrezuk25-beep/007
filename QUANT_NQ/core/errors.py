"""
Core.errors
============
شجرة الاستثناءات الموحّدة لكامل Core.

القاعدة: أي خطأ يحدث داخل ذرة أو أثناء التعامل معها يُلتقط ويُحوَّل إلى
أحد هذه الأنواع قبل أن يصل إلى Bootloader أو Health Manager، حتى لا
يُسمح لخطأ في ذرة واحدة بإسقاط العملية بأكملها (سياسة الأخطاء في
Project Spec + Article 30).
"""

from __future__ import annotations


class CoreError(Exception):
    """الأصل لكل استثناءات Core. لا يُطلق مباشرة."""
    # ⚠️ تطبيق المادة 89: خصائص رقمية ومعيارية للرصد والتوثيق الآلي
    code: int = 1000
    severity: str = "error"


# ---------------------------------------------------------------- Manifest --

class ManifestError(CoreError):
    code = 2000

class ManifestParseError(ManifestError):
    code = 2001

class DuplicateAtomIdError(ManifestError):
    code = 2002


# ------------------------------------------------------------ Dependencies --

class DependencyError(CoreError):
    code = 2100

class MissingDependencyError(DependencyError):
    code = 2101

class CircularDependencyError(DependencyError):
    code = 2102

class VersionIncompatibleError(DependencyError):
    code = 2103


# ------------------------------------------------------------------- Atom --

class AtomLifecycleError(CoreError):
    code = 3000

class AtomInitializationError(AtomLifecycleError):
    code = 3001

class AtomStartError(AtomLifecycleError):
    code = 3002

class AtomStopError(AtomLifecycleError):
    code = 3003

class AtomHealthCheckError(AtomLifecycleError):
    code = 3004

class CriticalAtomFailure(CoreError):
    code = 3005
    severity = "critical"


# ---------------------------------------------------------------- Runtime --

class EventBusError(CoreError):
    code = 3100

class RegistryError(CoreError):
    code = 3200

class UnknownAtomError(RegistryError):
    code = 3201

class ConfigError(CoreError):
    code = 3300

class SnapshotError(CoreError):
    code = 3400