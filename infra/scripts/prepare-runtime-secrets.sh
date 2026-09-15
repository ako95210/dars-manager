#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
infra_dir=$(cd -- "${script_dir}/.." && pwd)
secrets_dir="${infra_dir}/secrets"
runtime_uid="${DARSM_RUNTIME_UID:-10001}"
runtime_gid="${DARSM_RUNTIME_GID:-10001}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ce script doit être exécuté avec sudo pour préparer les secrets du serveur." >&2
  exit 1
fi

secrets=(
  "${secrets_dir}/postgres_password"
  "${secrets_dir}/openai_api_key"
)

install -d -o root -g root -m 0700 -- "${secrets_dir}"
for secret in "${secrets[@]}"; do
  if [[ ! -s "${secret}" ]]; then
    echo "Secret absent ou vide: ${secret}" >&2
    exit 1
  fi
  chown -- "${runtime_uid}:${runtime_gid}" "${secret}"
  chmod 0600 -- "${secret}"
done

echo "Secrets préparés pour le runtime ${runtime_uid}:${runtime_gid} (mode 0600)."
