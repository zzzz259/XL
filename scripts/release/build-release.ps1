#requires -Version 5.1
<#
.SYNOPSIS
    XL Update Tool Windows Release 一键构建编排。
.DESCRIPTION
    清理 -> 准备私有运行时 -> 裁剪 tools -> 安装构建依赖 -> 质量门禁 ->
    PyInstaller 打包 -> 自检 -> 附带说明文件 -> 体积报告 -> 打 ZIP + SHA256。
.EXAMPLE
    .\scripts\release\build-release.ps1
    .\scripts\release\build-release.ps1 -Version 1.60.2 -SkipTests -SkipRuntimes
#>
[CmdletBinding()]
param(
    [string]$Version = '1.60.2',
    [switch]$SkipTests,
    [switch]$SkipRuntimes,
    # 默认在 build/.venv 建隔离虚拟环境；加 -NoVenv 则直接使用当前 python
    [switch]$NoVenv
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$appDir    = Join-Path $repoRoot 'xl_updata_tool'
$buildDir  = Join-Path $repoRoot 'build'
$stageDir  = Join-Path $buildDir 'stage'
$releaseDir = Join-Path $repoRoot 'release'
$distDir   = Join-Path $appDir 'dist\XL'
$workDir   = Join-Path $appDir 'build'

# PS 5.1 下 EAP=Stop 会把外部程序的 stderr 输出变成 NativeCommandError，
# 统一在本函数内以 Continue 调用原生命令；非零退出码抛错。
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $eap
    }
    $global:LASTEXITCODE = $code
    if ($code -ne 0) { throw "命令失败（退出码 $code）: $Exe $($Arguments -join ' ')" }
}

function Invoke-Step([string]$Name, [scriptblock]$Action) {
    Write-Host ''
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host "==> $Name" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    $global:LASTEXITCODE = 0  # 避免上一步残留的退出码误判纯 PowerShell 步骤
    & $Action
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "步骤失败: $Name（退出码 $LASTEXITCODE）" }
}

# ---------- 1. 清理 ----------
Invoke-Step '清理 build/stage、dist、PyInstaller work 目录' {
    foreach ($p in @($stageDir, (Join-Path $buildDir 'dist'), $distDir, $workDir)) {
        if (Test-Path $p) { Remove-Item $p -Recurse -Force }
    }
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
}

# ---------- 2. 私有运行时 + tools 裁剪 ----------
if (-not $SkipRuntimes) {
    Invoke-Step '准备私有 Java 运行时（jlink）' {
        & (Join-Path $PSScriptRoot 'prepare-java.ps1')
        # 子脚本内部已用 EAP=Stop 保证真实失败会抛错；这里清掉其子进程残留的
        # LASTEXITCODE（如 jdeps 的告警退出码），避免 pwsh 下误判步骤失败
        $global:LASTEXITCODE = 0
    }
    Invoke-Step '准备私有 .NET 8 运行时' {
        & (Join-Path $PSScriptRoot 'prepare-dotnet.ps1')
        $global:LASTEXITCODE = 0
    }
} else {
    Write-Host '==> -SkipRuntimes：跳过 prepare-java / prepare-dotnet，使用已有 runtimes/' -ForegroundColor Yellow
    foreach ($p in @("$appDir\runtimes\java\bin\java.exe", "$appDir\runtimes\dotnet\dotnet.exe")) {
        if (-not (Test-Path $p)) { throw "-SkipRuntimes 但缺少已生成的运行时: $p（请先去掉 -SkipRuntimes 完整跑一次）" }
    }
}
Invoke-Step '裁剪 tools 到 build/stage/tools' {
    & (Join-Path $PSScriptRoot 'stage-tools.ps1')
    $global:LASTEXITCODE = 0
}

# ---------- 3. Python 环境 + 构建依赖 ----------
$python = 'python'
if (-not $NoVenv) {
    Invoke-Step '创建隔离虚拟环境 build/.venv' {
        $venvDir = Join-Path $buildDir '.venv'
        if (-not (Test-Path (Join-Path $venvDir 'Scripts\python.exe'))) {
            Invoke-Native 'python' @('-m', 'venv', $venvDir)
        } else {
            Write-Host "    复用已有 venv: $venvDir"
        }
    }
    $python = Join-Path $buildDir '.venv\Scripts\python.exe'
}
Write-Host "==> 使用 Python: $python"
Invoke-Step '安装构建依赖（requirements-build.txt + requirements-dev.txt）' {
    Invoke-Native $python @('-m', 'pip', 'install', '-r', (Join-Path $appDir 'requirements-build.txt'))
    # 质量门禁的 pytest/ruff 在 dev 依赖里，venv 是全新环境必须显式安装
    Invoke-Native $python @('-m', 'pip', 'install', '-r', (Join-Path $appDir 'requirements-dev.txt'))
}

# ---------- 4. 质量门禁 ----------
if (-not $SkipTests) {
    Invoke-Step 'pytest -q' {
        Push-Location $appDir
        try { Invoke-Native $python @('-m', 'pytest', '-q') }
        finally { Pop-Location }
    }
    Invoke-Step 'ruff check' {
        Push-Location $appDir
        try { Invoke-Native $python @('-m', 'ruff', 'check', 'tests', 'app') }
        finally { Pop-Location }
    }
} else {
    Write-Host '==> -SkipTests：跳过 pytest / ruff' -ForegroundColor Yellow
}

# ---------- 5. PyInstaller 打包 ----------
Invoke-Step 'pyinstaller build.spec --noconfirm --clean' {
    Push-Location $appDir
    try { Invoke-Native $python @('-m', 'PyInstaller', 'build.spec', '--noconfirm', '--clean') }
    finally { Pop-Location }
}
$xlExe = Join-Path $distDir 'XL.exe'
if (-not (Test-Path $xlExe)) { throw "打包产物缺失: $xlExe" }

# ---------- 6. 自检 ----------
Invoke-Step 'XL.exe --self-check' {
    Invoke-Native $xlExe @('--self-check')
    Write-Host '    self-check 通过（退出码 0）'
}

# ---------- 7. 附带文件 ----------
Invoke-Step '写入 LICENSE / README.txt 到 dist/XL' {
    $license = Join-Path $repoRoot 'LICENSE'
    if (Test-Path $license) {
        Copy-Item $license $distDir
        Write-Host '    已复制 LICENSE'
    } else {
        Write-Host '    仓库根无 LICENSE，跳过' -ForegroundColor Yellow
    }
    $readme = @"
XL Update Tool v$Version (Windows x64 便携版)
================================================

使用方法：
  1. 解压本 ZIP 到任意目录（路径不要含特殊权限要求）。
  2. 双击 XL.exe 启动。

说明：
  - 本包完全自包含：内置 Python/Qt、Java 运行时（Lua 反编译）、
    .NET 8 运行时（AssetStudio）、AssetStudio/SpineViewer/vgmstream 等工具。
  - 无需在系统安装 Python / Java / .NET。
  - 所有运行数据（下载的 bundle、导出产物、日志）写入 XL.exe 同级的
    data/、output/、logs/ 目录。

命令行：
  XL.exe --self-check   环境自检（检查内置运行时与工具是否完整）
  XL.exe --debug        调试模式（额外日志）
"@
    [IO.File]::WriteAllText((Join-Path $distDir 'README.txt'), $readme, [Text.UTF8Encoding]::new($true))
    Write-Host '    已生成 README.txt'
}

# ---------- 8. 体积报告 ----------
$sizeReportPath = Join-Path $buildDir 'size-report.txt'
Invoke-Step '组件体积报告' {
    function Get-DirMB([string]$Path) {
        if (-not (Test-Path $Path)) { return 0.0 }
        [math]::Round(((Get-ChildItem $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum / 1MB), 1)
    }
    $rows = [System.Collections.Generic.List[psobject]]::new()
    $totalMB = Get-DirMB $distDir
    # PyInstaller 6 onedir：datas/binaries 落在 exe 旁的 _internal/ 子目录
    $internalDir = Join-Path $distDir '_internal'
    $javaMB   = Get-DirMB (Join-Path $internalDir 'runtimes\java')
    $dotnetMB = Get-DirMB (Join-Path $internalDir 'runtimes\dotnet')
    $toolsMB  = Get-DirMB (Join-Path $internalDir 'tools')
    $rows.Add([pscustomobject]@{ Component = 'Python+Qt+app'; SizeMB = [math]::Round($totalMB - $javaMB - $dotnetMB - $toolsMB, 1) })
    $rows.Add([pscustomobject]@{ Component = 'runtimes/java (jlink JRE)'; SizeMB = $javaMB })
    $rows.Add([pscustomobject]@{ Component = 'runtimes/dotnet (.NET 8)'; SizeMB = $dotnetMB })
    foreach ($sub in Get-ChildItem (Join-Path $internalDir 'tools') -Directory) {
        $rows.Add([pscustomobject]@{ Component = "tools/$($sub.Name)"; SizeMB = (Get-DirMB $sub.FullName) })
    }
    $rows.Add([pscustomobject]@{ Component = 'TOTAL (dist/XL)'; SizeMB = $totalMB })
    $table = $rows | Format-Table -AutoSize | Out-String
    Write-Host $table
    [IO.File]::WriteAllText($sizeReportPath, "XL v$Version 组件体积报告 ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))`r`n$table", [Text.UTF8Encoding]::new($true))
    Write-Host "    已写入 $sizeReportPath"
}

# ---------- 9. ZIP + SHA256 ----------
Invoke-Step "打包 ZIP 与 SHA256（v$Version）" {
    $zipName = "XL-v$Version-win-x64-portable.zip"
    $zipPath = Join-Path $releaseDir $zipName
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path $distDir -DestinationPath $zipPath -CompressionLevel Optimal
    $zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "    ZIP: $zipPath ($zipMB MB)"

    $hash = (Get-FileHash -Algorithm SHA256 $zipPath).Hash.ToLower()
    $sumsPath = Join-Path $releaseDir 'SHA256SUMS.txt'
    [IO.File]::WriteAllText($sumsPath, "$hash  $zipName`r`n", [Text.UTF8Encoding]::new($false))
    Write-Host "    SHA256: $hash"
    Write-Host "    校验文件: $sumsPath"
}

Write-Host ''
Write-Host '==> 构建完成！' -ForegroundColor Green
Write-Host "    产物目录: $distDir"
Write-Host "    发布包:   $(Join-Path $releaseDir "XL-v$Version-win-x64-portable.zip")"
