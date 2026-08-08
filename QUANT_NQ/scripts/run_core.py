#!/usr/bin/env python3
"""
scripts/run_core.py
=====================
نقطة التشغيل الفعلية لـ Core V1.0: تحميل الإعداد → تهيئة الخدمات
المشتركة → Bootloader.boot() → (اختياري) REST/WebSocket/Dashboard →
انتظار إشارة إيقاف → إيقاف نظيف للذرات بترتيب عكسي عن الإقلاع.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import logger as core_logger  # noqa: E402
from core.bootloader import BootReport, Bootloader  # noqa: E402
from core.config import load_core_config  # noqa: E402
from core.contracts.atom import AtomState  # noqa: E402
from core.event_bus import EventBus  # noqa: E402
from core.health_manager import HealthManager  # noqa: E402
from core.hot_reload_service import HotReloadService  # noqa: E402
from core.journal import Journal  # noqa: E402
from core.metrics import Metrics  # noqa: E402
from core.registry import Registry  # noqa: E402
from core.snapshot_engine import SnapshotEngine  # noqa: E402
from core.dependency_resolver import resolve  # noqa: E402


async def run(demo_seconds: float | None, enable_api: bool | None) -> int:
    core_config = load_core_config(PROJECT_ROOT / "config" / "core.yaml")
    core_logger.configure(
        level=getattr(logging, str(core_config.get("log_level", "INFO")).upper(), logging.INFO),
        json_output=bool(core_config.get("log_json", True)),
    )
    log = logging.getLogger("quant_nq.core.bootstrap")

    atoms_root = PROJECT_ROOT / core_config.get("atoms_root", "atoms")
    registry = Registry()
    event_bus = EventBus()
    metrics = Metrics()
    journal_path = (core_config.get("journal") or {}).get("path")
    journal = Journal(path=(PROJECT_ROOT / journal_path) if journal_path else None)
    health_manager = HealthManager(registry, event_bus, journal, metrics)
    snapshot_engine = SnapshotEngine(registry, PROJECT_ROOT / "var" / "snapshots")

    # الأسرار تُهيّأ **قبل** الإقلاع: أول ما تفعله الذرة في
    # initialize() قد يكون طلب سرّها، فلا يجوز أن تجده فارغاً.
    _init_secret_provider(core_config, log)

    bootloader = Bootloader(
        atoms_root, registry, event_bus, journal, metrics, health_manager=health_manager
    )
    log.info("بدء الإقلاع من %s", atoms_root)
    report = await bootloader.boot()
    _log_report(log, report)

    if not report.success:
        log.critical(
            "فشلت ذرة/ذرات حرجة أثناء الإقلاع: %s — أُقلعت بقية الذرات بنجاح "
            "وCore يستمر بالعمل (المادة 21: critical لا يوقف النواة إطلاقًا).",
            report.abort_reason,
        )

    restore_report = await snapshot_engine.restore_all()
    if restore_report.restored:
        log.info("استُعيدت حالة سابقة للذرات: %s", restore_report.restored)
    if restore_report.failed:
        log.warning("فشلت استعادة حالة سابقة للذرات: %s", restore_report.failed)

    discovery_cfg = core_config.get("discovery") or {}
    rescan_interval_s = float(discovery_cfg.get("rescan_interval_s", 5.0))
    hot_reload = HotReloadService(atoms_root, registry, event_bus, health_manager, journal=journal)
    hot_reload.register()
    await hot_reload.start_periodic(interval_s=rescan_interval_s)
    log.info(
        "Runtime Discovery Engine مُفعَّلة — فحص تلقائي كل %.1f ثانية، "
        "+ POST /api/rescan لتفعيل فوري يدوي عند الحاجة",
        rescan_interval_s,
    )

    latest_report_box: dict[str, BootReport] = {"report": report}

    api_cfg = core_config.get("api") or {}
    use_api = enable_api if enable_api is not None else bool(api_cfg.get("enable_dashboard", True))
    server = None
    server_task = None
    if use_api:
        server, server_task = _start_api(
            registry, event_bus, metrics, journal, latest_report_box, api_cfg, log,
            health_manager=health_manager,
        )
        await asyncio.sleep(0.2)
        if server_task.done():
            server, server_task = None, None

    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        log.info("إشارة إيقاف مستلمة")
        stop_event.set()

    loop = asyncio.get_running_loop()
    installed_signals: list[int] = []
    try:
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)
            installed_signals.append(sig)
    except (NotImplementedError, ImportError, RuntimeError):
        pass

    if demo_seconds is not None:
        loop.call_later(demo_seconds, stop_event.set)

    await stop_event.wait()

    log.info("إيقاف نظيف لكل الذرات (بترتيب عكسي عن الإقلاع والاعتماديات حياً)...")
    # يجب أن يتوقف الاكتشاف الحي أولًا: لقطة تُلتقط بينما يُحمَّل محرك
    # الاكتشاف ذرة جديدة تعني لقطة لحالة متغيّرة تحت أقدامنا.
    await hot_reload.stop_periodic()
    snap_report = await snapshot_engine.snapshot_all()
    if snap_report.captured:
        log.info("التُقطت حالة الذرات: %s", snap_report.captured)
    if snap_report.failed:
        log.warning("فشل التقاط حالة الذرات: %s", snap_report.failed)
    
    # ⚠️ استدعاء دالة الإيقاف بنسختها الديناميكية الجديدة (دون تمرير قائمة الإقلاع القديمة)
    await _shutdown(registry, health_manager, log)

    for sig in installed_signals:
        try:
            loop.remove_signal_handler(sig)
        except (NotImplementedError, RuntimeError):
            pass

    if server is not None:
        server.should_exit = True
        await server_task

    log.info("تم إيقاف Core بنجاح")
    return 0


def _log_report(log: logging.Logger, report: BootReport) -> None:
    log.info(
        "انتهى الإقلاع: نجاح=%s مدة=%.2fث بدأت=%s فشلت=%s استُبعدت=%s",
        report.success, report.duration_s, report.booted, report.failed, report.excluded,
    )
    for f in report.scan_failures:
        log.warning("manifest مرفوض: %s — %s", f.path, f.error)


async def _serve_with_error_handling(server, host: str, port: int, log: logging.Logger) -> None:
    try:
        await server.serve()
    except SystemExit as exc:
        log.error(
            "فشل بدء خادم API/Dashboard على %s:%s (كود %s) — على الأرجح "
            "تضارب منفذ أو نقص صلاحيات. Core يستمر بدون واجهة API.",
            host, port, exc.code,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("خطأ غير متوقَّع في خادم API/Dashboard: %s — Core يستمر بدونها.", exc)


def _init_secret_provider(core_config: dict, log) -> None:  # noqa: ANN001
    """يهيّئ مزوّد الأسرار قبل إقلاع الذرات.

    خارج النواة تماماً: `core/` لا يعرف أن هذه الحزمة موجودة. حذف مجلد
    `security/` يجعل هذه الدالة تتجاوز نفسها بصمت وتعمل النواة كما هي
    (المادة 1/41).
    """
    cfg = core_config.get("secrets") or {}
    if not cfg.get("enabled", True):
        return
    try:
        from security import (
            ChainSecretProvider, EnvSecretProvider, FileSecretProvider,
            set_secret_provider,
        )
    except ImportError:
        log.info("حزمة security غير موجودة — يعمل النظام بلا مخزن أسرار")
        return

    vault_path = Path(cfg.get("vault_path", "runtime/secrets.enc"))
    provider = ChainSecretProvider(
        FileSecretProvider(
            vault_path,
            dpapi_blob=cfg.get("dpapi_blob"),
            allow_prompt=bool(cfg.get("allow_prompt", True)),
        ),
        EnvSecretProvider(prefix=cfg.get("env_prefix", "QUANT_SECRET_")),
    )
    set_secret_provider(provider)
    log.info("مزوّد الأسرار: %s", provider.health())


def _start_api(registry, event_bus, metrics, journal, latest_report_box, api_cfg, log, health_manager=None):  # noqa: ANN001, ANN201
    import uvicorn
    from core.api.app import create_app

    host, port = api_cfg.get("host", "127.0.0.1"), int(api_cfg.get("port", 8000))
    api_key = api_cfg.get("api_key")
    if api_key is None and host != "127.0.0.1":
        log.warning(
            "⚠️ api_key غير مُعدَّة و host=%s (ليس 127.0.0.1) — المادة 28 (Secure by Default) "
            "توصي بشدة بتعيين api_key بـcore.yaml لأي ربط متاح خارج الجهاز المحلي.", host,
        )
    app = create_app(
        registry, event_bus, metrics, journal,
        get_boot_report=lambda: latest_report_box["report"],
        api_key=api_key,
        health_manager=health_manager,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("محاولة بدء Dashboard على http://%s:%s", host, port)
    task = asyncio.create_task(_serve_with_error_handling(server, host, port, log))
    return server, task


async def _shutdown(registry: Registry, health_manager: HealthManager, log: logging.Logger) -> None:
    """⚠️ تطبيق المادة 11 و 16: الإيقاف المبني على السجل الديناميكي (يشمل الذرات المحملة حياً) بالترتيب الهرمي"""
    await health_manager.stop()

    # سحب جميع الذرات المعترف بها حالياً وحل اعتمادياتها لتحديد ترتيب التوقف الدقيق
    active_manifests = [r.manifest for r in registry.all()]
    try:
        graph = resolve(active_manifests)
        shutdown_order = list(reversed(graph.boot_order))
    except Exception as exc:
        log.warning("تعذّر حل الاعتماديات لتحديد ترتيب الإيقاف الدقيق (%s)، سيتم الإيقاف كإجراء طوارئ.", exc)
        shutdown_order = [r.id for r in registry.all()]

    for atom_id in shutdown_order:
        record = registry.find(atom_id)
        if record is None or record.state != AtomState.RUNNING:
            continue
        try:
            await record.instance.stop()
            registry.set_state(atom_id, AtomState.STOPPED)
        except Exception as exc:  # noqa: BLE001
            log.error("فشل إيقاف الذرة %s بأمان: %s", atom_id, exc)

    # مرحلة التنظيف النهائي (تتبع نفس مسار الترتيب العكسي للاعتماديات)
    for atom_id in shutdown_order:
        record = registry.find(atom_id)
        if record is None:
            continue
        try:
            await record.instance.shutdown()
        except Exception as exc:  # noqa: BLE001
            log.error("فشل shutdown نهائي للذرة %s: %s", record.id, exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="تشغيل QUANT_NQ Core V1.0")
    parser.add_argument("--demo-seconds", type=float, default=None, help="إيقاف تلقائي بعد N ثانية (اختبار/عرض)")
    parser.add_argument("--no-api", action="store_true", help="تعطيل REST/WebSocket/Dashboard")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.demo_seconds, enable_api=(False if args.no_api else None)))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())