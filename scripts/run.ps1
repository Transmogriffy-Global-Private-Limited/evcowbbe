$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) { throw 'Missing .env. Run scripts/configure.ps1 first.' }
if (-not (Get-Command go -ErrorAction SilentlyContinue)) { throw 'Go is not installed or not on PATH.' }
& go run ./cmd/server
if ($LASTEXITCODE -ne 0) { throw 'Go authentication API exited with an error.' }
