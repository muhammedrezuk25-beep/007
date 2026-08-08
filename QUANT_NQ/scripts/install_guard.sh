#!/usr/bin/env bash
# يركّب حارس التجميد محليًا في git — لا خادم، لا حساب، لا إنترنت.
# بعد التركيب: أي commit يلمس core/ يُرفض تلقائيًا على جهازك.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  echo "لا يوجد مستودع git هنا. نفّذ: git init"
  exit 1
fi

mkdir -p .git/hooks
cat > .git/hooks/pre-commit << 'HOOK'
#!/usr/bin/env bash
# حارس تجميد النواة (المادة 1/41/100) — يعمل محليًا فقط.
set -euo pipefail

TOUCHED=$(git diff --cached --name-only | grep '^core/' | grep -v '^core/CORE.lock$' || true)

if [ -n "$TOUCHED" ]; then
  echo
  echo "  ✋ توقف: هذا الـ commit يعدّل النواة المجمّدة."
  echo
  echo "$TOUCHED" | sed 's/^/     /'
  echo
  echo "  المادة 41: الحاجة لفتح ملف داخل core/ بسبب ذرة = فشل معماري."
  echo "  أعد تصميم الذرة بدل تعديل النواة."
  echo
  echo "  إن كان هذا إصدارًا معماريًا جديدًا قررته أنت عمدًا:"
  echo "     1. ارفع CORE_VERSION في core/__version__.py"
  echo "     2. python3 scripts/freeze_core.py freeze --reseal"
  echo "     3. git add core/CORE.lock"
  echo "     4. git commit --no-verify"
  echo
  exit 1
fi

python3 scripts/freeze_core.py verify --quiet || {
  echo "  ✋ ختم النواة لا يطابق محتواها. راجع: python3 scripts/freeze_core.py verify"
  exit 1
}
HOOK

chmod +x .git/hooks/pre-commit
echo "تم تركيب الحارس في .git/hooks/pre-commit"
echo "لتجاوزه مرة واحدة عن قصد: git commit --no-verify"
