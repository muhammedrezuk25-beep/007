"""
Core.config
============
Article 19: يقرأ Config الذرة من Manifest فقط. لا يحتوي أي إعداد خاص
داخل Core، ولا يعتمد على أي ملف بأي اسم ثابت لإعداد ذرة بعينها — قيم
إعداد كل ذرة تعيش داخل حقل `config` في manifest.yaml نفسه، وتُتحقق
مقابل `config_schema` فور تحميل الملف (`core.contracts.manifest`)، لا
هنا. هذا الملف مسؤول فقط عن إعداد Core العام نفسه (config/core.yaml)
— إعدادات المنصة، لا علاقة لها بأي ذرة بعينها.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.errors import ConfigError


def load_core_config(path: Path) -> dict[str, Any]:
    """إعداد Core العام فقط. لا يمر عبر config_schema (ذاك خاص بالذرات
    ويُتحقق منه داخل AtomManifest نفسها)."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML غير صالح في {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} يجب أن يكون خريطة في الجذر")
    return data
