#!/usr/bin/env bash
set -euo pipefail

readonly HOME_DIR="${HOME:?HOME must be set for a user-level install}"
readonly DEPLOY_ROOT="$HOME_DIR/xl_deploy"
readonly RELEASES="$DEPLOY_ROOT/releases"
readonly CURRENT="$DEPLOY_ROOT/current"
readonly SECRET_DIR="$HOME_DIR/.config/xl_deploy"
readonly CONFIG_FILE="$DEPLOY_ROOT/config.toml"
readonly SECRETS_FILE="$SECRET_DIR/secrets.env"
readonly ROUTER_TOKEN_FILE="$SECRET_DIR/router.token"
readonly UNIT_DIR="$HOME_DIR/.config/systemd/user"
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly TEMPLATE_DIR="$REPO_ROOT/xl_deploy/deploy"

if [[ "$HOME_DIR" != "/home/admin" ]]; then
  printf 'This deployment layout is pinned to /home/admin; refusing HOME=%s\n' "$HOME_DIR" >&2
  exit 1
fi

usage() {
  cat <<'EOF'
Usage: bootstrap.sh [--dry-run] [--activate]

Default: read-only inventory/dry-run. --activate installs user units and a
sample deployment config only when destination files do not already exist.
It never migrates or adopts legacy service directories/config/data, creates
current release pointers, stops services, enables units, or starts the timer.
EOF
}

activate=false
if [[ "$#" -gt 1 ]]; then
  usage >&2
  exit 2
fi
case "${1:---dry-run}" in
  --dry-run) activate=false ;;
  --activate) activate=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

inventory() {
  printf 'Mode: %s\n' "$([[ "$activate" == true ]] && printf 'explicit activation' || printf 'read-only dry-run/inventory')"
  printf 'Deployment root: %s\n' "$DEPLOY_ROOT"
  printf 'Immutable releases: %s\n' "$RELEASES"
  for branch in debug test main; do
    pointer="$CURRENT/$branch"
    if [[ -L "$pointer" ]]; then
      printf 'Current %s: symlink -> %s\n' "$branch" "$(readlink -- "$pointer")"
    elif [[ -e "$pointer" ]]; then
      printf 'Current %s: EXISTS but is not a symlink (will not replace)\n' "$branch"
    else
      printf 'Current %s: missing (requires explicit release bootstrap)\n' "$branch"
    fi
  done
  for path in \
    "$HOME_DIR/xl_qqbot" "$HOME_DIR/xl-qqbot-debug" "$HOME_DIR/xl-qqbot-test" \
    "$HOME_DIR/xl_qqbot-test" \
    "$HOME_DIR/xl_updata_server" "$CONFIG_FILE" \
    "$SECRETS_FILE" "$ROUTER_TOKEN_FILE" "$UNIT_DIR"; do
    if [[ -e "$path" ]]; then printf 'Existing operator path (preserved): %s\n' "$path"
    else printf 'Not present: %s\n' "$path"; fi
  done
}

inventory
if [[ "$activate" != true ]]; then
  printf '\nNo files or services changed. Review this inventory before activation.\n'
  exit 0
fi

for directory in "$DEPLOY_ROOT" "$RELEASES" "$CURRENT" "$SECRET_DIR" "$UNIT_DIR"; do
  if [[ -L "$directory" ]]; then
    printf 'Refusing: managed path is a symlink: %s\n' "$directory" >&2
    exit 1
  fi
  if [[ -e "$directory" && ! -d "$directory" ]]; then
    printf 'Refusing: expected a directory but found another object: %s\n' "$directory" >&2
    exit 1
  fi
done

# Cutover is fail-closed: never turn legacy checkouts into releases or overwrite
# any pre-existing unit/config. A separate, reviewed release bootstrap is needed.
for branch in debug test main; do
  pointer="$CURRENT/$branch"
  if [[ -e "$pointer" || -L "$pointer" ]]; then
    if [[ ! -L "$pointer" ]]; then
      printf 'Refusing activation: existing current path is not a symlink: %s\n' "$pointer" >&2
      exit 1
    fi
    target="$(readlink -f -- "$pointer")"
    case "$target" in
      "$RELEASES"/*) ;;
      *) printf 'Refusing activation: %s points outside immutable releases.\n' "$pointer" >&2; exit 1 ;;
    esac
    [[ -d "$target" ]] || { printf 'Refusing activation: missing release target %s\n' "$target" >&2; exit 1; }
  else
    printf 'No baseline current pointer for %s; leaving it absent until explicit release bootstrap.\n' "$branch"
  fi
done

require_private_config() {
  path="$1"
  if [[ ! -f "$path" ]]; then
    printf 'Refusing activation: map/provision the existing service config explicitly: %s\n' "$path" >&2
    exit 1
  fi
  config_mode="$(stat -c '%a' -- "$path")"
  if [[ "$config_mode" != 600 ]]; then
    printf 'Refusing activation: credential-bearing config %s has mode %s; set it to 600 first.\n' "$path" "$config_mode" >&2
    exit 1
  fi
}

for unit in xl-qqbot-router.service xl-qqbot-prod.service xl-qqbot-debug.service xl-qqbot-test.service; do
  working_directory="$(sed -n 's/^WorkingDirectory=//p' "$TEMPLATE_DIR/$unit")"
  if [[ -z "$working_directory" ]]; then
    printf 'Refusing activation: no explicit WorkingDirectory mapping in %s\n' "$unit" >&2
    exit 1
  fi
  require_private_config "$working_directory/config.toml"
done
backend_config="$(sed -n 's/^ExecStart=.* --config //p' "$TEMPLATE_DIR/xl-updata-server.service")"
if [[ -z "$backend_config" ]]; then
  printf 'Refusing activation: backend unit has no explicit --config mapping.\n' >&2
  exit 1
fi
require_private_config "$backend_config"
if [[ ! -f "$SECRETS_FILE" || -L "$SECRETS_FILE" ]]; then
  printf 'Refusing activation: create %s with required secrets, then chmod 600 it.\n' "$SECRETS_FILE" >&2
  exit 1
fi
secret_mode="$(stat -c '%a' -- "$SECRETS_FILE")"
if [[ "$secret_mode" != 600 ]]; then
  printf 'Refusing activation: %s mode is %s; set it to 600 first.\n' "$SECRETS_FILE" "$secret_mode" >&2
  exit 1
fi
if [[ ! -f "$ROUTER_TOKEN_FILE" || -L "$ROUTER_TOKEN_FILE" ]]; then
  printf 'Refusing activation: create %s with the router bearer token, then chmod 600 it.\n' "$ROUTER_TOKEN_FILE" >&2
  exit 1
fi
router_token_mode="$(stat -c '%a' -- "$ROUTER_TOKEN_FILE")"
if [[ "$router_token_mode" != 600 ]]; then
  printf 'Refusing activation: router token file mode is %s; set it to 600 first.\n' "$router_token_mode" >&2
  exit 1
fi

for name in xl-deploy-poll.service xl-deploy-poll.timer xl-qqbot-router.service \
            xl-qqbot-debug.service xl-qqbot-test.service xl-qqbot-prod.service xl-updata-server.service; do
  if [[ -e "$UNIT_DIR/$name" || -L "$UNIT_DIR/$name" ]]; then
    printf 'Refusing to overwrite existing user unit: %s\n' "$UNIT_DIR/$name" >&2
    exit 1
  fi
done
if [[ -L "$CONFIG_FILE" ]]; then
  printf 'Refusing to follow deployment config symlink: %s\n' "$CONFIG_FILE" >&2
  exit 1
fi

# All mutations below are additive and confined to admin's home. No old path,
# service, config, credential or runtime data is moved or removed.
mkdir -p -- "$RELEASES" "$CURRENT" "$SECRET_DIR" "$UNIT_DIR"
if [[ ! -e "$CONFIG_FILE" ]]; then
  install -m 600 "$REPO_ROOT/xl_deploy/config.toml.example" "$CONFIG_FILE"
else
  printf 'Keeping existing deployment config unchanged: %s\n' "$CONFIG_FILE"
fi
for name in xl-deploy-poll.service xl-deploy-poll.timer xl-qqbot-router.service \
            xl-qqbot-debug.service xl-qqbot-test.service xl-qqbot-prod.service xl-updata-server.service; do
  install -m 644 "$TEMPLATE_DIR/$name" "$UNIT_DIR/$name"
done
systemctl --user daemon-reload
printf '\nUnits installed but not enabled or started. Review configs, units and the old-service cutover first.\n'
