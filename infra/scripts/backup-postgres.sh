#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
infra_dir=$(cd -- "${script_dir}/.." && pwd)
env_file="${DARSM_ENV_FILE:-${infra_dir}/.env}"
compose_file="${infra_dir}/compose.beta.yml"
backup_dir="${DARSM_BACKUP_DIR:-${infra_dir}/backups}"

umask 077
mkdir -p -- "${backup_dir}"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="${backup_dir}/dars-manager-${timestamp}.dump"
temporary=$(mktemp "${backup_dir}/.dars-manager-${timestamp}.XXXXXX")

cleanup() {
  [[ ! -f "${temporary}" ]] || rm -f -- "${temporary}"
}
trap cleanup EXIT

docker compose --env-file "${env_file}" -f "${compose_file}" exec -T postgres \
  sh -eu -c 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom' \
  > "${temporary}"

docker compose --env-file "${env_file}" -f "${compose_file}" exec -T postgres \
  pg_restore --list < "${temporary}" > /dev/null

chmod 600 -- "${temporary}"
mv -- "${temporary}" "${destination}"
trap - EXIT
echo "Sauvegarde validée: ${destination}"
