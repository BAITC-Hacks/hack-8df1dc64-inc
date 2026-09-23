#!/usr/bin/env bash
# Stop-hook: блокирует завершение хода Claude Code, если код менялся, но docs/progress.md
# не был обновлён, или если тест этого модуля (./test.sh) падает.
#
# Это не бюрократия ради бюрократии: по Положению хакатона отсутствие у команды
# подтверждённого промежуточного результата за любой отчётный час соревновательной части —
# самостоятельное основание для дисквалификации. Этот хук — техническая страховка, чтобы
# агент физически не мог "забыть" зафиксировать результат.

set -euo pipefail

INPUT=$(cat)

# Защита от зацикливания: если Stop-hook уже сработал в этой же цепочке, не блокируем повторно.
STOP_HOOK_ACTIVE=$(echo "$INPUT" | grep -o '"stop_hook_active"[[:space:]]*:[[:space:]]*true' || true)
if [ -n "$STOP_HOOK_ACTIVE" ]; then
  exit 0
fi

# Ничего не менялось в текущей директории (модуле) — блокировать нечего.
if git diff --quiet -- . && git diff --cached --quiet -- .; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel)
PROGRESS_FILE="docs/progress.md"

CHANGED_FILES=$( { git diff --name-only -- .; git diff --cached --name-only -- .; } | sort -u)
PROGRESS_CHANGED=$(cd "$REPO_ROOT" && { git diff --name-only -- "$PROGRESS_FILE"; git diff --cached --name-only -- "$PROGRESS_FILE"; } | sort -u)

if [ -z "$PROGRESS_CHANGED" ]; then
  echo "Код в этом модуле изменился, но docs/progress.md — нет." >&2
  echo "Перед завершением хода: заполни свою строку в docs/progress.md за текущий час —" >&2
  echo "что сделано и как это работает простыми словами. Это не формальность —" >&2
  echo "по Положению хакатона отсутствие подтверждённого результата за отчётный час" >&2
  echo "может привести к дисквалификации команды." >&2
  exit 2
fi

# Если в этом модуле есть test.sh — прогнать его. Это тест ИМЕННО ЭТОГО этапа/модуля,
# не общая проверка всей системы и не code-review.
if [ -f "./test.sh" ]; then
  if ! bash ./test.sh; then
    echo "./test.sh этого модуля падает." >&2
    echo "Это тест именно текущего этапа (не всей системы) — почини его перед тем," >&2
    echo "как завершать ход. Если функциональность реально не готова — сначала доведи" >&2
    echo "до рабочего состояния, а не отключай проверку." >&2
    exit 2
  fi
fi

exit 0
