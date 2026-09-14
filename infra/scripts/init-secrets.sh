#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
infra_dir=$(cd -- "${script_dir}/.." && pwd)
secrets_dir="${infra_dir}/secrets"

umask 077
mkdir -p -- "${secrets_dir}"
chmod 700 -- "${secrets_dir}"

postgres_secret="${secrets_dir}/postgres_password"
openai_secret="${secrets_dir}/openai_api_key"

if [[ ! -s "${postgres_secret}" ]]; then
  openssl rand -base64 48 | tr -d '\n' > "${postgres_secret}"
  printf '\n' >> "${postgres_secret}"
  echo "Mot de passe PostgreSQL généré."
else
  echo "Le secret PostgreSQL existe déjà, aucune modification."
fi

if [[ ! -s "${openai_secret}" ]]; then
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    printf '%s\n' "${OPENAI_API_KEY}" > "${openai_secret}"
  else
    read -r -s -p "OPENAI_API_KEY: " openai_key
    echo
    if [[ -z "${openai_key}" ]]; then
      echo "La clé OpenAI ne peut pas être vide." >&2
      exit 1
    fi
    printf '%s\n' "${openai_key}" > "${openai_secret}"
  fi
  echo "Secret OpenAI enregistré."
else
  echo "Le secret OpenAI existe déjà, aucune modification."
fi

chmod 600 -- "${postgres_secret}" "${openai_secret}"
echo "Secrets prêts dans ${secrets_dir}."
