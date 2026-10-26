# Charger on Wheels authentication test gate.
# Safe default: local tests, static analysis, build. No DB writes or SMTP delivery.
# Explicit -Integration: writes to an independently provisioned *_test DB.
# Explicit -ExistingDatabase: creates test accounts in the configured app DB,
# without running migrations, touching Admin credentials, or sending email.
[CmdletBinding()]
param(
    [switch]$Integration,
    [switch]$ExistingDatabase
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    throw 'Go 1.25+ must be available on PATH.'
}
if (-not (Test-Path -LiteralPath 'go.mod') -or
    -not (Test-Path -LiteralPath 'go.sum')) {
    throw 'Expected go.mod and go.sum at project root.'
}

function Get-DbIdentity([string]$Raw) {
    try {
        $clean = $Raw.Trim().Trim('"').Trim("'")
        $uri = [Uri]::new($clean)
        $name = [Uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
        if ($uri.Scheme -notin @('postgres', 'postgresql') -or
            [string]::IsNullOrWhiteSpace($uri.Host) -or
            [string]::IsNullOrWhiteSpace($name) -or
            $name.Contains('/')) {
            throw 'invalid PostgreSQL URI'
        }
        return [pscustomobject]@{
            Host = $uri.Host.ToLowerInvariant()
            Port = $uri.Port
            Database = $name
        }
    } catch {
        throw 'Invalid PostgreSQL connection URL. Credentials were not printed.'
    }
}

function Get-ApplicationDbUrl {
    if (Test-Path 'Env:DATABASE_URL') {
        return [string]$env:DATABASE_URL
    }
    if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
        return ''
    }
    $matchesFound = @(
        Get-Content -LiteralPath '.env' |
            Where-Object { $_ -match '^\s*DATABASE_URL=' }
    )
    if ($matchesFound.Count -gt 1) {
        throw 'Multiple DATABASE_URL entries found in .env.'
    }
    if ($matchesFound.Count -eq 1) {
        return (($matchesFound[0] -split '=', 2)[1]).Trim()
    }
    return ''
}

$testUrl = [string]$env:TEST_DATABASE_URL
$appUrl = Get-ApplicationDbUrl
if ($Integration -and $ExistingDatabase) {
    throw 'Choose only one integration mode: -Integration or -ExistingDatabase.'
}
if ($Integration) {
    if ([string]::IsNullOrWhiteSpace($testUrl)) {
        throw 'Set TEST_DATABASE_URL to a separately provisioned disposable *_test database before using -Integration.'
    }
    $target = Get-DbIdentity $testUrl
    if (-not $target.Database.EndsWith('_test', [StringComparison]::Ordinal)) {
        throw 'Refusing integration writes: TEST_DATABASE_URL database name must end in _test.'
    }
    if (-not [string]::IsNullOrWhiteSpace($appUrl)) {
        $app = Get-DbIdentity $appUrl
        if ($app.Host -ceq $target.Host -and
            $app.Port -eq $target.Port -and
            $app.Database -ceq $target.Database) {
            throw 'Refusing integration writes: test database matches application database.'
        }
    }
    Write-Host "ISOLATED_TEST_DB=$($target.Host):$($target.Port)/$($target.Database)"
    Write-Host 'AUTHORIZED TEST WRITES=YES (-Integration)'
} elseif ($ExistingDatabase) {
    if ([string]::IsNullOrWhiteSpace($appUrl)) {
        throw 'No configured application DATABASE_URL found; existing-database tests refused.'
    }
    $target = Get-DbIdentity $appUrl
    Write-Host "EXISTING_APP_DB=$($target.Host):$($target.Port)/$($target.Database)"
    Write-Warning 'This creates uniquely named test customer/driver accounts, sessions, challenges, audit and rate-limit records in the EXISTING application DB.'
    Write-Warning 'The suite will not delete or update your pre-existing Admin account, change its password/2FA, run migrations, or send real mail.'
    $approval = Read-Host 'Type TEST_ONLY to authorize these limited test writes'
    if ($approval -cne 'TEST_ONLY') {
        throw 'Existing-database integration tests not authorized; no test writes performed.'
    }
    Write-Host 'AUTHORIZED EXISTING-DB TEST WRITES=YES'
} else {
    Write-Host 'SAFE_MODE=NO_DATABASE_WRITES_NO_EMAIL_DELIVERY'
}

$steps = [System.Collections.Generic.List[object]]::new()
function Invoke-GoStage([string]$Name, [string[]]$GoArgs) {
    Write-Host ""
    Write-Host "=== $Name ==="
    & go @GoArgs
    $status = [int]$LASTEXITCODE
    $script:steps.Add([pscustomobject]@{
        Stage = $Name
        Result = $(if ($status -eq 0) { 'PASS' } else { 'FAIL' })
        ExitCode = $status
    })
}

# Ensure TEST_DATABASE_URL cannot cause Go's ordinary unit test discovery to
# run DB-writing integration tests, even when the caller had it configured.
$originalTestUrl = [string]$env:TEST_DATABASE_URL
$originalAppTestUrl = [string]$env:AUTH_TEST_APP_DATABASE_URL
$originalAppTestAck = [string]$env:AUTH_TEST_EXISTING_DB_ACK
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:AUTH_TEST_APP_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:AUTH_TEST_EXISTING_DB_ACK -ErrorAction SilentlyContinue
try {
    Invoke-GoStage 'DEPENDENCY INTEGRITY' @('mod', 'verify')
    Invoke-GoStage 'UNIT AND HTTP CONTRACT TESTS' @('test', '-count=1', '-timeout=4m', './...')
    Invoke-GoStage 'STATIC ANALYSIS' @('vet', './...')
    Invoke-GoStage 'BUILD ALL PACKAGES' @('build', './...')

    $basePassed = @($steps | Where-Object Result -eq 'FAIL').Count -eq 0
    if (($Integration -or $ExistingDatabase) -and $basePassed) {
        try {
            if ($ExistingDatabase) {
                $env:AUTH_TEST_APP_DATABASE_URL = $appUrl
                $env:AUTH_TEST_EXISTING_DB_ACK = 'TEST_ONLY'
                Invoke-GoStage 'EXISTING DB TEST-ONLY ACCOUNT INTEGRATION' @(
                    'test', '-p=1', '-count=1', '-timeout=10m',
                    '-run', '^TestIntegrationHTTP', '-v', './internal/api'
                )
            } else {
                $env:TEST_DATABASE_URL = $originalTestUrl
                Invoke-GoStage 'ISOLATED POSTGRESQL HTTP INTEGRATION' @(
                    'test', '-p=1', '-count=1', '-timeout=10m',
                    '-run', '^TestIntegration', '-v', './internal/auth', './internal/api'
                )
            }
        } finally {
            Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:AUTH_TEST_APP_DATABASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:AUTH_TEST_EXISTING_DB_ACK -ErrorAction SilentlyContinue
        }
    } elseif ($Integration -or $ExistingDatabase) {
        $steps.Add([pscustomobject]@{
            Stage = 'POSTGRESQL HTTP INTEGRATION'
            Result = 'SKIP (earlier failures)'
            ExitCode = '-'
        })
    } else {
        $steps.Add([pscustomobject]@{
            Stage = 'ISOLATED POSTGRESQL HTTP INTEGRATION'
            Result = 'SKIP (not authorized)'
            ExitCode = '-'
        })
    }
} finally {
    Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:AUTH_TEST_APP_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:AUTH_TEST_EXISTING_DB_ACK -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrEmpty($originalTestUrl)) {
        $env:TEST_DATABASE_URL = $originalTestUrl
    }
    if (-not [string]::IsNullOrEmpty($originalAppTestUrl)) {
        $env:AUTH_TEST_APP_DATABASE_URL = $originalAppTestUrl
    }
    if (-not [string]::IsNullOrEmpty($originalAppTestAck)) {
        $env:AUTH_TEST_EXISTING_DB_ACK = $originalAppTestAck
    }
}

Write-Host ""
Write-Host '=== AUTH TEST SUMMARY ==='
$steps | Format-Table -AutoSize | Out-Host
if (@($steps | Where-Object Result -eq 'FAIL').Count -gt 0) {
    throw 'AUTH_TEST_GATE=FAIL; inspect failing stage output.'
}
if ($Integration -or $ExistingDatabase) {
    if (@($steps | Where-Object Result -eq 'PASS').Count -ne 5) {
        throw 'AUTH_TEST_GATE=INCOMPLETE; integration was not executed.'
    }
    if ($ExistingDatabase) {
        Write-Host 'AUTH_TEST_GATE=PASS_EXISTING_DB_TEST_ONLY'
        Write-Host 'ADMIN_ACCOUNT_ATTRIBUTES_VERIFIED_UNCHANGED_BY_TESTS'
        Write-Host 'TEST_ACCOUNT_RECORDS_RETAINED_IN_EXISTING_APP_DB'
    } else {
        Write-Host 'AUTH_TEST_GATE=PASS_INCLUDING_ISOLATED_POSTGRESQL'
    }
} else {
    Write-Warning 'AUTH_TEST_GATE=LOCAL_PASS_ONLY; database integration remains unverified.'
}
if (-not $ExistingDatabase) {
    Write-Host 'APPLICATION_DATABASE_UNCHANGED_BY_TEST_RUNNER'
}
Write-Host 'REAL_SMTP_DELIVERY_NOT_ATTEMPTED'
