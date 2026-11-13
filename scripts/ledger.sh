#!/usr/bin/env bash
# Holt bzw. speichert das Budget-Ledger auf dem Branch "budget-ledger".
#   scripts/ledger.sh pull            -> .ledger/ledger.json (Branch wird bei Bedarf angelegt)
#   scripts/ledger.sh push "Nachricht" -> committet & pusht das Ledger (schlägt laut fehl)
set -euo pipefail

cmd="${1:?pull|push}"
dir="${LEDGER_DIR:-.ledger}"
branch="${LEDGER_BRANCH:-budget-ledger}"

case "$cmd" in
  pull)
    rm -rf "$dir"
    git worktree prune
    if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
      git fetch --depth=1 origin "$branch"
      git worktree add --detach "$dir" FETCH_HEAD
      git -C "$dir" checkout -q -B "$branch"
    else
      echo "Ledger-Branch '$branch' existiert noch nicht – wird angelegt."
      git worktree add --detach "$dir" HEAD
      git -C "$dir" checkout -q --orphan "$branch"
      git -C "$dir" rm -rfq --ignore-unmatch . >/dev/null
      printf '{\n  "version": 1,\n  "entries": []\n}\n' > "$dir/ledger.json"
    fi
    echo "Ledger: $dir/ledger.json"
    ;;
  push)
    msg="${2:-ledger update}"
    git -C "$dir" add ledger.json
    if git -C "$dir" diff --cached --quiet; then
      echo "Ledger unverändert."
      exit 0
    fi
    git -C "$dir" -c user.name="storyhouse-budget-bot" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit -q -m "$msg"
    git -C "$dir" push origin "HEAD:refs/heads/$branch"
    ;;
  *)
    echo "Unbekannter Befehl: $cmd" >&2
    exit 1
    ;;
esac
