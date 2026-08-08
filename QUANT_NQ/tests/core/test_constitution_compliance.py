"""
اختبارات امتثال دستوري بنيوية
===============================
تفحص هذه الاختبارات كود Core نفسه (وليس سلوكه فقط) مقابل المواد التي
يمكن التحقق منها آليًا. أي خرق مستقبلي يُكسر البناء فورًا.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parents[2] / "core"
CORE_FILES = sorted(CORE_DIR.rglob("*.py"))

# مفردات تداولية محظورة داخل النواة (المادة 95). الفحص يجري على
# **الكود التنفيذي فقط** بعد تجريد التعليقات وسلاسل التوثيق: ذكر
# "Broker" في docstring يشرح ما تمنعه النواة ليس معرفة تداولية، بينما
# متغيّر أو دالة تحمل الاسم هو كذلك. كلمات عامة الاستعمال في الجدولة
# مثل order (boot_order) مستبعدة عمدًا لتفادي إنذار كاذب.
TRADING_TERMS = [
    "trade", "position", "portfolio", "broker", "candle", "ohlc",
    "ticker", "strategy", "indicator", "pnl", "margin",
    "leverage", "backtest", "slippage", "bid", "ask", "spread",
]


def _executable_source(path: Path) -> str:
    """يُرجع كود الوحدة بعد تجريد كل docstring وكل تعليق."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
                node.body[0].value, ast.Constant
            ) and isinstance(node.body[0].value.value, str):
                node.body.pop(0)
                if not node.body:
                    node.body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def test_core_contains_no_trading_knowledge() -> None:
    """المادة 95/97: النواة حاضنة تشغيل فقط، بلا أي معرفة تداولية."""
    offenders: list[str] = []
    for path in CORE_FILES:
        code = _executable_source(path)
        for term in TRADING_TERMS:
            if re.search(rf"\b{term}s?\b", code, re.IGNORECASE):
                offenders.append(f"{path.name}: {term}")
    assert not offenders, f"معرفة تداولية داخل النواة (المادة 95): {offenders}"


def test_core_hardcodes_no_atom_identity() -> None:
    """المادة 9/47: لا اسم ولا رقم ذرة بعينها مكتوب داخل Core."""
    pattern = re.compile(r"atom_id\s*==\s*\d+|manifest\.id\s*==\s*\d+|\.name\s*==\s*[\"']")
    offenders = [p.name for p in CORE_FILES if pattern.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"النواة تميّز ذرة بعينها: {offenders}"


def test_core_depends_on_no_fixed_atom_filenames() -> None:
    """المادة 4/42: لا اعتماد على اسم ملف ثابت داخل مجلد الذرة سوى
    عقد المانيفست نفسه."""
    allowed = {"manifest.yaml", "core.yaml", "dashboard.html"}
    pattern = re.compile(r"[\"']([\w\-.]+\.(?:yaml|yml|json|py|toml|html))[\"']")
    offenders: list[str] = []
    for path in CORE_FILES:
        for match in pattern.findall(path.read_text(encoding="utf-8")):
            if match not in allowed:
                offenders.append(f"{path.name}: {match}")
    assert not offenders, f"اعتماد على أسماء ملفات ثابتة: {offenders}"


def test_core_contains_no_unfinished_work_markers() -> None:
    """المادة 40/79: ممنوع شحن TODO/FIXME/HACK في كود معتمد."""
    pattern = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    offenders = [p.name for p in CORE_FILES if pattern.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"علامات عمل غير مكتمل: {offenders}"


def test_core_never_prints_directly() -> None:
    """المادة 25: كل الإخراج يمر عبر Logger."""
    offenders: list[str] = []
    for path in CORE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "print":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"استدعاء print() داخل النواة: {offenders}"


def test_core_never_kills_the_process() -> None:
    """المادة 87/88: لا sys.exit ولا os._exit ولا إيقاف حلقة asyncio
    داخل كود النواة العام."""
    banned = {"exit", "_exit", "abort", "kill"}
    offenders: list[str] = []
    for path in CORE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in banned and not isinstance(node.func.value, ast.Attribute):
                    owner = getattr(node.func.value, "id", "")
                    if owner in {"sys", "os"}:
                        offenders.append(f"{path.name}:{node.lineno} {owner}.{node.func.attr}")
    assert not offenders, f"محاولة إنهاء العملية داخل النواة: {offenders}"


def test_core_has_no_unused_imports() -> None:
    """المادة 77: ممنوع بقاء استيرادات غير مستخدمة."""
    import subprocess

    result = subprocess.run(
        ["python3", "-m", "ruff", "check", str(CORE_DIR), "--select", "F401", "--output-format", "concise"],
        capture_output=True, text=True,
    )
    assert "F401" not in result.stdout, f"استيرادات غير مستخدمة:\n{result.stdout}"


def test_every_public_core_service_has_a_protocol() -> None:
    """المادة 63: كل خدمة عامة لها واجهة مجردة رسمية."""
    from core.contracts import services
    from core.event_bus import EventBus
    from core.health_manager import HealthManager
    from core.journal import Journal
    from core.metrics import Metrics
    from core.registry import Registry

    pairs = [
        (EventBus(), services.EventBusProtocol),
        (Journal(), services.JournalProtocol),
        (Metrics(), services.MetricsProtocol),
        (Registry(), services.RegistryProtocol),
    ]
    for impl, protocol in pairs:
        assert isinstance(impl, protocol), (
            f"{type(impl).__name__} لا يطابق واجهته الرسمية {protocol.__name__}"
        )
    assert hasattr(HealthManager, "watch") and hasattr(HealthManager, "unwatch")


@pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: p.name)
def test_every_core_module_is_documented(path: Path) -> None:
    """المادة 38: توثيق داخلي إلزامي لكل وحدة."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if path.name == "__init__.py" and not tree.body:
        return
    assert ast.get_docstring(tree), f"{path.name} بلا docstring"
