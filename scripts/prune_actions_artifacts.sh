#!/usr/bin/env bash
# WSL/GitHub CLI cleanup for two specific Windows DEVELOPMENT artifact names.
# DRY RUN is the default. Deletion requires an explicit --apply.
set -Eeuo pipefail

repo="hatsu07/pokemon_champions_assistant"
min_age_days=1
keep_per_name=1
mode="dry-run"
include_legacy=false

usage() {
  cat <<'HELP'
Usage: bash scripts/prune_actions_artifacts.sh [options]
  --repo OWNER/REPO         Repository to clean
  --min-age-days N          Eligible artifacts must be this old (default 1)
  --keep-per-name N         Keep this many newest copies PER name (default 1)
  --include-legacy          Also remove the 3 older validation/assistant artifact names
                            (all versions; these are not the two current ZIP names)
  --apply                   Delete eligible items (without --apply: preview only)
  --help                    Print help

By default only two exact current names are eligible, keeping newest N:
  PokemonChampionsAssistant-DEVELOPMENT-windows-x64
  PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64

With --include-legacy these THREE obsolete names are ALSO eligible:
  PokemonChampionsAssistant-windows-x64
  PokemonChampionsSettingsValidation-windows-x64
  PokemonChampionsValidation-windows-x64
All copies of those legacy names older than --min-age-days are eligible;
--keep-per-name only protects the two current DEVELOPMENT names.
Other artifacts, workflow runs, source files and private data are unaffected.
GitHub Actions quota updates may lag after deletion.
HELP
}
while (($#)); do
  case "$1" in
    --repo) (($# >= 2)) || exit 2; repo="$2"; shift 2 ;;
    --min-age-days) (($# >= 2)) || exit 2; min_age_days="$2"; shift 2 ;;
    --keep-per-name) (($# >= 2)) || exit 2; keep_per_name="$2"; shift 2 ;;
    --include-legacy) include_legacy=true; shift ;;
    --apply) mode="apply"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "Invalid repo" >&2; exit 2; }
[[ "$min_age_days" =~ ^[0-9]+$ && "$keep_per_name" =~ ^[0-9]+$ ]] || {
  echo "Age and keep must be nonnegative integers" >&2; exit 2;
}
((min_age_days <= 3650 && keep_per_name >= 1 && keep_per_name <= 100)) || {
  echo "Age must be <= 3650; keep-per-name must be 1..100" >&2; exit 2;
}
command -v gh >/dev/null || { echo "Please install and authenticate gh CLI" >&2; exit 2; }
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Paginate across every artifact, retain full repository scope and fail closed.
if ! gh api --paginate "repos/$repo/actions/artifacts?per_page=100" \
  --jq '.artifacts[] | select(.expired != true) | [.name, .created_at, (.id | tostring), (.size_in_bytes | tostring)] | @tsv' \
  > "$tmp/list.tsv"; then
  echo "GitHub API listing failed; no deletions performed." >&2
  exit 1
fi
LC_ALL=C sort -t $'\t' -k2,2r "$tmp/list.tsv" > "$tmp/sorted.tsv"

assistant_seen=0
collector_seen=0
cutoff="$(( $(date -u +%s) - min_age_days * 86400 ))"
items=0
bytes=0
: > "$tmp/delete.tsv"
while IFS=$'\t' read -r name created id size; do
  case "$name" in
    PokemonChampionsAssistant-DEVELOPMENT-windows-x64)
      assistant_seen="$((assistant_seen+1))"
      ((assistant_seen > keep_per_name)) || continue ;;
    PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64)
      collector_seen="$((collector_seen+1))"
      ((collector_seen > keep_per_name)) || continue ;;
    PokemonChampionsAssistant-windows-x64|PokemonChampionsSettingsValidation-windows-x64|PokemonChampionsValidation-windows-x64)
      [[ "$include_legacy" == true ]] || continue ;;
    *) continue ;;
  esac
  [[ "$id" =~ ^[0-9]+$ && "$size" =~ ^[0-9]+$ ]] || {
    echo "Invalid artifact metadata: stopping" >&2; exit 1;
  }
  created_epoch="$(date -u -d "$created" +%s)" || {
    echo "Invalid artifact date: stopping" >&2; exit 1;
  }
  ((created_epoch <= cutoff)) || continue
  printf '%s\t%s\t%s\t%s\n' "$name" "$created" "$id" "$size" >> "$tmp/delete.tsv"
  items="$((items+1))"
  bytes="$((bytes+size))"
done < "$tmp/sorted.tsv"

printf 'Repo: %s | Mode: %s | Older than %s days | Keep latest %s current builds/name | Include legacy: %s\n' \
  "$repo" "$mode" "$min_age_days" "$keep_per_name" "$include_legacy"
printf 'Eligible: %s artifact(s), approx %s MiB\n' "$items" "$((bytes / 1048576))"
while IFS=$'\t' read -r name created id size; do
  printf '  %s  %s  created=%s  size=%s MiB\n' "$id" "$name" "$created" "$((size/1048576))"
done < "$tmp/delete.tsv"
if [[ "$mode" != "apply" ]]; then
  echo "DRY RUN. Re-run with --apply to delete the listed items."
  exit 0
fi

deleted=0
while IFS=$'\t' read -r name created id size; do
  if ! gh api --method DELETE "repos/$repo/actions/artifacts/$id" >/dev/null; then
    echo "Deletion of $id failed after $deleted successful deletion(s); stopping." >&2
    exit 1
  fi
  deleted="$((deleted+1))"
done < "$tmp/delete.tsv"
echo "Deleted $deleted old artifacts; workflow run history preserved."
