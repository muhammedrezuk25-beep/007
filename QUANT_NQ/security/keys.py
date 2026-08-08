"""
security/keys.py
================
الحصول على مفتاح التشفير — دون تخزين المفتاح نفسه كنص دائم.

المشكلة التي يعالجها هذا الملف: مفتاح Fernet داخل متغير بيئة يبقى
قابلاً للقراءة طوال عمر العملية من `/proc/<pid>/environ`، ومن أي أداة
تشخيص، ومن أي عملية فرعية تُورَّث البيئة إليها، ويتسرّب إلى سجلات
الأعطال. سرٌّ دائم بجوار الملف المشفّر يُلغي فائدة التشفير.

الاستراتيجيات المتاحة، بترتيب الأفضلية:

1. **DPAPI (ويندوز)** — المفتاح مُغلَّف بحساب المستخدم في نظام التشغيل.
   لا يعمل إلا على نفس الجهاز وبنفس الحساب. بلا تدخل بشري عند الإقلاع.
2. **عبارة مرور تفاعلية** — تُطلب مرة عند الإقلاع، يُشتق منها المفتاح
   بـ Argon2id (أو PBKDF2 إن غاب)، ثم تُمحى العبارة من الذاكرة.
3. **متغير بيئة** — للتطوير والحاويات فقط، ومصحوب بتحذير صريح.

في كل الحالات: المفتاح المشتق يعيش في `bytearray` قابل للمسح، لا في
`str` غير قابل للمسح.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets as _secrets
import sys
from dataclasses import dataclass
from typing import Callable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_log = logging.getLogger("quant_nq.security.keys")

PBKDF2_ITERATIONS = 600_000  # توصية OWASP 2023 لـ SHA-256
SALT_BYTES = 16

try:  # argon2 اختياري — أقوى، ويُستعمل تلقائياً إن وُجد
    from argon2.low_level import Type as _ArgonType
    from argon2.low_level import hash_secret_raw as _argon2_raw

    _HAS_ARGON2 = True
except ImportError:  # pragma: no cover
    _HAS_ARGON2 = False


@dataclass(slots=True)
class KdfParams:
    """معاملات الاشتقاق — تُخزَّن **بجوار** النص المشفّر، لا سراً فيها."""

    algorithm: str = "pbkdf2-sha256"
    salt: bytes = b""
    iterations: int = PBKDF2_ITERATIONS
    memory_kib: int = 65536
    parallelism: int = 4

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "salt": base64.b64encode(self.salt).decode(),
            "iterations": self.iterations,
            "memory_kib": self.memory_kib,
            "parallelism": self.parallelism,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KdfParams:
        return cls(
            algorithm=data.get("algorithm", "pbkdf2-sha256"),
            salt=base64.b64decode(data.get("salt", "")),
            iterations=int(data.get("iterations", PBKDF2_ITERATIONS)),
            memory_kib=int(data.get("memory_kib", 65536)),
            parallelism=int(data.get("parallelism", 4)),
        )

    @staticmethod
    def new() -> KdfParams:
        algorithm = "argon2id" if _HAS_ARGON2 else "pbkdf2-sha256"
        return KdfParams(algorithm=algorithm, salt=_secrets.token_bytes(SALT_BYTES))


def derive_key(passphrase: bytes, params: KdfParams) -> bytearray:
    """يشتق مفتاح Fernet (32 بايت مُرمَّزة base64) من عبارة مرور.

    يُرجع `bytearray` لا `bytes`: الأول قابل للمسح في مكانه، والثاني
    كائن ثابت يبقى في الذاكرة حتى يجمعه جامع القمامة متى شاء.
    """
    if params.algorithm == "argon2id":
        if not _HAS_ARGON2:
            raise ValueError("المخزن يستعمل argon2id — ثبّت الحزمة: pip install argon2-cffi")
        raw = _argon2_raw(
            secret=bytes(passphrase), salt=params.salt, time_cost=3,
            memory_cost=params.memory_kib, parallelism=params.parallelism,
            hash_len=32, type=_ArgonType.ID,
        )
    else:
        raw = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32,
            salt=params.salt, iterations=params.iterations,
        ).derive(bytes(passphrase))

    key = bytearray(base64.urlsafe_b64encode(raw))
    wipe(bytearray(raw))
    return key


def wipe(buffer: bytearray | None) -> None:
    """يصفّر محتوى المخزن في مكانه.

    تنبيه صريح لا يجوز إخفاؤه: بايثون لا يضمن محو كل نسخة من الذاكرة.
    قد تكون نسخة قد نُسخت أثناء إعادة تخصيص، أو كُتبت في ملف المبادلة
    (swap). هذا يقلّل نافذة التعرّض ولا يلغيها. الضمان الحقيقي يأتي من
    عدم كتابة السر على القرص أصلاً، لا من المحو بعد الاستعمال.
    """
    if not buffer:
        return
    for i in range(len(buffer)):
        buffer[i] = 0


# ------------------------------------------------------- خلفيات المفتاح --

def _from_dpapi(blob_path) -> bytearray | None:  # noqa: ANN001
    """ويندوز فقط: يفكّ مفتاحاً مغلَّفاً بحساب المستخدم عبر DPAPI."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        encrypted = blob_path.read_bytes()
        blob_in = _BLOB(len(encrypted), ctypes.cast(
            ctypes.create_string_buffer(encrypted), ctypes.POINTER(ctypes.c_char)))
        blob_out = _BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            _log.error("DPAPI: تعذّر فك المفتاح — قد يكون أُنشئ بحساب مستخدم آخر")
            return None
        try:
            return bytearray(ctypes.string_at(blob_out.pbData, blob_out.cbData))
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception as exc:  # noqa: BLE001
        _log.error("DPAPI غير متاح: %s", exc)
        return None


def _from_passphrase_prompt(params: KdfParams) -> bytearray | None:
    """يطلب عبارة المرور مرة واحدة عند الإقلاع، ثم يمحوها."""
    if not sys.stdin or not sys.stdin.isatty():
        _log.warning("لا طرفية تفاعلية — تعذّر طلب عبارة المرور")
        return None
    import getpass

    try:
        entered = getpass.getpass("عبارة مرور مخزن الأسرار: ")
    except (EOFError, KeyboardInterrupt):
        _log.warning("أُلغي إدخال عبارة المرور")
        return None

    buffer = bytearray(entered.encode("utf-8"))
    try:
        return derive_key(buffer, params)
    finally:
        wipe(buffer)


def _from_env(params: KdfParams, env_var: str) -> bytearray | None:
    """للتطوير والحاويات فقط."""
    raw = os.environ.get(env_var)
    if not raw:
        return None
    _log.warning(
        "مفتاح المخزن مقروء من متغير البيئة %s — سرٌّ دائم قابل للقراءة من "
        "/proc/<pid>/environ ومن أي عملية فرعية. للتطوير فقط؛ استعمل DPAPI "
        "أو عبارة مرور في التشغيل الفعلي.",
        env_var,
    )
    if raw.startswith("pass:"):
        buffer = bytearray(raw[5:].encode("utf-8"))
        try:
            return derive_key(buffer, params)
        finally:
            wipe(buffer)
    return bytearray(raw.encode("utf-8"))  # مفتاح Fernet جاهز


def resolve_key(
    params: KdfParams, *,
    dpapi_blob=None,  # noqa: ANN001
    env_var: str = "QUANT_MASTER_KEY",
    allow_prompt: bool = True,
    strategies: list[Callable[[], bytearray | None]] | None = None,
) -> tuple[bytearray | None, str]:
    """يجرّب الاستراتيجيات بالترتيب ويُرجع (المفتاح، اسم الاستراتيجية)."""
    chain: list[tuple[str, Callable[[], bytearray | None]]] = []
    if dpapi_blob is not None and dpapi_blob.exists():
        chain.append(("dpapi", lambda: _from_dpapi(dpapi_blob)))
    if allow_prompt:
        chain.append(("passphrase", lambda: _from_passphrase_prompt(params)))
    chain.append(("env", lambda: _from_env(params, env_var)))

    if strategies:
        for fn in strategies:
            key = fn()
            if key:
                return key, "custom"

    for name, fn in chain:
        try:
            key = fn()
        except Exception as exc:  # noqa: BLE001 — لا استراتيجية تُسقط الباقي
            _log.error("فشلت استراتيجية المفتاح '%s': %s", name, exc)
            continue
        if key:
            _log.info("مفتاح المخزن من استراتيجية '%s'", name)
            return key, name
    return None, "none"
