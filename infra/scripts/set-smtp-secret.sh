#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ce script doit être exécuté avec sudo." >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
infra_dir=$(cd -- "${script_dir}/.." && pwd)
secrets_dir="${infra_dir}/secrets"
destination="${secrets_dir}/smtp_password"
runtime_uid="${DARSM_RUNTIME_UID:-10001}"
runtime_gid="${DARSM_RUNTIME_GID:-10001}"

read -r -s -p "Mot de passe ou clé SMTP: " smtp_password
echo
read -r -s -p "Confirmation: " confirmation
echo
if [[ -z "${smtp_password}" || "${smtp_password}" != "${confirmation}" ]]; then
  echo "Le secret est vide ou les valeurs ne correspondent pas." >&2
  exit 1
fi

install -d -o root -g root -m 0700 -- "${secrets_dir}"
temporary=$(mktemp "${secrets_dir}/.smtp-password.XXXXXX")
cleanup() {
  [[ ! -e "${temporary}" ]] || rm -f -- "${temporary}"
}
trap cleanup EXIT
printf '%s\n' "${smtp_password}" > "${temporary}"
chown "${runtime_uid}:${runtime_gid}" "${temporary}"
chmod 0600 "${temporary}"
mv -- "${temporary}" "${destination}"
trap - EXIT
echo "Secret SMTP enregistré sans l’ajouter à l’historique du shell."
