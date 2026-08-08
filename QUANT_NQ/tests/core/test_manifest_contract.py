"""
المادة 17 / 18 / 61 / 68 — المانيفست كعقد وحيد وحاكم
=====================================================
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.contracts.manifest import AtomManifest, StartupMode

BASE = {"id": 1, "name": "a", "version": "1.0.0", "core_version": ">=1.0.0"}


def test_minimal_manifest_is_valid_and_frozen() -> None:
    m = AtomManifest.model_validate(BASE)
    assert m.critical is False
    assert m.startup_mode is StartupMode.AUTO
    with pytest.raises(ValidationError):
        m.id = 2  # المانيفست عقد ثابت بعد التحقق


def test_unknown_field_is_rejected() -> None:
    """المادة 7: Core لا يتخذ أي قرار اعتمادًا على حقول خارج العقد —
    فالحقل المجهول خطأ كتابي يُكشف فورًا، لا يُتجاهل بصمت."""
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "family": "risk"})


def test_metadata_accepts_free_organisational_keys() -> None:
    """المادة 22: التصنيف الحر يعيش في metadata وتتجاهله النواة."""
    m = AtomManifest.model_validate({**BASE, "metadata": {"family": "risk", "team": "x"}})
    assert m.metadata["family"] == "risk"


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_blank_name_is_rejected(bad) -> None:  # noqa: ANN001
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "name": bad})


def test_invalid_own_version_is_rejected() -> None:
    """المادة 19/74: الإصدار يخضع لترقيم قابل للمقارنة."""
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "version": "نسخة أولى"})


def test_invalid_core_version_constraint_is_rejected_at_load_time() -> None:
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "core_version": ">>=1.0"})


def test_duplicate_dependency_on_same_atom_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({
            **BASE, "dependencies": [{"id": 5}, {"id": 5, "version": ">=2.0"}],
        })


def test_config_is_validated_against_its_own_schema() -> None:
    """المادة 26: الإعداد يعيش في المانيفست ويُتحقق منه فور التحميل."""
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    AtomManifest.model_validate({**BASE, "config_schema": schema, "config": {"n": 5}})

    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "config_schema": schema, "config": {"n": "خمسة"}})

    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "config_schema": schema, "config": {}})


def test_structurally_broken_schema_is_reported_clearly() -> None:
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({
            **BASE, "config_schema": {"type": "not_a_real_type"}, "config": {},
        })


def test_health_timeout_must_be_below_interval() -> None:
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({
            **BASE, "health": {"interval_ms": 1000, "timeout_ms": 1000},
        })


def test_atom_id_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        AtomManifest.model_validate({**BASE, "id": 0})
