"""
اختبار طرف-إلى-طرف: تشغيل النواة كعملية حقيقية
================================================
لا محاكاة: يُشغَّل `scripts/run_core.py` كعملية منفصلة على شجرة ذرات
حقيقية، ويُستجوَب REST API فعليًا أثناء عملها.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.core.conftest import write_atom

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORT = 8931


def _get(path: str, timeout: float = 2.0):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture
def live_project(tmp_path: Path) -> Path:
    """نسخة كاملة من المشروع بشجرة ذرات خاصة بهذا الاختبار."""
    project = tmp_path / "project"
    project.mkdir()
    for name in ("core", "scripts"):
        subprocess.run(["cp", "-r", str(PROJECT_ROOT / name), str(project / name)], check=True)

    atoms = project / "atoms"
    atoms.mkdir()
    write_atom(atoms, 1, subdir="family_alpha/first")
    write_atom(atoms, 2, subdir="family_beta/deep/second", dependencies=[{"id": 1}])
    write_atom(atoms, 3, critical=True, fail_on="start")  # ذرة حرجة تفشل عمدًا

    config = project / "config"
    config.mkdir()
    (config / "core.yaml").write_text(
        f"log_level: WARNING\nlog_json: true\natoms_root: atoms\n"
        f"api:\n  host: 127.0.0.1\n  port: {PORT}\n",
        encoding="utf-8",
    )
    return project


@pytest.mark.timeout(60)
def test_full_lifecycle_as_a_real_process(live_project: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(live_project)}
    process = subprocess.Popen(
        [sys.executable, "scripts/run_core.py", "--demo-seconds", "12"],
        cwd=live_project, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        payload = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                payload = _get("/api/health")
                break
            except (urllib.error.URLError, OSError, TimeoutError):
                continue

        assert payload is not None, "لم يستجب API — لم تُقلع النواة"
        assert payload["status"] == "ok"

        atoms = {a["id"]: a for a in _get("/api/atoms")}

        # المادة 21: الذرة الحرجة الفاشلة لم توقف النواة ولا الذرات السليمة
        assert atoms[1]["state"] == "running"
        assert atoms[2]["state"] == "running"
        assert atoms[3]["state"] == "failed"

        report = _get("/api/boot-report")
        assert sorted(report["booted"]) == [1, 2]
        assert report["failed"] == [3]
        assert report["success"] is False

        # المادة 81: الـ API والـ Dashboard يعملان رغم فشل ذرة حرجة
        assert _get("/api/metrics")["counters"]
        assert isinstance(_get("/api/journal?n=5"), list)
        assert _get("/api/atoms/1")["name"] == "atom-1"

        outcome = process.wait(timeout=30)
        assert outcome == 0, "لم تُنهِ النواة نفسها بنظافة"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.timeout(60)
def test_hot_plug_and_unplug_without_restart(live_project: Path) -> None:
    """المادة 14/46: إضافة وسحب ذرة حياً دون إعادة تشغيل النواة."""
    env = {**os.environ, "PYTHONPATH": str(live_project)}
    process = subprocess.Popen(
        [sys.executable, "scripts/run_core.py", "--demo-seconds", "25"],
        cwd=live_project, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                _get("/api/health")
                break
            except (urllib.error.URLError, OSError, TimeoutError):
                continue

        pid_before = process.pid
        new_dir = write_atom(live_project / "atoms", 42, subdir="family_gamma/hot")

        for _ in range(24):
            time.sleep(1)
            if any(a["id"] == 42 for a in _get("/api/atoms")):
                break
        else:
            pytest.fail("لم تُكتشف الذرة المضافة حياً")

        assert process.pid == pid_before, "أُعيد تشغيل النواة — خرق المادة 46"

        import shutil

        shutil.rmtree(new_dir)
        for _ in range(24):
            time.sleep(1)
            if not any(a["id"] == 42 for a in _get("/api/atoms")):
                break
        else:
            pytest.fail("لم تُزل الذرة المسحوبة من Registry (المادة 15)")

        assert process.pid == pid_before
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
