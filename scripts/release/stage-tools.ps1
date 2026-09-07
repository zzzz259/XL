#requires -Version 5.1
<#
.SYNOPSIS
    将仓库 tools/ 裁剪复制到 build/stage/tools/，供 PyInstaller datas 打包。
.DESCRIPTION
    - AssetStudio：排除 GUI 程序集、*.pdb、runtimes 下非 win 平台（只留 win、win-x64）、
      x86/、14 个本地化目录、log*.txt；保留 CLI 全套、核心 dll、x64/、AI/、Keys.json。
    - SpineViewer：runtimes 下只留 win、win-x64；排除 *.pdb、data/；其余全留。
    - vgmstream / epic7_debank_v1_0 / lua：整体复制（epic7 排除 input/、_tempo/）。
    结束时打印各目录裁剪前后体积对比表（MB）。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$srcRoot  = Join-Path $repoRoot 'xl_updata_tool\tools'
$dstRoot  = Join-Path $repoRoot 'build\stage\tools'

if (-not (Test-Path $srcRoot)) { throw "找不到 tools 目录: $srcRoot" }

function Get-DirMB([string]$Path) {
    if (-not (Test-Path $Path)) { return 0.0 }
    [math]::Round(((Get-ChildItem $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum / 1MB), 1)
}

function Invoke-RoboCopy([string]$Src, [string]$Dst, [string[]]$ExcludeDirs, [string[]]$ExcludeFiles) {
    $args = @($Src, $Dst, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/MT:8')
    if ($ExcludeDirs)  { $args += '/XD';  $args += $ExcludeDirs }
    if ($ExcludeFiles) { $args += '/XF';  $args += $ExcludeFiles }
    & robocopy @args | Out-Null
    # robocopy 退出码 0-7 均表示成功（8+ 才是失败）
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（退出码 $LASTEXITCODE）: $Src -> $Dst" }
    $global:LASTEXITCODE = 0
}

Write-Host '==> [stage-tools] 清理并重建 staging 目录...'
if (Test-Path $dstRoot) { Remove-Item $dstRoot -Recurse -Force }
New-Item -ItemType Directory -Path $dstRoot -Force | Out-Null

$report = [System.Collections.Generic.List[psobject]]::new()

# --- AssetStudio ---
# 本地化目录为 System.CommandLine / GUI 的卫星程序集，仅影响报错文案语言，CLI 功能不受影响
$asSrc = Join-Path $srcRoot 'AssetStudio'
$asLocales = 'cs','de','es','fr','it','ja','ko','pl','pt-BR','ru','tr','zh-Hans','zh-Hant'
$asExcludeDirs = @(
    'x86',
    # runtimes 下非 win 平台
    'browser', 'linux-x64', 'osx-arm64', 'osx-x64', 'win-arm64', 'win-x86'
) + $asLocales
# robocopy /XD 按目录名匹配会误伤同名目录，这里的路径均唯一，无冲突
$asExcludeFiles = @(
    'AssetStudio.GUI.exe', 'AssetStudio.GUI.dll', 'AssetStudio.GUI.deps.json',
    'AssetStudio.GUI.dll.config', 'AssetStudio.GUI.runtimeconfig.json',
    '*.pdb', 'log*.txt'
)
$before = Get-DirMB $asSrc
Write-Host '==> [stage-tools] 复制 AssetStudio（排除 GUI/pdb/x86/非 win 运行时/本地化/日志）...'
Invoke-RoboCopy $asSrc (Join-Path $dstRoot 'AssetStudio') $asExcludeDirs $asExcludeFiles
$report.Add([pscustomobject]@{ Component = 'AssetStudio'; BeforeMB = $before; AfterMB = (Get-DirMB (Join-Path $dstRoot 'AssetStudio')) })

# --- SpineViewer ---
$svSrc = Join-Path $srcRoot 'SpineViewer'
$svExcludeDirs = @(
    'data',
    # runtimes 下非 win 平台（保留 win、win-x64）
    'browser', 'fedora-x64', 'linux-arm', 'linux-arm64', 'linux-musl-x64', 'linux-x64',
    'osx', 'osx-arm64', 'osx-x64', 'win-arm64', 'win-x86'
)
$svExcludeFiles = @('*.pdb')
$before = Get-DirMB $svSrc
Write-Host '==> [stage-tools] 复制 SpineViewer（排除 pdb/data/非 win 运行时，保留 ffmpeg 与双 exe）...'
Invoke-RoboCopy $svSrc (Join-Path $dstRoot 'SpineViewer') $svExcludeDirs $svExcludeFiles
$report.Add([pscustomobject]@{ Component = 'SpineViewer'; BeforeMB = $before; AfterMB = (Get-DirMB (Join-Path $dstRoot 'SpineViewer')) })

# --- vgmstream（完整保留） ---
$vgSrc = Join-Path $srcRoot 'vgmstream'
$before = Get-DirMB $vgSrc
Write-Host '==> [stage-tools] 复制 vgmstream（完整）...'
Invoke-RoboCopy $vgSrc (Join-Path $dstRoot 'vgmstream') @() @()
$report.Add([pscustomobject]@{ Component = 'vgmstream'; BeforeMB = $before; AfterMB = (Get-DirMB (Join-Path $dstRoot 'vgmstream')) })

# --- epic7_debank_v1_0（排除测试输入与临时目录） ---
$e7Src = Join-Path $srcRoot 'epic7_debank_v1_0'
$before = Get-DirMB $e7Src
Write-Host '==> [stage-tools] 复制 epic7_debank_v1_0（排除 input/、_tempo/）...'
Invoke-RoboCopy $e7Src (Join-Path $dstRoot 'epic7_debank_v1_0') @('input', '_tempo') @()
$report.Add([pscustomobject]@{ Component = 'epic7_debank_v1_0'; BeforeMB = $before; AfterMB = (Get-DirMB (Join-Path $dstRoot 'epic7_debank_v1_0')) })

# --- lua（整体复制，含 unluac.jar） ---
$luaSrc = Join-Path $srcRoot 'lua'
$before = Get-DirMB $luaSrc
Write-Host '==> [stage-tools] 复制 lua（完整）...'
Invoke-RoboCopy $luaSrc (Join-Path $dstRoot 'lua') @() @()
$report.Add([pscustomobject]@{ Component = 'lua'; BeforeMB = $before; AfterMB = (Get-DirMB (Join-Path $dstRoot 'lua')) })

# --- 体积对比表 ---
$totalBefore = ($report | Measure-Object BeforeMB -Sum).Sum
$totalAfter  = ($report | Measure-Object AfterMB  -Sum).Sum
$report.Add([pscustomobject]@{ Component = 'TOTAL'; BeforeMB = $totalBefore; AfterMB = $totalAfter })

Write-Host ''
Write-Host '==> [stage-tools] 裁剪前后体积对比（MB）：' -ForegroundColor Cyan
$report | Format-Table -AutoSize | Out-String | Write-Host

# --- 关键文件抽查 ---
$mustExist = @(
    'AssetStudio\AssetStudio.CLI.exe',
    'AssetStudio\Keys.json',
    'SpineViewer\SpineViewer.exe',
    'SpineViewer\SpineViewerCLI.exe',
    'SpineViewer\ffmpeg.exe',
    'epic7_debank_v1_0\_subcontractors\quickbms.exe',
    'lua\unluac.jar',
    'vgmstream\vgmstream-cli.exe'
)
foreach ($rel in $mustExist) {
    $p = Join-Path $dstRoot $rel
    if (-not (Test-Path $p)) { throw "staging 缺少关键文件: $rel" }
}

$mustAbsent = @(
    'AssetStudio\AssetStudio.GUI.exe',
    'AssetStudio\x86',
    'AssetStudio\zh-Hans',
    'SpineViewer\runtimes\linux-x64',
    'SpineViewer\runtimes\osx'
)
foreach ($rel in $mustAbsent) {
    $p = Join-Path $dstRoot $rel
    if (Test-Path $p) { throw "staging 应已裁剪但仍存在: $rel" }
}

Write-Host "==> [stage-tools] 完成，${totalBefore} MB -> ${totalAfter} MB" -ForegroundColor Green
