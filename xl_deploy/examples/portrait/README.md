# Portrait deployment: supplied immutable inputs, disabled companions

These templates are preparation material, not activation authorization. No
bundle files are fetched, installed, copied from a local cp314 probe, or inferred
from PATH. The ordinary deploy allowlist, branch restart lists and bootstrap
activation remain unchanged. `xl-portrait-main.service` and
`xl-portrait-test.service` have no `[Install]` section and require a separately
approved activation marker. Do not create that marker during preflight.

## Configuration wire

The production runtime JSON uses top-level `bundle`, `decrypt_key_env`,
`metadata`, and optional `ExecutionConfig` timing/owner fields. `metadata.runtime`,
`metadata.tool_fingerprint` and top-level `package_runtime` are forbidden: the
verified resolver supplies all three. The environment variable name is a name
only; its secret value must never enter inventory, examples, logs or diagnostics.
The enabled CLI accepts only this verified wire; disabled/help remain inert.

`bundle` has explicit `roots` for server/tool/python/dotnet/java/native/display,
`inventory_sha256`, `descriptor_path`, `descriptor_sha256`, `host_files` (identity
to absolute path), `entrypoints` (python/java/unluac/opmap/prlimit to [role,path]
or [host,identity]), `dependency_roles`, `retention` (authority_path/release_path), and `loader_dirs` ([role,directory] or [host,identity] in
explicit search order; a host identity selects its real parent directory). No download URLs or automatic package discovery exist.
Each root is an immutable directory; roots cannot overlap. Use absolute release
SHA paths, not a mutable current alias, when preparing the bundle/configuration.
The two source roots have one fixed retained layout:
`RELEASE/runtime/portrait-sources/xl_updata_server` and
`RELEASE/runtime/portrait-sources/xl_updata_tool`. The release itself remains the
registered/pinned authority path. These dedicated copies are separate from the
ordinary full server/tool checkout used by existing services.

Canonical inventory is `ToolRoot/runtime/portrait/inventory.json`. UTF-8 JSON is
sorted-key, compact, no BOM/newline, no NaN and no duplicate fields. Schema 1 fields:

- `entries`: sorted unique (role,path), with role/path/size/sha256/kind. Kinds are
  source/data/managed/elf. Managed files carry platform `net8.0` and provenance. ELF entries also carry platform `linux-x86_64`, nonempty
  immutable provenance identity and `abi` from actual ELF inspection: class,
  machine, needed, soname, interpreter, rpath, runpath, GLIBC and GLIBCXX.
- `descriptor_groups`: sorted unique [role,path] lists under dotnet/app_files/
  native_files/xvfb/xkbcomp/x_libraries/runtime_files; membership cannot overlap.
  Absolute descriptor singleton values dotnet/xvfb/xkbcomp are objects; others
  are lists. Every descriptor path/hash must equal actual canonical membership.
- `source_scopes`: nonempty explicit minimum recipe directory lists for server/tool.
  Both entire source roots are exact-set verified, including root-level modules,
  regular packages and namespace packages; scopes cannot exempt siblings. Prepare
  dedicated immutable import trees with only the accepted source closure, retaining
  sibling `xl_updata_server` / `xl_updata_tool` layout for lua_decoder. Do not point
  these roots at a broad checkout containing Node/tests/desktop facilities. Every
  source directory must be implied by a pinned file; unlisted empty namespace/cache
  directories also fail closed. Every other root is exact-set.
  Bytecode (.pyc/.pyo) and unknown symlinks fail closed;
  `PYTHONDONTWRITEBYTECODE=1` prevents new cache creation but does not authorize
  stale caches. Build source closure from the accepted worker imports, including
  dynamic imports, and preserve that evidence with the immutable release.
- `capture_policy`: nonempty versioned render/capture policy identity.
- `host_files`: canonical host identities, each with size/hash/platform/provenance/
  actual ELF abi; operational paths live only in trusted config. Include loader,
  libc and all needed host libraries plus namespace tools unshare/mount/sh and
  any host executable. Actual execution expects `/usr/bin/unshare`,
  `/usr/bin/mount`, `/usr/bin/sh`; their configured paths must match the target.
- `entrypoints`, `dependency_roles`, `loader_dirs`: exact copies of the config
  semantic mappings, so changing interpreter/import/loader selection changes the
  existing tool fingerprint. Absolute deployment prefixes and secrets are absent.
  `dependency_roles` is exactly `["python"]`; active Python search paths may only
  be the two complete source roots and paths inside the complete private prefix.
- `dynamic_profile`: the mandatory restricted profile described below. Unknown or
  missing profiles are rejected before a guarded package/render worker starts.

Tool manifest includes actual ToolRoot files plus canonical inventory. Runtime
manifest includes every actual canonical file and host file. The descriptor's
absolute bytes have a separate PackageRuntime SHA, not semantic fingerprint.
Identical-byte relocation retains `model.digest(tool_manifest)` after explicitly
preparing a descriptor/config at the new location. Preflight never rewrites either.

.NET entry files are actual files under `runtime/portrait/managed/`, including
both CLI dll/deps.json/runtimeconfig.json files. CLR IL and evidenced .NET8 Linux-x64 ReadyToRun PE checking and actual
`.deps.json` membership reject missing declared assets; runtimeconfig must select
Microsoft.NETCore.App 8 FDD, never an included/self-contained framework. Do not
prune assemblies by names. A changed supplied application closure requires a fresh
local managed/package execution check before target acceptance. No Qt/WPF desktop
runtime distribution is an approved input.
Only verified managed PE and the paired deps/runtimeconfig JSON enter `app_files`.
ELF/native assets must remain at their verified original locations through
`native_files` or `x_libraries`; staging never copies a native ORIGIN dependency
graph. This contract rejects native assets in `app_files` even when the original
location's NEEDED/RUNPATH resolves successfully.

## Restricted dynamic-input profile

Canonical inventory includes this exact shape; all paths below are examples of
role-relative selections, not artifacts supplied by this repository:

```json
{
  "schema": "mesa-llvmpipe-xvfb-invariant/v1",
  "font_mode": "directory",
  "dri_driver": ["native", "dri/swrast_dri.so"],
  "glx_vendor": ["native", "libGLX_mesa.so.0"],
  "egl_vendor": ["native", "libEGL_mesa.so.0"],
  "egl_vendor_json": ["display", "egl-vendor.json"],
  "tree_manifest": ["display", "tree-manifest.json"],
  "share_tree": ["display", "share"],
  "font_tree": ["display", "share/fonts/X11/misc"]
}
```

The three actual Mesa ELF files must also be members of `x_libraries` and satisfy
the same ABI/NEEDED/loader-first-candidate verification as all other binaries.
Their directory order enters canonical `loader_dirs`.
The GLX/EGL SONAME selection is checked separately from NEEDED: the first actual
candidate in both stage loader environments and every verified native caller's
RPATH/RUNPATH must be that exact pinned vendor, including declared host aliases.
An unlisted same-name file in a host loader directory is rejected. The DRI directory contains
only the pinned real `swrast_dri.so`; prepare a regular supplied artifact there,
not a mutable link to an ambient distribution. Native dependencies remain at
their verified locations. `egl-vendor.json` is exactly the JSON object
`{"file_format_version":"1.0.0","ICD":{"library_path":"libEGL_mesa.so.0"}}`.
Both actual CLI runtimeconfig files set
`runtimeOptions.configProperties["System.Globalization.Invariant"]` to JSON true.
This profile intentionally uses invariant globalization; ICU discovery is disabled.

The actual display share tree contains only `X11` and `fonts`. Its required recipe
includes `X11/xkb/{rules/base,keycodes/evdev,symbols/pc,types/complete,compat/complete,
geometry/pc}`, `X11/locale/locale.alias`, and the selected font directory's
`fonts.dir` plus every listed font. Supply the complete needed include/resource
tree, not only these minimum recipe files. Every real tree file is an ordinary
canonical inventory entry. All directory aliases use the canonical same-root
alias mechanism; display aliases must be relative, stay inside the mounted share
tree, match every actual readlink hop, and terminate in real inventoried trees.
Unknown files, aliases, empty directories and unsupported layouts fail closed.

The narrow alternative `font_mode:"built-ins"` forbids the `font_tree` field.
It requires actual Xvfb NEEDED `libXfont2.so.2` to resolve to its unique verified
ELF provider in x_libraries, and both real pinned Xvfb/provider binaries to contain
the built-in-font implementation marker. The operational profile derives two
additional RuntimeFile refs, `xvfb` and `builtin_font_provider`, from those exact
descriptor/ELF identities. Xvfb then receives precisely `-fp built-ins`; it does
not rely on fallback from a broken directory. The mode itself is canonical and
changes the fingerprint. A plain TTF directory without fonts.dir cannot use the
directory mode. If a supplied OS xkb directory is a symlink outside the private
share tree (for example X11/xkb -> ../xkeyboard-config-2), explicitly materialize
the actual target bytes into private share/X11/xkb during new-tree preparation,
record the source alias provenance, and inventory those real copied bytes. Do not
retain an escaping alias or broaden the profile to the entire host /usr/share.

`tree-manifest.json` is a separate bounded canonical JSON data file outside that
share tree, with `schema:1`, `files:[{path,sha256}]`, and
`aliases:[{path,target}]`; paths/targets are share-relative and lists are sorted
by path. Its entire file/alias membership must equal canonical inventory entries
and aliases under the share tree. It never lists itself. The inventory pins this
manifest's real size/SHA. No absolute installation prefix or secret enters it.

The absolute descriptor keeps its seven original groups and adds one small
`dynamic_profile` object: schema is unchanged, the five file selections become
`{path:absolute,sha256}` refs, and share_tree/font_tree become absolute directories.
It contains no inline tree-member arrays. Existing 2 MiB descriptor and request
bounds remain unchanged. The worker verifies the bounded referenced tree manifest
and actual complete tree, then retains only a frozen eight-string profile;
full tree lists and loaded descriptor documents are discarded before heavy work.
The inventory and manifest therefore retain the same fingerprint after relocation,
while the absolute descriptor has its own new SHA as before.

The guarded runner actually enforces the profile. It clears ambient GL/EGL/Mesa/
Gallium/.NET/loader/font/XKB/XDG overrides; pins swrast/llvmpipe and Mesa GLX/EGL
selection; disables Mesa shader caches; forces invariant globalization; and uses
fresh owned HOME/config/cache/runtime directories. Xvfb receives explicit `-fp`
with the verified font directory. Supported Xvfb bytes must contain its fixed
`/usr/share/X11/xkb` path and either the complete `/usr/bin/xkbcomp` path or the
standard `/usr/bin` plus xkbcomp command-template representation.

Before binding resources, the worker verifies the private user mapping and that
`NS_GET_USERNS` identifies that user namespace as the mount namespace owner.
Only inside that namespace, existing `/usr/share` receives the validated share
tree and existing `/etc` receives a new empty owned directory. Both binds are
remounted read-only/nodev/nosuid/noexec; all mount calls use `-n` and the existing
resource lease, guard and deadline. No host target is created or edited. The
empty `/etc` and private HOME exclude ambient drirc/fontconfig/loader-cache files;
the supplied share tree excludes ambient GLVND vendor directories. Inputs must
not be beneath overlaid `/usr/share`, `/etc`, `/tmp`, or `/usr/bin`, or overlap the
owned attempt. This is one supported profile, not a generic arbitrary-dlopen
proof. Other driver/display/globalization combinations require a new reviewed
contract, not a provenance label or a bypass switch.

To prepare a new immutable companion source tree, copy the accepted revision's
`SOURCE_FILES` and `SOURCE_DIRECTORIES` recipe in `bundle.py` into the fixed sibling
layout above, with no bytecode or unrelated checkout trees. Add the actual paired
FDD files, unluac/opmap and canonical inventory under its ToolRoot. Retain file
hashes against that exact revision; record the dedicated source recipe, supplied
runtime artifacts and alias graph. Build the relative display tree manifest from
actual files first, then its inventory entry, then canonical inventory and finally
the separate absolute descriptor/config. Do not edit an active immutable tree or
invoke ordinary deploy to activate the companion. Existing desktop build scripts
remain unchanged; the private FDD configuration must explicitly set invariant
globalization, and any changed FDD combination still requires a fresh controlled
managed/package/Native acceptance. The current full AL8 artifact set is unready.

## Read-only inspection and explicit execution

Run with the prepared same-revision server+tool on PYTHONPATH and bytecode writes
disabled. The standalone bundle config is the object stored in `runtime.json`'s
`bundle` member, not the full runtime JSON:

```sh
python -P -m server_app.portraits.bundle --config /absolute/bundle-config.json
python -P -m server_app.portraits.cli health --enable --config /absolute/runtime.json
```

Bundle preflight reports configuration verification and `execution_admitted=false`.
It checks files only and imports the pure Reader; it does not initialize stores,
clear busy guards, probe network, render, install or claim live kernel caps.
Runtime health additionally validates configured scope/store/core journal and
explicit guards through existing runtime validation. Missing/malformed/wrong-scope
or busy guards remain unready. The actual owner updater must initialize its own
guard via its controlled startup/noop; do not manufacture a quiet guard file.

Without `--enable`, runtime CLI returns disabled before reading config or secrets.
After separate approval, exact existing commands are `init-store`, `tick`, `daemon`,
`health` and `manual`; every enabled command uses `--config`. Historical selection
is a bound JSON request, not invented command flags:

```sh
python -P -m server_app.portraits.cli manual --enable --config /absolute/runtime.json --request /absolute/request.json --dry-run
python -P -m server_app.portraits.cli manual --enable --config /absolute/runtime.json --request /absolute/request.json
```

The request has request_id, version, characters (list of character IDs or `all`),
skins (list of skin IDs or `all`), authenticated prepared_ref and request_ref.
Use one explicit pinned version and request_id; dry-run requires the authenticated
metadata baseline and guards and does not fetch or write. Historical manual jobs
must never become automatic baseline/backfill or automatic announcements.

## Isolated main/test policy

Use separate updater/source/CDN/data/store/work/artifacts/ledger/notification/cursor
identities per tier. Both tiers share `/home/admin/xl_deploy/shared/portrait/host.lock`.
Both configurations enumerate all main and test publication guards plus every
still-competing legacy updater status, with exact expected scope. Never substitute
main CDN/data into test or alter normal test CDN polling policy as a side effect.

Companion caps: one CPU, CPUQuota100%, whole cgroup MemoryHigh384M/MemoryMax512M, no swap, core0,
KillMode control-group, finite stop30s; no child escapes this cap. ResourcePolicy
uses memory_bytes536870912, admission_bytes671088640 (free640MiB),
abort_bytes134217728 (cancel below128MiB free), stage_seconds120,
updater-priority admission and shared host lock. Effective kernel/cgroup evidence
is checked at admission; unit text alone cannot prove enforcement. Stop only the
owned companion and its process tree. Existing updater/Bot caps are unchanged.

The first automatic run records metadata-only baseline and never renders history.
Only subsequent authenticated new/changed rows become work; unchanged rows stay
unchanged. Numeric skin ordinals cover every catalog skin. Bot query with no
ordinal retains cards; explicit numeric portrait query follows accepted consumer
syntax. Old-source/tool fallback must identify itself truthfully. The existing
single Gateway owns QQ; no second connection. Five-plus-three serial selection,
quiet hours and notifier ACK/delivery cursor behavior belong to the frozen Bot
consumer, whose accepted option names appear in the tier-specific Bot examples.
Failures/quarantine stay visible; manual retries need explicit bounded intent.

## Remaining activation gate

Do not activate from fixtures or old smoke evidence. Record immutable release SHA,
all artifact hashes, exact FDD closure, compatible Python3.11/3.12 wheels/metadata,
complete ELF architecture/ABI/NEEDED/dynamic-loader provenance including GL/Mesa/
LLVM/CLR/JRE/Python native components. Every supplied ELF requires <=GLIBC2.28 and
<=GLIBCXX3.4.25. Unknown evidence fails closed. The existing 22 AL8 libraries are
not complete graphics/runtime closure, and Ubuntu display binaries are not AL8
proof. UnityPy is pinned1.25.0; other package pins await compatible actual metadata.

After local bundle review, one separately approved bounded test-only real campfire
skin may be proposed: current authenticated metadata baseline, no groups or
notifications, no batch/history, one CPU/tree512MiB/noSwap/120s stage/free640MiB/cancel below128MiB free,
all updater guards, finite own-tree stop. Old original-host authorization is spent.
No deployment, restart of existing services, remote operation or Native test is
performed by this change. PR/push remain parent-controlled; review trusted-base
CI behavior before any publication.

## Fixed source revision and host aliases

Companion unit source paths never follow `current/main` or `current/test`.
`runtime.env` explicitly fixes PORTRAIT_PYTHON and PYTHONPATH to one approved SHA;
PYTHONPATH begins with that release's server then tool root. `/usr/bin/env` is
an explicit pinned host executable, not a PATH search for Python. The production
resolver checks every already imported server_app/app module against actual pinned
source files and the first two import roots. Changing an ordinary current symlink
cannot move the companion's source imports. Stop/reconfigure/start the companion
only under a separate bounded activation approval when upgrading its bundle.

Before activation, register and add the exact immutable release absolute path to
existing release-storage.json `pins` (schema1 retains pins/rollback/releases).
Do not remove that pin while either tier references it, even when the companion is
stopped. Private shared runtime roots have separate retention ownership and must
remain immutable and retained with this bundle. No automatic pin/restart/cleanup
is introduced here. Existing Bot jobs/notifier units retain their same-ref model.

For a known fixed host symlink, canonical host entry may add `alias_of` pointing
to another host identity. Every actual readlink hop is explicit, at most32 with no
cycle, ending in a regular verified target; alias and target agree on actual byte/
ABI identity and both enter runtime_manifest. Unknown symlinks remain forbidden.
Descriptor entries remain real files. Host aliases such as sh are operational
namespace inputs, never synthetic descriptor aliases.

Descriptor groups may reference [host,identity] for actual verified host libraries.
Their real directories are included in accepted native_files/x_libraries loader
environments, and canonical loader_dirs must match the actual managed directory
order. Xvfb uses only x_libraries; Python/JRE/host tools receive the pinned host
directories through SourceRuntime.environment. RPATH and RUNPATH are expanded with
bounded ORIGIN syntax and correct environment precedence. Every NEEDED first
candidate must be the declared actual provider; default ld.so.cache is not treated
as proof. Runtime aliases have explicit chain identities. Preload/AUDIT are absent
from the constructed SourceRuntime child environment and cleared by ManagedLaunch.

The JSON examples intentionally contain invalid placeholder hashes, scopes and CDN
hosts: they fail closed until an operator supplies actual evidence. All new flags
are false. No example silently initializes guards or picks a competing updater.

The retention authority has no standalone pin command in this repository. Its
writer uses `_save_authority` while the deployment state lock is held. A future
approved operator update must coordinate controller/deployment/cleanup writers,
use the same lock and atomic authority update, or explicitly arrange a stopped
maintenance window before editing. Never overwrite JSON alongside a live writer.
The bundle public resolver only reads and checks the registered explicit pin.

Prepare Python without stale bytecode (for example use pip --no-compile during
explicit creation of a new immutable tree). Health never removes caches. A venv
interpreter and site-packages alone are insufficient: private base Python stdlib,
libpython and native extensions and their host ELF closure need actual identities.
Do not transplant a cp314 Ubuntu probe to the target Python3.11/3.12 AL8 runtime.

## Final Python, consumer and preparation contract

`roots.python` is the complete immutable private base prefix, including stdlib,
libpython, stdlib extension modules and venvs. The invoked Python is the declared
venv alias inside that prefix; it must equal actual sys.executable. Do not resolve
the invocation path to the base interpreter and bypass pyvenv.cfg. Actual
sys.base_prefix, private venv config (`include-system-site-packages=false`), disabled
user site, all loaded module bytes, active sys.path directories and archives are
verified. Ordinary prefix/site-package files are all inventoried, including
METADATA/WHEEL/RECORD; compatible interpreter/Linux wheel tags and base dependency
metadata are checked without package imports, and UnityPy is exactly1.25.0. Unknown
version/marker/direct-reference/extra formats fail closed rather than guessing.
Active stdlib zip files must be pinned, bounded and contain no stale bytecode.
Executable .pth hooks, external .pth paths, sitecustomize/usercustomize hooks and
isolated _pth overrides are not admitted by this ordinary-private-venv contract.

Canonical `directory_aliases` is a list of exact
`{role,path,target_role,target_path}` objects. Only same-root aliases are admitted;
every actual readlink hop is explicit, no root alias/cycle/cross-root link, chain<=32.
All real target tree files remain listed once; enumeration does not follow alias
copies. For a normal Linux venv this may bind python `venvs/main/lib64` to
`venvs/main/lib`; bin/python uses the separate explicit host file alias chain to
python-base. The base executable has one real canonical file identity; host aliases
and its host target mapping must agree on the same actual bytes.

The production parent uses -P. Parent-approved unchanged worker boundary:
SourcePrepare/Package/Render/Transfer workers retain their accepted argv, including
SourcePrepare's `python -m` (no -P). The only accepted cwd exception is the newly
created private0700 owned attempt directory created by accepted preparation under
ResourceSlot, containing fixed request/result/asset paths. It is not arbitrary
operator cwd or a queued job code path. Existing workers do not execute/import
untrusted Python files written there. Do not claim workers also use -P or allow a
broader unknown import path. This decision preserves all accepted launch code;
a broad reviewer may independently inspect that exact boundary.

Updater server.instance_id, runtime metadata.instance_id and Bot instance_id must
agree: examples use stable-bot for main and test-bot for test, with distinct source
IDs, roots and configured CDN. A portrait_delivery entry is keyed by an existing
watch.update_sources name. Its enabled fields are exactly source_id/core_scope/
core_journal/runtime_ledger/portrait_store/artifact_root/updater_root/
image_delivery_mode=local_multipart; notification.portrait_roots uses the same Bot
instance/source and artifact root. Disabled entries contain only enabled=false.
Portrait query has its separate default-false portrait_query section. `@Bot 角色名`
keeps ordinary cards; `@Bot 角色名 1` selects ordinal1 (trailing ASCII digits; exact
full character names retain priority). Out-of-range ordinals show available skins.

Opt-in card consumption records cleanup_deferred: live producer folders and the
consumer's content-addressed card_snapshots remain retained after durable delivery
ACK. Budget disk for both. Only a separately coordinated cleanup may remove them;
no background cleanup daemon or unsafe concurrent deletion is added. Disabled
legacy behavior and its normal cleanup remain unchanged. Do not enable all units
or alter ordinary polling to activate this isolated feature.

Do not use scripts/build-portrait-scene-renderer.py output as Linux FDD directly:
its includedFrameworks config is desktop/self-contained. Tracked SpineViewerCLI
win-x64 deps/runtimepack8.0.30 are also not a Linux profile. Preserve desktop script
behavior. Explicit preparation must derive Linux FDD app configs/deps from actual
accepted headless build metadata, retain all declared headless dependencies
(including audio when declared), record source/build/output hashes and prepare the
absolute descriptor. Old private app-al8 PortraitSceneCLI deps is a traceable
candidate, not the missing final paired CLI closure. Preparation is separate from
health and requires actual artifacts and a fresh parent-controlled packaged check;
health never edits deps/configs/descriptors or removes baggage by filename.

Historical original-host evidence (2026-10-31, not current health) used cgroup v1
and recorded host SwapTotal0. Accepted Executor already supports v1/hybrid and
fails closed for v1 with host swap>0; memsw==memory alone does not prove no swap.
MemorySwapMax0 in a template is not original-host enforcement evidence. Real health/
admission must inspect actual hierarchy limits and host swap through the accepted
boundary implementation. No new SSH/host inspection was performed here.

Incremental input identity uses each skin's normalized config and dependency
closure, not the entire DataOwner/BaseFashion blob. Adding a new character does not
mechanically mark every old skin changed; actual per-skin differences drive work.

MemoryHigh=384M is the cache-pressure/reclaim threshold; MemoryMax=512M is the
hard whole-tree limit. Historical 256MiB measurements retain their original budget.
