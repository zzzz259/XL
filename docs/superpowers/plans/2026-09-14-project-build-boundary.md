# Project Build Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `xl_updata_tool`'s release scripts and generated build outputs inside the subproject and repair all path consumers.

**Architecture:** Project-owned PowerShell scripts derive `$appDir` from `PSScriptRoot`; repository-level CI remains at `.github/workflows`. All generated build, staging, runtime, distribution, and release paths become project-local.

**Tech Stack:** PowerShell 5.1+, GitHub Actions, PyInstaller, Python project virtual environment.

**Spec:** `docs/superpowers/specs/2026-09-14-project-build-boundary-design.md`

## Global Constraints

- Each project owns its source, tests, dependencies, tools, build scripts, and generated outputs.
- Root CI remains repository-level orchestration.
- Do not alter application behavior or third-party tool contents.
- Derive paths from `PSScriptRoot`; do not rely on the caller's current directory.
- Use the project Python environment for validation.

### Task 1: Relocate and repair release scripts

**Files:**
- Move: `scripts/release/` to `xl_updata_tool/scripts/release/`
- Modify: `xl_updata_tool/scripts/release/build-release.ps1`
- Modify: `xl_updata_tool/scripts/release/stage-tools.ps1`
- Modify: `xl_updata_tool/scripts/release/prepare-java.ps1`
- Modify: `xl_updata_tool/scripts/release/prepare-dotnet.ps1`

- [ ] Move the exact `scripts` directory into `xl_updata_tool`.
- [ ] Resolve `$appDir` as two levels above each script's `PSScriptRoot`, and `$repoRoot` as its parent.
- [ ] Make build and release output directories project-local.
- [ ] Keep private runtime paths under `xl_updata_tool/runtimes`.

### Task 2: Repair consumers and ignore rules

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `xl_updata_tool/README.md`
- Modify: `xl_updata_tool/build.spec`
- Modify: `.gitignore`

- [ ] Update CI and documentation commands to the new project-owned script path.
- [ ] Update `build.spec` staging and error-message paths.
- [ ] Ignore project-local `build`, `release`, `dist`, and runtimes without broad root patterns that could hide unrelated projects.
- [ ] Search for stale executable references and remove only obsolete operational references.

### Task 3: Verify the path contract

**Files:**
- Test: repository path checks, PowerShell parse checks, project tests, Ruff, compile/import smoke.

- [ ] Parse all release scripts with PowerShell without executing them.
- [ ] Run `pip check`, `pytest`, Ruff, and Python compile/import using `.venv`.
- [ ] Verify the new script path and project-local output path strings.
- [ ] Confirm `git status` and list intended changes.
