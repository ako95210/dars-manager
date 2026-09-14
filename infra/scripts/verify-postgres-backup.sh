#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "Usage: $0 chemin/vers/sauvegarde.dump" >&2
  exit 2
fi

dump_file=$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
infra_dir=$(cd -- "${script_dir}/.." && pwd)
env_file="${DARSM_ENV_FILE:-${infra_dir}/.env}"
compose_file="${infra_dir}/compose.beta.yml"
verification_db="dars_restore_verify_$(date -u +%Y%m%d%H%M%S)_${RANDOM}"

if [[ ! "${verification_db}" =~ ^[a-z0-9_]+$ ]]; then
  echo "Nom de base temporaire invalide." >&2
  exit 1
fi

compose=(docker compose --env-file "${env_file}" -f "${compose_file}")
cleanup() {
  "${compose[@]}" exec -T postgres sh -eu -c \
    'dropdb --username "$POSTGRES_USER" --if-exists "$1"' sh "${verification_db}" \
    > /dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" exec -T postgres sh -eu -c \
  'createdb --username "$POSTGRES_USER" "$1"' sh "${verification_db}"
"${compose[@]}" exec -T postgres sh -eu -c \
  'pg_restore --username "$POSTGRES_USER" --dbname "$1" --no-owner --no-privileges' \
  sh "${verification_db}" < "${dump_file}"

revision=$("${compose[@]}" exec -T postgres sh -eu -c \
  'psql --username "$POSTGRES_USER" --dbname "$1" --tuples-only --no-align --command "SELECT version_num FROM alembic_version"' \
  sh "${verification_db}")
user_table=$("${compose[@]}" exec -T postgres sh -eu -c \
  'psql --username "$POSTGRES_USER" --dbname "$1" --tuples-only --no-align --command "SELECT to_regclass('\''public.users'\'')"' \
  sh "${verification_db}")

if [[ -z "${revision}" || "${user_table}" != "users" ]]; then
  echo "La restauration de contrôle est incomplète." >&2
  exit 1
fi

echo "Restauration vérifiée dans une base éphémère (révision ${revision})."
