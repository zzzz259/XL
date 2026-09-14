# Project Build Boundary Design

## Goal

Make `xl_updata_tool` self-contained as a subproject by relocating its release scripts and generated build/release outputs under the project directory, while keeping the repository-level GitHub Actions workflow as the monorepo orchestrator.

## Scope

- Move `scripts/release/` to `xl_updata_tool/scripts/release/`.
- Keep `.github/workflows/` at repository root.
- Change project build, staging, runtime, PyInstaller, ZIP, and size-report paths to use `xl_updata_tool/build/`, `xl_updata_tool/release/`, and `xl_updata_tool/dist/`.
- Update documentation, CI references, and ignore rules.
- Do not change application behavior or third-party tool contents.

## Path Contract

- `$appDir` is the `xl_updata_tool` directory resolved from a release script's `PSScriptRoot`.
- `$repoRoot` is the parent of `$appDir`, used only for repository-level metadata such as the root license.
- Staging is `xl_updata_tool/build/stage/tools`.
- Private runtimes are `xl_updata_tool/runtimes/{java,dotnet}`.
- PyInstaller work and output are `xl_updata_tool/build/` and `xl_updata_tool/dist/XL/`.
- Release ZIPs and checksums are `xl_updata_tool/release/`.

## Compatibility

The root workflow remains the entry point for tag releases and invokes `xl_updata_tool/scripts/release/build-release.ps1`. Running the script from either repository root or project directory must resolve the same project paths because all scripts derive paths from `PSScriptRoot`.

## Verification

- Search for stale root `scripts/release`, `build/stage`, and `release/` references outside documentation history.
- Parse all four PowerShell scripts.
- Run project `pytest`, Ruff, and Python compile/import checks with the project virtual environment.
- Run the staging script in a controlled validation only if needed; do not run a full release build unless explicitly requested.
- Confirm `git status` contains only intended changes.
