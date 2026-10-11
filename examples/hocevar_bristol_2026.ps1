# Run from PowerShell: .\examples\hocevar_bristol_2026.ps1
# Pass Python script options, for example: --race spring --show
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Get-Command python -ErrorAction SilentlyContinue
$taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ($taskPython) {
    $taskPythonPath = $taskPython.Source
} elseif (Test-Path -LiteralPath $taskBundledPython) {
    $taskPythonPath = $taskBundledPython
} else {
    throw 'Python is required. Install Python 3.10 or newer and the package with its plot extra.'
}
$taskOldPythonPath = $env:PYTHONPATH
$taskOldMplConfig = $env:MPLCONFIGDIR
try {
    $taskPlotDeps = Join-Path $taskRoot '.plot-deps'
    if (Test-Path -LiteralPath $taskPlotDeps) {
        $env:PYTHONPATH = "$taskPlotDeps;$taskOldPythonPath"
    }
    $env:MPLCONFIGDIR = Join-Path $taskRoot '.matplotlib'
    & $taskPythonPath (Join-Path $PSScriptRoot 'hocevar_bristol_2026.py') @args
    if ($LASTEXITCODE -ne 0) { throw "Chart script failed with exit code $LASTEXITCODE" }
} finally {
    $env:PYTHONPATH = $taskOldPythonPath
    $env:MPLCONFIGDIR = $taskOldMplConfig
}
