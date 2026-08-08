"""
Core.manifest_loader
======================
الاكتشاف التلقائي بالكامل (Article 4 + Article 8: Bootloader.Scan).

يفحص شجرة الذرات بحثًا عن أي ملف manifest.yaml بغض النظر عن اسم أو عمق
المجلد، يحمّله، يتحقق منه عبر AtomManifest، ويجمع كل الأخطاء دون أن
يوقف الفحص بسبب ملف واحد تالف (Fault Tolerant — سياسة الأخطاء).

ملاحظة تصميم: الفحص مُعطى جذرًا (root) بدل أن يكون مفتوحًا على المشروع
كاملًا حرفيًا (repo/.git/venv...). Bootloader يمرر atoms/ كجذر افتراضي،
وهذا يحقق نفس المبدأ: لا اعتماد على أسماء المجلدات أو العائلات *داخل*
هذا الجذر، مهما كان عمقها أو تسميتها.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from core.contracts.manifest import AtomManifest
from core.errors import ManifestParseError

MANIFEST_FILENAME = "manifest.yaml"


@dataclass(frozen=True, slots=True)
class DiscoveredAtom:
    """ذرة تم اكتشافها والتحقق منها بنجاح، مع مكانها على القرص."""

    manifest: AtomManifest
    directory: Path


@dataclass(frozen=True, slots=True)
class DiscoveryFailure:
    """فشل تحميل/تحقق manifest واحد — لا يوقف بقية الاكتشاف."""

    path: Path
    error: str


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    atoms: list[DiscoveredAtom]
    failures: list[DiscoveryFailure]

    @property
    def ok(self) -> bool:
        return not self.failures


def scan(root: Path) -> DiscoveryReport:
    """فحص متكرر (Recursive Scan) لكامل root بحثًا عن manifest.yaml.
    لا يعرف شيئًا عن أسماء المجلدات أو العائلات — أي عمق، أي اسم."""

    atoms: list[DiscoveredAtom] = []
    failures: list[DiscoveryFailure] = []

    if not root.exists():
        return DiscoveryReport(atoms=[], failures=[])

    for manifest_path in sorted(root.rglob(MANIFEST_FILENAME)):
        try:
            raw = _load_yaml(manifest_path)
            manifest = AtomManifest.model_validate(raw)
        except ManifestParseError as exc:
            failures.append(DiscoveryFailure(path=manifest_path, error=str(exc)))
            continue
        except ValidationError as exc:
            failures.append(
                DiscoveryFailure(path=manifest_path, error=_format_validation_error(exc))
            )
            continue

        directory = manifest_path.parent
        # المانيفست وحده لا يصنع ذرة: إن غاب ملف نقطة الدخول فالذرة
        # ناقصة، لا صالحة. بدون هذا الفحص يبقى `manifest.yaml` وحيدًا
        # كافيًا لإبقاء ذرة محذوف كودها "مكتشفة"، فلا تُسحب من التشغيل
        # أبدًا وتنكشف الحقيقة فقط عند إعادة تشغيل النواة (المادة 15).
        entry_error = _missing_entrypoint(manifest, directory)
        if entry_error is not None:
            failures.append(DiscoveryFailure(path=manifest_path, error=entry_error))
            continue

        atoms.append(DiscoveredAtom(manifest=manifest, directory=directory))

    _flag_duplicate_ids(atoms, failures)
    return DiscoveryReport(atoms=atoms, failures=failures)


def entrypoint_file(manifest: AtomManifest, directory: Path) -> Path:
    """مسار ملف نقطة الدخول كما يشتقّه Core من `entrypoint`.

    مصدر الحقيقة الوحيد لهذا الاشتقاق، يستعمله المُحمِّل والمُشكِّل معًا
    حتى لا يتباعد المنطقان (المادة 42/62).
    """
    module_name, _, _ = manifest.entrypoint.partition(":")
    return directory / f"{module_name.replace('.', '/')}.py"


def _missing_entrypoint(manifest: AtomManifest, directory: Path) -> str | None:
    path = entrypoint_file(manifest, directory)
    if path.is_file():
        return None
    return (
        f"ملف نقطة الدخول غير موجود: {path.name} "
        f"(entrypoint='{manifest.entrypoint}') — الذرة ناقصة"
    )


def _load_yaml(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except OSError as exc:
        raise ManifestParseError(f"تعذّرت قراءة الملف {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestParseError(f"YAML غير صالح في {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestParseError(f"{path} يجب أن يكون خريطة (mapping) في الجذر")
    return data


def _format_validation_error(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return "؛ ".join(parts)


def _flag_duplicate_ids(
    atoms: list[DiscoveredAtom], failures: list[DiscoveryFailure]
) -> None:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for atom in atoms:
        aid = atom.manifest.id
        (duplicates if aid in seen else seen).add(aid)

    if not duplicates:
        return

    for atom in atoms:
        if atom.manifest.id in duplicates:
            failures.append(
                DiscoveryFailure(
                    path=atom.directory / MANIFEST_FILENAME,
                    error=f"Atom ID مكرر: {atom.manifest.id} (Article 6 — الهوية يجب أن تكون فريدة)",
                )
            )
    atoms[:] = [a for a in atoms if a.manifest.id not in duplicates]
