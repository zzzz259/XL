$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$workspacePython = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) {
    & $localPython -B desktop.py @args
} elseif (Test-Path -LiteralPath $workspacePython) {
    & $workspacePython -B desktop.py @args
} else {
    throw '未找到项目 Python。请先在本目录创建 Python 3.11+ 的 .venv。无需安装第三方依赖。'
}
