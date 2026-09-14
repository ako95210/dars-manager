#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^https:// ]]; then
  echo "Usage: $0 https://dars.example.org" >&2
  exit 2
fi

base_url=${1%/}
state_file="${DARSM_MONITOR_STATE_FILE:-/tmp/dars-manager-health.state}"
previous="unknown"
[[ ! -f "${state_file}" ]] || previous=$(<"${state_file}")

if curl --fail --silent --show-error --max-time 10 \
  "${base_url}/api/health/ready" > /dev/null; then
  current="up"
else
  current="down"
fi

printf '%s\n' "${current}" > "${state_file}"
if [[ "${current}" == "${previous}" ]]; then
  [[ "${current}" == "up" ]]
  exit
fi

message="Dars Manager ${current}: ${base_url}"
logger -t dars-manager-monitor -- "${message}" 2>/dev/null || true
echo "${message}"
if [[ -n "${DARSM_ALERT_WEBHOOK:-}" ]]; then
  curl --fail --silent --show-error --max-time 10 \
    -H "Content-Type: application/json" \
    --data "{\"text\":\"${message}\"}" \
    "${DARSM_ALERT_WEBHOOK}" > /dev/null
fi

[[ "${current}" == "up" ]]
