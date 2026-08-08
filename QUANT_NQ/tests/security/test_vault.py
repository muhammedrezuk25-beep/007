"""
اختبارات مخزن الأسرار
======================
المبدأ الحاكم: **المزوّد لا يرمي أبداً**. كل فشل يصير حالة معلنة و`None`.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from security import (
    ChainSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    NullSecretProvider,
    get_secret_provider,
    reset_secret_provider,
    set_secret_provider,
)
from security.interfaces import ISecretProvider, SecretProviderState
from security.keys import KdfParams, derive_key, wipe
from security.providers import VAULT_FORMAT, VAULT_VERSION

PASSPHRASE = "عبارة-اختبار-طويلة-وقوية".encode("utf-8")
SECRETS = {"telegram_bot_token": "123:ABCDEF", "mt5_password": "hunter2"}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    params = KdfParams(algorithm="pbkdf2-sha256", salt=b"0123456789abcdef", iterations=1000)
    key = derive_key(bytearray(PASSPHRASE), params)
    token = Fernet(bytes(key)).encrypt(json.dumps(SECRETS).encode("utf-8"))
    wipe(key)

    path = tmp_path / "secrets.enc"
    path.write_text(json.dumps({
        "format": VAULT_FORMAT, "version": VAULT_VERSION, "cipher": "fernet",
        "kdf": params.to_dict(), "payload": base64.b64encode(token).decode(),
    }), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("QUANT_MASTER_KEY", raising=False)
    reset_secret_provider()
    yield
    reset_secret_provider()


def _open(path: Path, passphrase: bytes = PASSPHRASE, monkeypatch=None) -> FileSecretProvider:
    os.environ["QUANT_MASTER_KEY"] = "pass:" + passphrase.decode("utf-8")
    try:
        return FileSecretProvider(path, allow_prompt=False)
    finally:
        os.environ.pop("QUANT_MASTER_KEY", None)


# ------------------------------------------------------ المسار السعيد --

def test_secrets_are_readable_with_correct_passphrase(vault: Path) -> None:
    provider = _open(vault)
    assert provider.state is SecretProviderState.AVAILABLE
    assert provider.get_secret("telegram_bot_token") == "123:ABCDEF"
    assert provider.has_secret("mt5_password")


def test_nothing_readable_appears_on_disk(vault: Path) -> None:
    """الأثر الوحيد على القرص ملف مشفّر — لا قيمة صريحة فيه."""
    raw = vault.read_bytes()
    for value in SECRETS.values():
        assert value.encode() not in raw, "سرٌّ ظاهر بنص صريح على القرص"


def test_only_key_names_are_exposed(vault: Path) -> None:
    provider = _open(vault)
    assert provider.available_keys() == ["mt5_password", "telegram_bot_token"]
    assert "hunter2" not in json.dumps(provider.health())


# ------------------------------------------- لا استثناء يكسر النظام --

def test_wrong_passphrase_returns_none_and_locks(vault: Path) -> None:
    provider = _open(vault, "عبارة-خاطئة".encode("utf-8"))
    assert provider.state is SecretProviderState.LOCKED
    assert provider.get_secret("telegram_bot_token") is None
    assert provider.get_secret("telegram_bot_token", "افتراضي") == "افتراضي"


def test_missing_key_source_locks_without_raising(vault: Path) -> None:
    provider = FileSecretProvider(vault, allow_prompt=False)
    assert provider.state is SecretProviderState.LOCKED
    assert provider.get_secret("anything") is None


def test_missing_vault_file_is_unavailable_not_fatal(tmp_path: Path) -> None:
    provider = FileSecretProvider(tmp_path / "nope.enc", allow_prompt=False)
    assert provider.state is SecretProviderState.UNAVAILABLE
    assert provider.get_secret("x") is None


@pytest.mark.parametrize("content", [
    "ليس JSON إطلاقاً",
    '{"format": "something-else"}',
    '{"format": "quant-nq-vault", "version": 999}',
    '{"format": "quant-nq-vault", "version": 1, "payload": "!!!غير صالح"}',
])
def test_corrupt_vault_never_raises(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.enc"
    path.write_text(content, encoding="utf-8")
    provider = _open(path)
    assert provider.state in (SecretProviderState.LOCKED, SecretProviderState.UNAVAILABLE)
    assert provider.get_secret("x") is None


def test_truncated_ciphertext_never_raises(vault: Path) -> None:
    envelope = json.loads(vault.read_text(encoding="utf-8"))
    envelope["payload"] = envelope["payload"][:20]
    vault.write_text(json.dumps(envelope), encoding="utf-8")
    assert _open(vault).get_secret("telegram_bot_token") is None


# ------------------------------------------------------------ التفريغ --

def test_clear_removes_everything(vault: Path) -> None:
    provider = _open(vault)
    provider.clear()
    assert provider.state is SecretProviderState.CLEARED
    assert provider.available_keys() == []
    assert provider.get_secret("telegram_bot_token") is None


# ------------------------------------------------------------ السلسلة --

def test_chain_falls_back_to_env(vault: Path, monkeypatch) -> None:
    monkeypatch.setenv("QUANT_SECRET_EXTRA_KEY", "من-البيئة")
    chain = ChainSecretProvider(_open(vault), EnvSecretProvider())
    assert chain.get_secret("telegram_bot_token") == "123:ABCDEF"
    assert chain.get_secret("extra_key") == "من-البيئة"


def test_chain_survives_a_locked_member(vault: Path, monkeypatch) -> None:
    """عضو مقفل لا يمنع بقية السلسلة من الخدمة."""
    monkeypatch.setenv("QUANT_SECRET_TOKEN", "احتياطي")
    locked = FileSecretProvider(vault, allow_prompt=False)
    chain = ChainSecretProvider(locked, EnvSecretProvider())
    assert chain.get_secret("token") == "احتياطي"


# ------------------------------------------------------------- المُفرد --

def test_default_provider_is_null_and_safe() -> None:
    """ذرة تطلب سراً في بيئة بلا مخزن تحصل على None لا استثناء."""
    provider = get_secret_provider()
    assert isinstance(provider, NullSecretProvider)
    assert provider.get_secret("anything") is None


def test_provider_can_be_installed_and_reset(vault: Path) -> None:
    set_secret_provider(_open(vault))
    assert get_secret_provider().get_secret("mt5_password") == "hunter2"
    reset_secret_provider()
    assert get_secret_provider().get_secret("mt5_password") is None


# -------------------------------------------------------- عزل النواة --

def test_core_does_not_know_security_exists() -> None:
    """المادة 41: القدرة أُضيفت بالإضافة لا بتعديل النواة."""
    core_dir = Path(__file__).resolve().parents[2] / "core"
    offenders = [
        p.name for p in core_dir.rglob("*.py")
        if "security" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"النواة تعرف بحزمة الأسرار: {offenders}"


def test_all_providers_honour_the_interface() -> None:
    for provider in (NullSecretProvider(), EnvSecretProvider(), ChainSecretProvider()):
        assert isinstance(provider, ISecretProvider)
        assert provider.get_secret("x") is None
        assert provider.available_keys() == [] or isinstance(provider.available_keys(), list)


def test_kdf_params_round_trip() -> None:
    params = KdfParams.new()
    assert KdfParams.from_dict(params.to_dict()).salt == params.salt


def test_wipe_zeroes_the_buffer() -> None:
    buffer = bytearray(b"sensitive")
    wipe(buffer)
    assert set(buffer) == {0}
