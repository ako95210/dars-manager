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
smtp_secret="${infra_dir}/${DARSM_SMTP_PASSWORD_FILE:-./secrets/smtp_password}"
runtime_uid="${DARSM_RUNTIME_UID:-10001}"
runtime_gid="${DARSM_RUNTIME_GID:-10001}"
for secret in "${postgres_secret}" "${openai_secret}"; do
  if [[ ! -s "${secret}" ]]; then
    echo "Secret absent ou vide: ${secret}" >&2
    exit 1
  fi
  secret_uid=$(stat -c '%u' "${secret}")
  secret_gid=$(stat -c '%g' "${secret}")
  secret_mode=$(stat -c '%a' "${secret}")
  if [[ "${secret_uid}" != "${runtime_uid}" || "${secret_gid}" != "${runtime_gid}" || "${secret_mode}" != "600" ]]; then
    echo "Secret mal préparé pour le runtime: ${secret}. Exécutez sudo ./scripts/prepare-runtime-secrets.sh." >&2
    exit 1
  fi
done

if [[ ! -e "${smtp_secret}" ]]; then
  echo "Emplacement du secret SMTP absent: ${smtp_secret}. Exécutez sudo ./scripts/prepare-runtime-secrets.sh." >&2
  exit 1
fi
smtp_uid=$(stat -c '%u' "${smtp_secret}")
smtp_gid=$(stat -c '%g' "${smtp_secret}")
smtp_mode=$(stat -c '%a' "${smtp_secret}")
if [[ "${smtp_uid}" != "${runtime_uid}" || "${smtp_gid}" != "${runtime_gid}" || "${smtp_mode}" != "600" ]]; then
  echo "Secret SMTP mal préparé pour le runtime: ${smtp_secret}." >&2
  exit 1
fi
if [[ -n "${DARSM_SMTP_HOST:-}" ]]; then
  if [[ -z "${DARSM_EMAIL_FROM:-}" || -z "${DARSM_SMTP_USERNAME:-}" || ! -s "${smtp_secret}" ]]; then
    echo "La configuration SMTP est incomplète (expéditeur, utilisateur ou mot de passe)." >&2
    exit 1
  fi
fi

docker compose --env-file "${env_file}" -f "${compose_file}" config --quiet
echo "Préflight réussi pour https://${DARSM_DOMAIN}."
