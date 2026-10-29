param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 scripts/dev.py @Args
  } else {
    & python scripts/dev.py @Args
  }
  exit $LASTEXITCODE
} finally { Pop-Location }
