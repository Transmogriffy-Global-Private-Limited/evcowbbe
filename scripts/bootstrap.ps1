# Legacy entrypoint from v1: configuration stage ONLY.
# Never starts containers, migrates a database, or starts the server.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'configure.ps1')
