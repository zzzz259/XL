#requires -Version 5.1
<#
.SYNOPSIS
    下载并安装私有 .NET 8 运行时（仅 Microsoft.NETCore.App，x64）到 xl_updata_tool/runtimes/dotnet。
.PARAMETER Version
    可选，显式指定 8.0.x 版本（如 8.0.20）。未指定时解析最新 8.0.x 并打印。
#>
[CmdletBinding()]
param(
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$buildDir   = Join-Path $repoRoot 'build'
$installDir = Join-Path $repoRoot 'xl_updata_tool\runtimes\dotnet'
$scriptPath = Join-Path $buildDir 'dotnet-install.ps1'

# PS 5.1 下 EAP=Stop 会把外部程序的 stderr 输出变成 NativeCommandError，
# 统一在本函数内以 Continue 调用原生命令，返回输出文本与退出码。
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # ErrorRecord（来自 stderr）取其 Message，避免混入 "exe :" 前缀
        $output = (& $Exe @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
        } | Out-String)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $eap
    }
    $global:LASTEXITCODE = $code
    return @{ Output = $output; ExitCode = $code }
}

# 兼容旧版 PowerShell 的 TLS
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

# --- 解析版本 ---
if ([string]::IsNullOrWhiteSpace($Version)) {
    Write-Host '==> [prepare-dotnet] 查询最新 .NET Runtime 8.0.x 版本...'
    $latestUrl = 'https://builds.dotnet.microsoft.com/dotnet/Runtime/8.0/latest.version'
    $Version = (Invoke-WebRequest -Uri $latestUrl -UseBasicParsing).Content.Trim()
    if ($Version -notmatch '^\d+\.\d+\.\d+') { throw "无法解析最新版本号: '$Version'" }
}
Write-Host "==> [prepare-dotnet] 目标版本: $Version (Microsoft.NETCore.App, x64)"

# --- 下载 dotnet-install.ps1 ---
if (-not (Test-Path $scriptPath)) {
    Write-Host '==> [prepare-dotnet] 下载 dotnet-install.ps1 ...'
    Invoke-WebRequest -Uri 'https://dot.net/v1/dotnet-install.ps1' -OutFile $scriptPath -UseBasicParsing
} else {
    Write-Host "==> [prepare-dotnet] 复用已有安装脚本: $scriptPath"
}

# --- 安装（仅 Microsoft.NETCore.App 运行时，不含 SDK/ASP.NET/Desktop） ---
if (Test-Path $installDir) {
    Write-Host "==> [prepare-dotnet] 清理旧运行时: $installDir"
    Remove-Item $installDir -Recurse -Force
}
New-Item -ItemType Directory -Path $installDir -Force | Out-Null

Write-Host "==> [prepare-dotnet] 安装到 $installDir ..."
$global:LASTEXITCODE = 0  # dotnet-install.ps1 是脚本，未必设置 $LASTEXITCODE；先初始化再判断
& $scriptPath -Runtime dotnet -Architecture x64 -Version $Version -InstallDir $installDir
if ($LASTEXITCODE -ne 0) { throw "dotnet-install.ps1 失败，退出码 $LASTEXITCODE" }

# --- 验证 ---
$dotnetExe = Join-Path $installDir 'dotnet.exe'
if (-not (Test-Path $dotnetExe)) { throw "安装结果缺少 dotnet.exe: $installDir" }
$r = Invoke-Native $dotnetExe @('--list-runtimes')
if ($r.ExitCode -ne 0) { throw "dotnet.exe --list-runtimes 验证失败，退出码 $($r.ExitCode)`n$($r.Output)" }
Write-Host $r.Output
if ($r.Output -notmatch 'Microsoft\.NETCore\.App 8\.0\.\d+') {
    throw "验证失败：--list-runtimes 输出不含 Microsoft.NETCore.App 8.0.x`n$($r.Output)"
}

$sizeMB = [math]::Round(((Get-ChildItem $installDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "==> [prepare-dotnet] 完成，私有 .NET 运行时体积 ${sizeMB} MB" -ForegroundColor Green
