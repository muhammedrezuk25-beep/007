"""
Core.logger
============
Article 14 (+ 20 في الدستور الأول): أي Logger يحمل Atom ID تلقائيًا،
ولا يُكتب يدويًا لكل ذرة.
"""

from __future__ import annotations

import json
import logging
import sys
import contextvars
from typing import Any

_CORE_LOGGER_NAME = "quant_nq.core"
_configured = False

# ⚠️ حاوية سياقية آمنة للتزامن (Async-Safe) تمرر رقم التتبع من ناقل الأحداث إلى السجل صمتاً
current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


class _AtomLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("atom_id", self.extra["atom_id"])
        return msg, kwargs


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "atom_id": getattr(record, "atom_id", None),
        }
        
        # ⚠️ تطبيق المادة 30: قراءة وحقن المعرف تلقائياً (إن وُجد بالسياق)
        trace_id = current_trace_id.get()
        if trace_id:
            payload["trace_id"] = trace_id
            
        payload["message"] = record.getMessage()

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure(level: int = logging.INFO, json_output: bool = True) -> None:
    global _configured
    root = logging.getLogger(_CORE_LOGGER_NAME)
    root.setLevel(level)
    if _configured:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    if json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | atom=%(atom_id)s | %(name)s | %(message)s")
        )
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(atom_id: int, *, component: str | None = None) -> logging.LoggerAdapter:
    if not _configured:
        configure()
    name = (
        f"{_CORE_LOGGER_NAME}.atom.{atom_id}"
        if component is None
        else f"{_CORE_LOGGER_NAME}.atom.{atom_id}.{component}"
    )
    base = logging.getLogger(name)
    return _AtomLoggerAdapter(base, {"atom_id": atom_id})