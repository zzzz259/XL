#requires -Version 5.1
<#
.SYNOPSIS
    用 jlink 为 unluac.jar 生成精简的私有 Java 运行时（xl_updata_tool/runtimes/java）。
.NOTES
    需要 JDK 21+（unluac.jar 按 Java 21 编译，class file version 65）。
.PARAMETER JdkHome
    可选，显式指定 JDK 根目录（需含 bin/jlink.exe）。默认依次尝试 JAVA_HOME、PATH。
#>
[CmdletBinding()]
param(
    [string]$JdkHome
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$jarPath  = Join-Path $repoRoot 'xl_updata_tool\tools\lua\unluac.jar'
$outDir   = Join-Path $repoRoot 'xl_updata_tool\runtimes\java'

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

Write-Host '==> [prepare-java] 定位 JDK...' -ForegroundColor Cyan

# --- 定位 JDK：优先 -JdkHome，其次 JAVA_HOME，最后 PATH ---
$candidates = @()
if ($JdkHome)        { $candidates += $JdkHome }
if ($env:JAVA_HOME)  { $candidates += $env:JAVA_HOME }
$jlinkOnPath = Get-Command jlink.exe -ErrorAction SilentlyContinue
if ($jlinkOnPath) {
    # PATH 中的 jlink 位于 <jdk>/bin，取其父目录
    $candidates += (Split-Path $jlinkOnPath.Source -Parent)
}

$jdk = $null
foreach ($c in $candidates) {
    if ($c -and (Test-Path (Join-Path $c 'bin\jlink.exe'))) { $jdk = (Resolve-Path $c).Path; break }
}
if (-not $jdk) {
    throw "未找到含 bin\jlink.exe 的 JDK。请安装 JDK 21+，或设置 JAVA_HOME，或用 -JdkHome 指定。"
}

$jlinkExe = Join-Path $jdk 'bin\jlink.exe'
$jdepsExe = Join-Path $jdk 'bin\jdeps.exe'
$javacExe = Join-Path $jdk 'bin\java.exe'

# --- 校验 JDK 主版本 >= 21 ---
# unluac.jar 的 class file version 为 65（Java 21），用更低版本 jlink 出的 JRE 无法运行它。
$r = Invoke-Native $javacExe @('-version')
$verText = (($r.Output).Trim() -split "`r?`n" | Select-Object -First 1)
Write-Host "    JDK: $jdk ($verText)"
if ($verText -notmatch '"?(\d+)') { throw "无法解析 java 版本号: $verText" }
if ([int]$Matches[1] -lt 21) {
    throw "JDK 版本过低（需要 >= 21，unluac.jar 按 Java 21 编译）: $verText"
}

if (-not (Test-Path $jarPath)) { throw "找不到 unluac.jar: $jarPath" }

# --- jdeps 推导模块依赖；失败或为空时回退 java.base ---
Write-Host '==> [prepare-java] jdeps 分析 unluac.jar 模块依赖...'
$modules = ''
$r = Invoke-Native $jdepsExe @('--ignore-missing-deps', '--print-module-deps', $jarPath)
if ($r.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($r.Output)) {
    $modules = (($r.Output).Trim() -split "`r?`n" | Select-Object -Last 1).Trim()
} else {
    Write-Warning "jdeps 执行失败（退出码 $($r.ExitCode)），回退 java.base。`n$($r.Output)"
}
if ([string]::IsNullOrWhiteSpace($modules)) {
    $modules = 'java.base'
    Write-Warning 'jdeps 未输出模块列表，回退 java.base。'
}
Write-Host "    模块: $modules"

# --- jlink 生成运行时 ---
if (Test-Path $outDir) {
    Write-Host "==> [prepare-java] 清理旧运行时: $outDir"
    Remove-Item $outDir -Recurse -Force
}
New-Item -ItemType Directory -Path (Split-Path $outDir -Parent) -Force | Out-Null

Write-Host "==> [prepare-java] jlink 输出到 $outDir"
$r = Invoke-Native $jlinkExe @('--add-modules', $modules, '--strip-debug', '--no-man-pages',
    '--no-header-files', '--compress=2', '--output', $outDir)
if ($r.ExitCode -ne 0) { throw "jlink 失败，退出码 $($r.ExitCode)`n$($r.Output)" }

# --- 验证 ---
$javaExe = Join-Path $outDir 'bin\java.exe'
if (-not (Test-Path $javaExe)) { throw "jlink 输出缺少 bin\java.exe: $outDir" }
$r = Invoke-Native $javaExe @('-version')
if ($r.ExitCode -ne 0) { throw "java.exe -version 验证失败，退出码 $($r.ExitCode)`n$($r.Output)" }
$javaVer = (($r.Output).Trim() -split "`r?`n" | Select-Object -First 1)
Write-Host "    运行时验证通过: $javaVer"

# 冒烟验证生成的 JRE 能加载 unluac.jar（不带参数应打印用法；出现 LinkageError 即为版本不兼容）
$r = Invoke-Native $javaExe @('-jar', $jarPath)
if ($r.Output -match 'UnsupportedClassVersionError|LinkageError') {
    throw "生成的 JRE 无法运行 unluac.jar（$($Matches[0])）。请用 JDK 21+ 重新运行本脚本。`n$($r.Output)"
}
Write-Host '    unluac.jar 加载冒烟通过'

$sizeMB = [math]::Round(((Get-ChildItem $outDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "==> [prepare-java] 完成，私有 JRE 体积 ${sizeMB} MB" -ForegroundColor Green

