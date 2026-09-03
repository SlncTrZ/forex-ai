#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${DEEPSEEK_KEY_FILE:-$HOME/.config/forex-ai/deepseek_api_key}"
mkdir -p "$(dirname "$KEY_FILE")"
printf 'DeepSeek API key: '
IFS= read -r -s KEY
printf '\n'
if [ -z "$KEY" ]; then
  echo 'Empty key; nothing changed.' >&2
  exit 2
fi
umask 077
printf '%s\n' "$KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
unset KEY
echo "Saved DeepSeek API key to $KEY_FILE with mode 600."
