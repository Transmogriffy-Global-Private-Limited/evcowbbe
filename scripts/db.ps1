# Unified Django schema workflow. Run from repository root on PowerShell 7+.
# Example: .\scripts\db.ps1 generate --name customer_history
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Install the project Python venv at .venv before using scripts/db.ps1'
}
Push-Location -LiteralPath $repo
try {
    & $python -B (Join-Path $repo 'scripts\db.py') @args
    if ($LASTEXITCODE -ne 0) { throw "Database workflow failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }
