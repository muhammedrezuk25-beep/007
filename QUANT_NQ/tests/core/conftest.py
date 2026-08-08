"""أدوات مشتركة لاختبارات Core: بناء شجرة ذرات مؤقتة على القرص."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

ATOM_TEMPLATE = '''
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus


class Atom(AtomBase):
    def __init__(self):
        self.events = []
        self.context = None

    async def initialize(self, context: AtomContext) -> None:
        self.context = context
        self.events.append("initialize")
        {init_body}

    async def start(self) -> None:
        self.events.append("start")
        {start_body}

    async def stop(self) -> None:
        self.events.append("stop")

    async def shutdown(self) -> None:
        self.events.append("shutdown")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(state=HealthState.{health})
'''


def write_atom(
    root: Path,
    atom_id: int,
    *,
    subdir: str | None = None,
    critical: bool = False,
    version: str = "1.0.0",
    core_version: str = ">=1.0.0",
    dependencies: list[dict] | None = None,
    health: dict | None = None,
    startup_mode: str = "auto",
    config: dict | None = None,
    config_schema: dict | None = None,
    fail_on: str | None = None,
    health_state: str = "HEALTHY",
) -> Path:
    """يكتب ذرة صالحة كاملة (manifest.yaml + atom.py) على القرص."""
    directory = root / (subdir or f"atom_{atom_id}")
    directory.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "id": atom_id,
        "name": f"atom-{atom_id}",
        "version": version,
        "core_version": core_version,
        "critical": critical,
        "startup_mode": startup_mode,
    }
    if dependencies:
        manifest["dependencies"] = dependencies
    if health:
        manifest["health"] = health
    if config:
        manifest["config"] = config
    if config_schema:
        manifest["config_schema"] = config_schema

    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )

    raise_line = 'raise RuntimeError("عطل مُتعمَّد في الاختبار")'
    (directory / "atom.py").write_text(
        textwrap.dedent(
            ATOM_TEMPLATE.format(
                init_body=raise_line if fail_on == "initialize" else "pass",
                start_body=raise_line if fail_on == "start" else "pass",
                health=health_state,
            )
        ),
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def atoms_root(tmp_path: Path) -> Path:
    root = tmp_path / "atoms"
    root.mkdir()
    return root
