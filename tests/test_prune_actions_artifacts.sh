#!/usr/bin/env bash
# Offline regression tests for WSL artifact housekeeping; no GitHub changes.
set -Eeuo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
script="$root/scripts/prune_actions_artifacts.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
export MOCK_GH_LIST="$tmp/list.tsv" MOCK_GH_LOG="$tmp/deletions.log"
cat > "$tmp/bin/gh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == api ]] || exit 90
if [[ "$2" == "--paginate" ]]; then
  [[ -z "$MOCK_GH_FAIL" ]] || exit 91
  cat "$MOCK_GH_LIST"
elif [[ "$2" == "--method" && "$3" == DELETE ]]; then
  printf '%s\n' "$4" >> "$MOCK_GH_LOG"
else
  exit 92
fi
FAKE
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"
export MOCK_GH_FAIL=""

recent="$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ')"
old="$(date -u -d '5 days ago' '+%Y-%m-%dT%H:%M:%SZ')"
nameA=PokemonChampionsAssistant-DEVELOPMENT-windows-x64
nameB=PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64
{
  printf '%s\t%s\t%s\t%s\n' "$nameA" "$recent" 101 90000
  printf '%s\t%s\t%s\t%s\n' "$nameB" "$recent" 201 80000
  printf '%s\t%s\t%s\t%s\n' "$nameA" "$old" 102 70000
  printf '%s\t%s\t%s\t%s\n' "$nameB" "$old" 202 60000
  printf '%s\t%s\t%s\t%s\n' Other-Private-Artifact "$old" 999 55555
} > "$MOCK_GH_LIST"

# Dry run must print two candidates but make no destructive API calls.
bash "$script" --min-age-days 1 > "$tmp/dry.log"
grep -q 'Eligible: 2 artifact' "$tmp/dry.log"
grep -q 'DRY RUN' "$tmp/dry.log"
test ! -e "$MOCK_GH_LOG"

# Explicit apply only deletes the two old allowed artifact IDs.
bash "$script" --min-age-days 1 --apply > "$tmp/apply.log"
test "$(wc -l < "$MOCK_GH_LOG")" -eq 2
grep -q '/102$' "$MOCK_GH_LOG"
grep -q '/202$' "$MOCK_GH_LOG"
! grep -q '/101$' "$MOCK_GH_LOG"
! grep -q '/201$' "$MOCK_GH_LOG"
! grep -q '/999$' "$MOCK_GH_LOG"

# Even --apply must not delete anything if the authenticated list fails.
: > "$MOCK_GH_LOG"
export MOCK_GH_FAIL="1"
if bash "$script" --apply > "$tmp/fail.log" 2>&1; then
  echo 'Expected API failure' >&2
  exit 1
fi
test ! -s "$MOCK_GH_LOG"

echo 'WSL GitHub artifact cleanup tests passed'
