# Windows x64 构建：从项目根目录或双击 build_windows.bat 执行。
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.13', '-3.12', '-3.11', '-3')) {
            & py $version -c 'import sys,struct;assert sys.version_info >= (3,11) and struct.calcsize("P") == 8' 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            & py $version -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 虚拟环境。' }
            $created = $true
            break
        }
    }
    if (-not $created -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python -c 'import sys,struct;assert sys.version_info >= (3,11) and struct.calcsize("P") == 8'
        if ($LASTEXITCODE -ne 0) { throw '需要 64 位 Python 3.11+。' }
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 虚拟环境。' }
        $created = $true
    }
    if (-not $created) { throw '未找到 64 位 Python 3.11+；也可使用 GitHub Actions 在线构建。' }
}
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $python -c 'import sys,struct;assert sys.version_info >= (3,11) and struct.calcsize("P") == 8, "需要 64 位 Python 3.11+"'
if ($LASTEXITCODE -ne 0) { throw '需要 64 位 Python 3.11+。' }
& $python -m pip install --disable-pip-version-check 'pyinstaller>=6.12,<7'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 安装失败。' }
& $python -B scripts/build_portable.py --output output
if ($LASTEXITCODE -ne 0) { throw '构建失败；请检查上面的测试/打包日志。' }
Write-Host '构建完成：output\星落抽卡手记-本地版.zip'
