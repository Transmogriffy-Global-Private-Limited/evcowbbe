# Stage 2: run test suite, read-only checks against existing services,
# optional isolated integration tests, and EXPLICITLY approved migration.
# Never installs or starts PostgreSQL, mail emulators, Docker or containers.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
    throw 'Missing .env. First run: pwsh -NoProfile -File .\scripts\configure.ps1'
}
if (-not (Get-Command go -ErrorAction SilentlyContinue)) { throw 'Go 1.25+ required on PATH.' }
$goVersion = (& go version | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot invoke Go toolchain.' }
$versionMatch = [regex]::Match($goVersion, 'go(?<major>\d+)\.(?<minor>\d+)')
if (-not $versionMatch.Success) { throw 'Cannot determine Go version.' }
$major = [int]$versionMatch.Groups['major'].Value
$minor = [int]$versionMatch.Groups['minor'].Value
if ($major -lt 1 -or ($major -eq 1 -and $minor -lt 25)) { throw "Go 1.25+ required. Found: $goVersion" }

Write-Host '=== CHECK LOCAL CONFIGURATION ==='
$lines = @(Get-Content -LiteralPath '.env')
$settings = @{}
foreach ($line in $lines) {
    if ($line.Trim() -eq '' -or $line.TrimStart().StartsWith('#')) { continue }
    if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw 'Malformed .env. Aborting.' }
    $key = $Matches[1]
    if ($settings.ContainsKey($key)) { throw "Duplicate .env key: $key" }
    $settings[$key] = $Matches[2]
}
foreach ($key in @('DATABASE_URL','SMTP_HOST','MAIL_FROM_ADDRESS','PASSWORD_PEPPER','OTP_HMAC_PEPPER')) {
    if (-not $settings.ContainsKey($key) -or
        [string]::IsNullOrWhiteSpace([string]$settings[$key]) -or
        ([string]$settings[$key]).Contains('REPLACE_WITH_')) {
        throw "Missing or placeholder in .env: $key. Run scripts/configure.ps1."
    }
}
foreach ($key in @('DATABASE_URL','SMTP_HOST','SMTP_PORT','SMTP_TLS_MODE','SMTP_USERNAME','SMTP_PASSWORD',
                   'MAIL_FROM_ADDRESS','MAIL_FROM_NAME','PASSWORD_PEPPER','OTP_HMAC_PEPPER')) {
    if (Test-Path "Env:$key") { throw "Process environment overrides .env key $key. Remove that override before continuing." }
}
try {
    $uri = [Uri]::new([string]$settings['DATABASE_URL'])
    if ($uri.Scheme -notin @('postgres','postgresql') -or $uri.Host -eq '' -or $uri.AbsolutePath -eq '/') {
        throw 'invalid database destination'
    }
    $dbName = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
    Write-Host "PostgreSQL target: $($uri.Host):$($uri.Port)/$dbName"
} catch { throw 'Invalid DATABASE_URL; check .env configuration.' }
Write-Host "SMTP target: $($settings['SMTP_HOST']):$($settings['SMTP_PORT'])"

Write-Host '=== RESOLVE GO MODULES ==='
& go mod download
if ($LASTEXITCODE -ne 0) { throw 'Go dependency resolution failed. No migration applied.' }

# Isolated integration test opt-in. Never CREATE a database or reuse the app DB.
$testURL = [string]$env:TEST_DATABASE_URL
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
try {
    Write-Host '=== UNIT TESTS ==='
    & go test ./...
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed. Database migration NOT run.' }

    Write-Host '=== LIVE POSTGRESQL + SMTP PREFLIGHT (READ-ONLY) ==='
    & go run ./cmd/preflight
    if ($LASTEXITCODE -ne 0) { throw 'Remote connectivity failed. Database migration NOT run.' }

    if (-not [string]::IsNullOrWhiteSpace($testURL)) {
        $target = [Uri]::new($testURL)
        if ($target.Scheme -notin @('postgres', 'postgresql') -or
            -not [Uri]::UnescapeDataString($target.AbsolutePath.Trim('/')).EndsWith('_test') -or
            $target.AbsolutePath -ceq $uri.AbsolutePath -and $target.Host -ceq $uri.Host -and $target.Port -eq $uri.Port) {
            throw 'TEST_DATABASE_URL must target a separate disposable database ending _test.'
        }
        Write-Host '=== OPT-IN INTEGRATION TESTS (WRITES TO TEST DB ONLY) ==='
        $env:TEST_DATABASE_URL = $testURL
        & go test -count=1 -run '^TestIntegration' ./internal/auth -v
        if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL integration test failed. No app DB migration.' }
        Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
    } else {
        Write-Warning 'TEST_DATABASE_URL not configured; live DB integration tests skipped (not a production acceptance).'
    }

    Write-Host '=== MIGRATION APPROVAL ==='
    Write-Host "Destination: $($uri.Host):$($uri.Port)/$dbName"
    Write-Host 'This writes the auth schema to the EXISTING PostgreSQL database above.'
    $approval = Read-Host 'Type MIGRATE to apply the schema, or anything else to stop'
    if ($approval -cne 'MIGRATE') { throw 'Migration not approved; database unchanged by setup.' }
    & go run ./cmd/migrate up
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed. Inspect before re-running.' }
    Write-Host 'SETUP_COMPLETE=YES'
    Write-Host 'OPTIONAL EMAIL DELIVERY TEST: pwsh -NoProfile -File .\scripts\send-test-email.ps1'
    Write-Host 'START API: pwsh -NoProfile -File .\scripts\run.ps1'
    Write-Host 'INITIAL ADMIN: pwsh -NoProfile -File .\scripts\bootstrap-admin.ps1'
} finally {
    Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
}
