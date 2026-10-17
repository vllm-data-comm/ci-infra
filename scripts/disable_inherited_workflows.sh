#!/usr/bin/env bash
# Disable every workflow not named lwd_*. Run after each upstream sync:
# inherited workflows stay disabled (a repository setting), but workflows
# newly added upstream arrive enabled.
set -euo pipefail
repo=${1:?usage: disable_inherited_workflows.sh <owner/repo>}
gh api "repos/$repo/actions/workflows" --paginate --jq \
  '.workflows[] | select(.state == "active") | "\(.id) \(.path)"' |
while read -r id path; do
  case "${path##*/}" in
    lwd_*) continue ;;
  esac
  echo "disabling $repo $path"
  gh api -X PUT "repos/$repo/actions/workflows/$id/disable" >/dev/null
done
