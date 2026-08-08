"""
security/providers.py
=====================
تنفيذات `ISecretProvider`. لا واحد منها يرمي استثناءً للمستهلك:
كل فشل يُسجَّل، ويُترجَم إلى حالة معلنة، ويُرجع `None`.

صيغة ملف المخزن (`secrets.enc`) — مغلّف JSON صريح لا كتلة بايتات صمّاء:

    {
      "format": "quant-nq-vault",
      "version": 1,
      "cipher": "fernet",
      "kdf": {"algorithm": "argon2id", "salt": "<b64>", ...},
      "payload": "<b64 ciphertext>"
    }

الـ salt ومعاملات الاشتقاق ليست أسراراً، ويجب أن تُخزَّن بجوار النص
المشفّر وإلا تعذّر فك التشفير على جهاز آخر. المحتوى وحده مشفّر.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from security.interfaces import ISecretProvider, SecretProviderState
from security.keys import KdfParams, resolve_key, wipe

_log = logging.getLogger("quant_nq.security.vault")

VAULT_FORMAT = "quant-nq-vault"
VAULT_VERSION = 1


class NullSecretProvider(ISecretProvider):
    """مزوّد فارغ — يجعل غياب المخزن حالة طبيعية لا فرعاً خاصاً في الكود."""

    @property
    def state(self) -> SecretProviderState:
        return SecretProviderState.UNAVAILABLE

    @property
    def name(self) -> str:
        return "null"

    def get_secret(self, key: str, default: Any = None) -> Any:
        return default

    def has_secret(self, key: str) -> bool:
        return False

    def available_keys(self) -> list[str]:
        return []

    def clear(self) -> None:
        return None


class EnvSecretProvider(ISecretProvider):
    """أسرار من متغيرات البيئة — للتطوير أو للحاويات المُدارة."""

    def __init__(self, prefix: str = "QUANT_SECRET_") -> None:
        self._prefix = prefix

    @property
    def state(self) -> SecretProviderState:
        return SecretProviderState.AVAILABLE

    @property
    def name(self) -> str:
        return f"env({self._prefix}*)"

    def _var(self, key: str) -> str:
        return self._prefix + key.upper()

    def get_secret(self, key: str, default: Any = None) -> Any:
        import os

        return os.environ.get(self._var(key), default)

    def has_secret(self, key: str) -> bool:
        import os

        return self._var(key) in os.environ

    def available_keys(self) -> list[str]:
        import os

        n = len(self._prefix)
        return sorted(k[n:].lower() for k in os.environ if k.startswith(self._prefix))

    def clear(self) -> None:
        return None


class FileSecretProvider(ISecretProvider):
    """مخزن مشفّر على القرص، مفكوك في الذاكرة العشوائية حصراً."""

    def __init__(
        self,
        vault_path: str | Path,
        *,
        dpapi_blob: str | Path | None = None,
        env_var: str = "QUANT_MASTER_KEY",
        allow_prompt: bool = True,
        auto_open: bool = True,
    ) -> None:
        self._path = Path(vault_path)
        self._dpapi_blob = Path(dpapi_blob) if dpapi_blob else None
        self._env_var = env_var
        self._allow_prompt = allow_prompt
        self._cache: dict[str, Any] = {}
        self._state = SecretProviderState.UNINITIALIZED
        self._key_source = "none"
        if auto_open:
            self.open()

    # ------------------------------------------------------------ فتح --

    def open(self) -> SecretProviderState:
        """يحاول فتح المخزن. **لا يرمي أبداً** — يُرجع الحالة الناتجة."""
        self._cache = {}

        if not self._path.exists():
            _log.info("لا مخزن أسرار في %s — يعمل النظام بلا أسرار", self._path)
            self._state = SecretProviderState.UNAVAILABLE
            return self._state

        envelope = self._read_envelope()
        if envelope is None:
            self._state = SecretProviderState.UNAVAILABLE
            return self._state

        params = KdfParams.from_dict(envelope.get("kdf") or {})
        key, source = resolve_key(
            params, dpapi_blob=self._dpapi_blob,
            env_var=self._env_var, allow_prompt=self._allow_prompt,
        )
        self._key_source = source

        if key is None:
            _log.error(
                "مخزن الأسرار موجود لكن لا مفتاح متاح — الحالة LOCKED. "
                "الذرات التي تحتاج أسراراً ستتصرّف بحسب سياستها، وCore يواصل العمل."
            )
            self._state = SecretProviderState.LOCKED
            return self._state

        try:
            payload = base64.b64decode(envelope["payload"])
            plaintext = Fernet(bytes(key)).decrypt(payload)
        except InvalidToken:
            # الرسالة تتجنّب عمداً التمييز بين "مفتاح خاطئ" و"ملف معطوب":
            # التمييز يفيد المهاجم في تخمين المفتاح.
            _log.error("تعذّر فك مخزن الأسرار (مفتاح غير مطابق أو ملف تالف) — LOCKED")
            self._state = SecretProviderState.LOCKED
            return self._state
        except Exception as exc:  # noqa: BLE001
            _log.error("خطأ غير متوقّع أثناء فك المخزن: %s — UNAVAILABLE", type(exc).__name__)
            self._state = SecretProviderState.UNAVAILABLE
            return self._state
        finally:
            wipe(key)

        try:
            data = json.loads(plaintext.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("محتوى المخزن يجب أن يكون كائناً")
        except Exception as exc:  # noqa: BLE001
            _log.error("محتوى المخزن غير صالح: %s — UNAVAILABLE", type(exc).__name__)
            self._state = SecretProviderState.UNAVAILABLE
            return self._state
        finally:
            wipe(bytearray(plaintext))

        self._cache = data
        self._state = SecretProviderState.AVAILABLE
        _log.info(
            "فُتح مخزن الأسرار: %d مفتاح (المفتاح من '%s')",
            len(self._cache), self._key_source,
        )
        return self._state

    def _read_envelope(self) -> dict | None:
        try:
            envelope = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.error("تعذّرت قراءة مغلّف المخزن %s: %s", self._path, type(exc).__name__)
            return None
        if envelope.get("format") != VAULT_FORMAT:
            _log.error("صيغة مخزن غير معروفة في %s", self._path)
            return None
        if int(envelope.get("version", 0)) > VAULT_VERSION:
            _log.error(
                "المخزن بإصدار %s أحدث من المدعوم (%s) — حدِّث الأدوات",
                envelope.get("version"), VAULT_VERSION,
            )
            return None
        return envelope

    # ------------------------------------------------------------ قراءة --

    @property
    def state(self) -> SecretProviderState:
        return self._state

    @property
    def name(self) -> str:
        return f"file({self._path.name})"

    def get_secret(self, key: str, default: Any = None) -> Any:
        if self._state is not SecretProviderState.AVAILABLE:
            _log.debug("طُلب السر '%s' والمخزن في حالة %s", key, self._state.value)
            return default
        return self._cache.get(key, default)

    def has_secret(self, key: str) -> bool:
        return self._state is SecretProviderState.AVAILABLE and key in self._cache

    def available_keys(self) -> list[str]:
        return sorted(self._cache)

    def clear(self) -> None:
        for k in list(self._cache):
            value = self._cache[k]
            if isinstance(value, str):
                self._cache[k] = "\0" * len(value)
            del self._cache[k]
        self._cache.clear()
        self._state = SecretProviderState.CLEARED
        _log.info("فُرِّغ مخزن الأسرار من الذاكرة")

    def health(self) -> dict[str, Any]:
        base = super().health()
        base["key_source"] = self._key_source
        base["vault_present"] = self._path.exists()
        return base


class ChainSecretProvider(ISecretProvider):
    """يجرّب عدة مزوّدات بالترتيب — أول من يملك المفتاح يفوز.

    يتيح: مخزن مشفّر للإنتاج، ومتغيرات بيئة للتطوير، دون أن تعرف الذرة
    أيّهما يخدمها.
    """

    def __init__(self, *providers: ISecretProvider) -> None:
        self._providers = [p for p in providers if p is not None]

    @property
    def state(self) -> SecretProviderState:
        for p in self._providers:
            if p.state is SecretProviderState.AVAILABLE:
                return SecretProviderState.AVAILABLE
        for p in self._providers:
            if p.state is SecretProviderState.LOCKED:
                return SecretProviderState.LOCKED
        return SecretProviderState.UNAVAILABLE

    @property
    def name(self) -> str:
        return "chain(" + " → ".join(p.name for p in self._providers) + ")"

    def get_secret(self, key: str, default: Any = None) -> Any:
        for p in self._providers:
            if p.has_secret(key):
                return p.get_secret(key, default)
        return default

    def has_secret(self, key: str) -> bool:
        return any(p.has_secret(key) for p in self._providers)

    def available_keys(self) -> list[str]:
        seen: list[str] = []
        for p in self._providers:
            for k in p.available_keys():
                if k not in seen:
                    seen.append(k)
        return sorted(seen)

    def clear(self) -> None:
        for p in self._providers:
            p.clear()

    def health(self) -> dict[str, Any]:
        base = super().health()
        base["members"] = [p.health() for p in self._providers]
        return base
