#!/usr/bin/env python3
"""
scripts/secrets_admin.py — إدارة مخزن الأسرار المشفّر
======================================================
لا يطبع أي سر على الشاشة، ولا يكتب أي قيمة بنص صريح على القرص.

    python3 scripts/secrets_admin.py init          # إنشاء مخزن جديد
    python3 scripts/secrets_admin.py set KEY       # إضافة/تعديل سر
    python3 scripts/secrets_admin.py list          # أسماء المفاتيح فقط
    python3 scripts/secrets_admin.py remove KEY
    python3 scripts/secrets_admin.py rotate        # تغيير عبارة المرور
    python3 scripts/secrets_admin.py check         # فحص بلا كشف

الملف الناتج `runtime/secrets.enc` هو الأثر **الوحيد** على القرص، وهو
مشفّر بالكامل. عبارة المرور لا تُخزَّن في أي مكان.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet, InvalidToken

from security.keys import KdfParams, derive_key, wipe
from security.providers import VAULT_FORMAT, VAULT_VERSION

DEFAULT_VAULT = Path("runtime/secrets.enc")


def _prompt_passphrase(confirm: bool = False) -> bytearray:
    first = getpass.getpass("عبارة مرور المخزن: ")
    if confirm:
        second = getpass.getpass("أعد إدخالها للتأكيد: ")
        if first != second:
            print("العبارتان غير متطابقتين.", file=sys.stderr)
            raise SystemExit(2)
        if len(first) < 12:
            print("تحذير: عبارة أقصر من 12 محرفاً ضعيفة أمام التخمين دون اتصال.",
                  file=sys.stderr)
    return bytearray(first.encode("utf-8"))


def _write_atomic(path: Path, data: str) -> None:
    """كتابة ذرّية + صلاحيات مالك فقط (0600) قبل ظهور الملف في مكانه."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".vault.", suffix=".tmp")
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load(path: Path, passphrase: bytearray) -> tuple[dict, KdfParams]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    params = KdfParams.from_dict(envelope["kdf"])
    key = derive_key(passphrase, params)
    try:
        plain = Fernet(bytes(key)).decrypt(base64.b64decode(envelope["payload"]))
    except InvalidToken:
        print("عبارة مرور غير صحيحة أو ملف تالف.", file=sys.stderr)
        raise SystemExit(3) from None
    finally:
        wipe(key)
    return json.loads(plain.decode("utf-8")), params


def _save(path: Path, data: dict, passphrase: bytearray, params: KdfParams) -> None:
    key = derive_key(passphrase, params)
    try:
        token = Fernet(bytes(key)).encrypt(
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        )
    finally:
        wipe(key)
    envelope = {
        "format": VAULT_FORMAT, "version": VAULT_VERSION, "cipher": "fernet",
        "kdf": params.to_dict(), "payload": base64.b64encode(token).decode(),
    }
    _write_atomic(path, json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="إدارة مخزن أسرار QUANT_NQ")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("list")
    sub.add_parser("check")
    sub.add_parser("rotate")
    for name in ("set", "remove"):
        cmd = sub.add_parser(name)
        cmd.add_argument("key")

    args = parser.parse_args()
    vault: Path = args.vault

    if args.cmd == "init":
        if vault.exists():
            print(f"المخزن موجود: {vault}", file=sys.stderr)
            return 1
        passphrase = _prompt_passphrase(confirm=True)
        try:
            params = KdfParams.new()
            _save(vault, {}, passphrase, params)
        finally:
            wipe(passphrase)
        print(f"أُنشئ مخزن فارغ: {vault}  (اشتقاق: {params.algorithm})")
        print("لا تُخزَّن عبارة المرور في أي مكان — احفظها بنفسك.")
        return 0

    if not vault.exists():
        print(f"لا مخزن في {vault}. ابدأ بـ: secrets_admin.py init", file=sys.stderr)
        return 1

    if args.cmd == "check":
        envelope = json.loads(vault.read_text(encoding="utf-8"))
        mode = oct(vault.stat().st_mode & 0o777)
        print(f"الصيغة    : {envelope.get('format')} v{envelope.get('version')}")
        print(f"الاشتقاق  : {envelope.get('kdf', {}).get('algorithm')}")
        print(f"الصلاحيات : {mode}" + ("" if mode == "0o600" else "  ⚠️ يُفضَّل 0600"))
        print(f"الحجم     : {vault.stat().st_size} بايت")
        return 0

    passphrase = _prompt_passphrase()
    try:
        data, params = _load(vault, passphrase)

        if args.cmd == "list":
            if not data:
                print("(المخزن فارغ)")
            for k in sorted(data):
                print(f"  {k}")
            return 0

        if args.cmd == "set":
            value = getpass.getpass(f"قيمة '{args.key}' (مخفية): ")
            data[args.key] = value
            _save(vault, data, passphrase, params)
            print(f"حُفظ '{args.key}'.")
            return 0

        if args.cmd == "remove":
            if data.pop(args.key, None) is None:
                print(f"لا مفتاح باسم '{args.key}'.", file=sys.stderr)
                return 1
            _save(vault, data, passphrase, params)
            print(f"حُذف '{args.key}'.")
            return 0

        if args.cmd == "rotate":
            print("أدخل عبارة المرور الجديدة:")
            new_passphrase = _prompt_passphrase(confirm=True)
            try:
                _save(vault, data, new_passphrase, KdfParams.new())
            finally:
                wipe(new_passphrase)
            print("غُيّرت عبارة المرور و salt الاشتقاق.")
            return 0
    finally:
        wipe(passphrase)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
