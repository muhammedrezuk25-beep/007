#!/usr/bin/env bash
# فحص محلي سريع قبل أي دفع (المادة 1/40/49).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "1/3  التحقق من ختم تجميد النواة…"
python3 scripts/freeze_core.py verify

echo "2/3  فحص الجودة الثابت…"
python3 -m ruff check core/ scripts/ --select F,E9

echo "3/3  اختبارات Core الكاملة…"
PYTHONPATH=. python3 -m pytest tests/ -q

echo
echo "النواة سليمة، مختومة، ومطابقة للدستور."
