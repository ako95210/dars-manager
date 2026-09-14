#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
infra_dir=$(cd -- "${script_dir}/.." && pwd)
env_file="${DARSM_ENV_FILE:-${infra_dir}/.env}"
compose_file="${infra_dir}/compose.beta.yml"

if [[ ! -f "${env_file}" ]]; then
  echo "Fichier manquant: ${env_file}. Copiez .env.example vers .env." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

if [[ -z "${DARSM_DOMAIN:-}" || "${DARSM_DOMAIN}" == *example.org ]]; then
  echo "DARSM_DOMAIN doit contenir le domaine réel de la bêta." >&2
  exit 1
fi
if [[ ! "${DARSM_DOMAIN}" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "DARSM_DOMAIN doit être un nom d'hôte sans schéma ni chemin." >&2
  exit 1
fi

postgres_secret="${infra_dir}/${DARSM_POSTGRES_PASSWORD_FILE:-./secrets/postgres_password}"
openai_secret="${infra_dir}/${DARSM_OPENAI_API_KEY_FILE:-./secrets/openai_api_key}"
for secret in "${postgres_secret}" "${openai_secret}"; do
  if [[ ! -s "${secret}" ]]; then
    echo "Secret absent ou vide: ${secret}" >&2
    exit 1
  fi
done

docker compose --env-file "${env_file}" -f "${compose_file}" config --quiet
echo "Préflight réussi pour https://${DARSM_DOMAIN}."
