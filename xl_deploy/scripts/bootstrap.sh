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

report_config_mappings() {
  local config_path="$1"
  local working_directory="$2"
  if [[ "$config_path" != /* ]]; then
    if [[ -z "$working_directory" ]]; then
      printf 'Config/data mappings unavailable: relative config path has no WorkingDirectory: %s\n' "$config_path"
      return
    fi
    config_path="$working_directory/$config_path"
  fi
  if [[ ! -f "$config_path" ]]; then
    printf 'Config/data mappings unavailable: config is missing: %s\n' "$config_path"
    return
  fi
  python3 - "$config_path" "$working_directory" <<'PY'
import sys
import tomllib
from pathlib import Path

config_path = Path(sys.argv[1]).resolve()
working_directory = Path(sys.argv[2]).resolve() if sys.argv[2] else None
try:
    with config_path.open("rb") as stream:
        config = tomllib.load(stream)
except Exception as exc:
    print(f"Config/data mappings unavailable: {config_path} ({type(exc).__name__})")
    raise SystemExit(0)

path_keys = {
    "watch": ("data_dir", "outbox_dir", "character_data", "versions_dir"),
    "paths": ("data_dir", "unluac_jar", "unluac_opmap", "character_card_font"),
    "deployment": ("root", "repository", "state_dir", "releases_root", "current_root",
                   "backend_config", "backend_data"),
    "router": ("token_file",),
}
for section, keys in path_keys.items():
    values = config.get(section, {})
    if not isinstance(values, dict):
        continue
    for key in keys:
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        mapped = Path(value).expanduser()
        label = "configured data directory" if key == "data_dir" else f"configured {key}"
        if not mapped.is_absolute():
            if section == "watch" and working_directory is None:
                print(f"{label}: unresolved (relative path; effective WorkingDirectory unavailable)")
                continue
            base_dir = working_directory if section == "watch" else config_path.parent
            mapped = base_dir / mapped
        mapped = mapped.resolve()
        state = "exists" if mapped.exists() else "missing"
        print(f"{label}: {mapped} ({state})")
PY
}

find_unit_working_directory() {
  local unit_name="$1" unit_dir candidate
  shift
  for unit_dir in "$@"; do
    candidate="$unit_dir/$unit_name"
    if [[ -f "$candidate" ]]; then
      sed -n 's/^WorkingDirectory=//p' "$candidate" | tail -n 1
      return
    fi
  done
}

report_unit_source() {
  local unit_file="$1" unit_name="$2" inherited_directory="${3:-}"
  local working_directory config_path
  working_directory="$(sed -n 's/^WorkingDirectory=//p' "$unit_file" | tail -n 1)"
  [[ -n "$working_directory" ]] || working_directory="$inherited_directory"
  printf '\nExisting unit definition: %s (unit %s)\n' "$unit_file" "$unit_name"
  sed -n -E '/^(WorkingDirectory|EnvironmentFile)=/p' "$unit_file" | sed 's/^/  /'
  config_path="$(sed -n -E 's/^ExecStart=.* --config[ =]([^ ]+).*$/\1/p' "$unit_file" | tail -n 1)"
  if [[ -z "$config_path" && "$unit_name" == xl-qqbot* && -n "$working_directory" ]]; then
    config_path="$working_directory/config.toml"
  fi
  if [[ -n "$config_path" ]]; then
    printf '  Config mapping candidate: %s\n' "$config_path"
    report_config_mappings "$config_path" "$working_directory" | sed 's/^/  /'
  else
    printf '  Config mapping: no --config argument or WorkingDirectory found in this definition\n'
  fi
}

report_effective_unit() {
  local unit_name="$1" effective_properties effective_directory effective_exec effective_config
  if ! command -v systemctl >/dev/null 2>&1; then
    printf '\nEffective unit state unavailable for %s: systemctl is missing.\n' "$unit_name"
    return
  fi
  effective_properties="$(systemctl --user show "$unit_name" --no-pager \
    --property=LoadState --property=FragmentPath --property=DropInPaths \
    --property=WorkingDirectory --property=EnvironmentFiles 2>/dev/null || true)"
  if [[ -z "$effective_properties" ]]; then
    printf '\nEffective unit state unavailable for %s; source-level definitions are listed above.\n' "$unit_name"
    return
  fi
  printf '\nEffective systemd properties for %s (secret values excluded):\n' "$unit_name"
  printf '%s\n' "$effective_properties" | sed 's/^/  /'
  effective_directory="$(sed -n 's/^WorkingDirectory=//p' <<<"$effective_properties" | tail -n 1)"
  [[ "$effective_directory" == /* ]] || effective_directory=""
  effective_exec="$(systemctl --user show "$unit_name" --no-pager --property=ExecStart --value 2>/dev/null || true)"
  effective_config="$(printf '%s' "$effective_exec" | python3 "$REPO_ROOT/xl_deploy/operator_inventory.py" --extract-systemd-config)"
  if [[ -z "$effective_config" && "$effective_exec" == *"--config"* ]]; then
    printf '  Effective config mapping unresolved: explicit --config could not be parsed safely.\n'
    return
  fi
  if [[ -z "$effective_config" && "$unit_name" == xl-qqbot* && -n "$effective_directory" ]]; then
    effective_config="$effective_directory/config.toml"
  fi
  if [[ -n "$effective_config" ]]; then
    printf '  Effective config mapping: %s\n' "$effective_config"
    report_config_mappings "$effective_config" "$effective_directory" | sed 's/^/  /'
  else
    printf '  Effective config mapping: not resolved from systemd ExecStart/WorkingDirectory\n'
  fi
}

is_project_unit_source() {
  local unit_name="$1" unit_file="$2"
  [[ "$unit_name" == xl-* ]] || grep -Eiq 'xl[-_ ]|/xl' "$unit_file"
}

report_existing_units() {
  local unit_file unit_name dropin_file dropin_directory inherited_directory unit_dir
  local found=false
  local -a unit_dirs=()
  local -A seen_dirs=()
  local -A seen_units=()
  while IFS= read -r unit_dir; do
    [[ -n "$unit_dir" && -d "$unit_dir" && -z "${seen_dirs[$unit_dir]:-}" ]] || continue
    seen_dirs["$unit_dir"]=1
    unit_dirs+=("$unit_dir")
  done < <(
    systemd-analyze --user unit-paths 2>/dev/null || true
    printf '%s\n' \
      "$UNIT_DIR" "${XDG_CONFIG_HOME:-$HOME_DIR/.config}/systemd/user" \
      "${XDG_DATA_HOME:-$HOME_DIR/.local/share}/systemd/user" \
      "$HOME_DIR/.local/share/systemd/user" \
      /etc/systemd/user /run/systemd/user /usr/local/lib/systemd/user \
      /usr/lib/systemd/user /usr/share/systemd/user
  )
  for unit_dir in "${unit_dirs[@]}"; do
    for unit_file in "$unit_dir"/*.service; do
      [[ -f "$unit_file" ]] || continue
      unit_name="${unit_file##*/}"
      is_project_unit_source "$unit_name" "$unit_file" || continue
      found=true
      seen_units["$unit_name"]=1
      inherited_directory="$(find_unit_working_directory "$unit_name" "${unit_dirs[@]}")"
      report_unit_source "$unit_file" "$unit_name" "$inherited_directory"
    done
    for dropin_file in "$unit_dir"/*.service.d/*.conf; do
      [[ -f "$dropin_file" ]] || continue
      dropin_directory="${dropin_file%/*}"
      unit_name="${dropin_directory##*/}"
      unit_name="${unit_name%.d}"
      is_project_unit_source "$unit_name" "$dropin_file" || continue
      found=true
      seen_units["$unit_name"]=1
      inherited_directory="$(find_unit_working_directory "$unit_name" "${unit_dirs[@]}")"
      report_unit_source "$dropin_file" "$unit_name" "$inherited_directory"
    done
  done
  for unit_name in "${!seen_units[@]}"; do
    report_effective_unit "$unit_name"
  done
  if [[ "$found" != true ]]; then
    printf '\nNo existing user service unit files found in the discovered systemd search paths.\n'
  fi
}

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
  report_existing_units
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
