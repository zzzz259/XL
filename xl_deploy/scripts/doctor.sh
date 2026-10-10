#!/usr/bin/env bash
set -u

readonly HOME_DIR="${HOME:?HOME must be set}"
readonly DEPLOY_ROOT="$HOME_DIR/xl_deploy"
readonly SECRET_DIR="$HOME_DIR/.config/xl_deploy"
readonly UNIT_DIR="$HOME_DIR/.config/systemd/user"
readonly MAIN_API_ENV_FILE="$HOME_DIR/.config/xl_updata_server/api.env"
readonly TEST_API_ENV_FILE="$HOME_DIR/.config/xl_updata_server-test/api.env"

if [[ "$HOME_DIR" != "/home/admin" ]]; then
  printf 'This deployment layout is pinned to /home/admin; refusing HOME=%s\n' "$HOME_DIR" >&2
  exit 1
fi

printf '%s\n' 'XL deployment read-only doctor (does not change files or services)'
for path in "$DEPLOY_ROOT/releases" "$DEPLOY_ROOT/current/debug" "$DEPLOY_ROOT/current/test" \
            "$DEPLOY_ROOT/current/main" "$DEPLOY_ROOT/state"; do
  if [[ -L "$path" ]]; then printf 'symlink: %s -> %s\n' "$path" "$(readlink -- "$path")"
  elif [[ -e "$path" ]]; then printf 'present: %s\n' "$path"
  else printf 'missing: %s\n' "$path"; fi
done

for path in "$DEPLOY_ROOT/config.toml" "$SECRET_DIR/secrets.env" "$SECRET_DIR/router.token" \
            "$MAIN_API_ENV_FILE" "$TEST_API_ENV_FILE" \
            "$HOME_DIR/xl_qqbot/config.toml" "$HOME_DIR/xl-qqbot-debug/config.toml" \
            "$HOME_DIR/xl-qqbot-test/config.toml" "$HOME_DIR/xl_qqbot-test/config.toml" \
            "$HOME_DIR/xl_updata_server/config.toml" \
            "$HOME_DIR/xl_updata_server-test/config.toml"; do
  if [[ "$path" == "$SECRET_DIR/router.token" && -L "$SECRET_DIR/router.token" ]]; then
    printf 'WARNING: router.token must be a regular file, not a symlink: %s\n' "$path"
  elif [[ -f "$path" ]]; then
    mode="$(stat -c '%a' -- "$path" 2>/dev/null || stat -f '%Lp' -- "$path" 2>/dev/null || printf '?')"
    printf 'file mode=%s: %s\n' "$mode" "$path"
    if [[ ( "$path" == "$SECRET_DIR/secrets.env" || "$path" == "$SECRET_DIR/router.token" || \
            "$path" == "$MAIN_API_ENV_FILE" || "$path" == "$TEST_API_ENV_FILE" ) && "$mode" != 600 ]]; then
      printf 'WARNING: deployment secret files must be chmod 600\n'
    fi
  else printf 'missing: %s\n' "$path"; fi
done

for unit in xl-deploy-poll.timer xl-deploy-poll.service xl-qqbot-router.service \
            xl-qqbot-test.service xl-qqbot-prod.service \
            xl-updata-server.service xl-updata-server-test.service; do
  printf '\n--- %s ---\n' "$unit"
  systemctl --user status --no-pager "$unit" 2>&1 || true
done

printf '\n--- deployment poller recent journal ---\n'
journalctl --user -u xl-deploy-poll.service -n 50 --no-pager 2>&1 || true
