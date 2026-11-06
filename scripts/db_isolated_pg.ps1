# Consolidated-schema acceptance against a NEW LOCAL temporary PostgreSQL 17 cluster.
# Does NOT modify/register/stop the existing Windows PostgreSQL service.
# Run in PowerShell 7+ from the evcowbbe repository root.
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'
$role = 'cow_s6_accept_owner'
$dbname = 'cow_s6_acceptance_test'
$expectedBranch = 'anubhab-work'
$expectedBase = '6aced157df0cc26bcf8b289a5184c6a400d6028d'
$runRoot = $null
$dataDir = $null
$passwordFile = $null
$pgCtl = $null
$clusterCreated = $false
$clusterShutdownConfirmed = $false
$acceptanceChecksPassed = $false

# Capture ALL inherited PostgreSQL-prefixed process settings, not a hand-picked
# subset. PostgreSQL server startup honors e.g. PGPORT, PGCLIENTENCODING,
# PGDATESTYLE, PGGEQO and PGTZ; empty values are not necessarily harmless.
$pgEnvNames = @(
    [Environment]::GetEnvironmentVariables('Process').Keys |
        ForEach-Object { [string]$_ } |
        Where-Object { $_.StartsWith('PG', [StringComparison]::OrdinalIgnoreCase) }
)
$envNames = @(
    'DJANGO_SETTINGS_MODULE', 'APP_ENV', 'PASSWORD_PEPPER', 'OTP_HMAC_PEPPER', 'DATABASE_URL',
    'PUBLIC_BASE_URL', 'HTTP_LISTEN_ADDR', 'SMTP_TLS_MODE',
    'PGPORT', 'PGCLIENTENCODING', 'PGPASSWORD', 'COW_PG_TEST_HARNESS_READY'
) + $pgEnvNames
$envNames = @($envNames | Select-Object -Unique)
# Capture membership AND value. On Windows, missing and empty variables
# can be represented differently across .NET/PowerShell environment APIs.
$initialProcessEnvironment = [Environment]::GetEnvironmentVariables('Process')
$oldEnv = @{}
$oldEnvPresent = @{}
foreach ($key in $envNames) {
    $present = $initialProcessEnvironment.Contains($key)
    $oldEnvPresent[$key] = $present
    $oldEnv[$key] = if ($present) { [string]$initialProcessEnvironment[$key] } else { $null }
}

function Assert-NativeSuccess([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

try {
    $RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
    Push-Location -LiteralPath $RepositoryRoot
    try {
        if (-not (Test-Path -LiteralPath 'manage.py' -PathType Leaf)) {
            throw 'Run from the evcowbbe repository root.'
        }
        $branch = (& git branch --show-current).Trim()
        Assert-NativeSuccess 'git branch'
        $head = (& git rev-parse HEAD).Trim()
        Assert-NativeSuccess 'git rev-parse'
        if ($branch -notin @($expectedBranch, 'main')) {
            throw "Unexpected working branch: $branch"
        }
        & git merge-base --is-ancestor $expectedBase HEAD
        Assert-NativeSuccess 'reviewed baseline ancestry'
        Write-Host 'REVIEWED_BASELINE_ANCESTRY=PASS'
        foreach ($path in @(
            'cow/migrations/0008_zone_commercial_registry.py',
            'cow/models.py',
            'tests/integration/test_zone_registry_pg.py'
        )) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                throw "Sprint 6 overlay missing: $path"
            }
        }
        if (Test-Path -LiteralPath 'cow/models_zone.py') { throw 'Split model registry must be removed' }
        $python = if (Test-Path -LiteralPath '.\.venv\Scripts\python.exe') {
            (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
        } else {
            $p = Get-Command python -ErrorAction Stop
            $p.Source
        }
        # Avoid creating any database resources when local test dependencies
        # are missing. This is a non-database import check only.
        & $python -c 'import django, psycopg, pytest, pytest_django'
        Assert-NativeSuccess 'Python Django/PostgreSQL test dependencies'
        $binDir = (& pg_config --bindir | Select-Object -First 1).Trim()
        Assert-NativeSuccess 'pg_config'
        $initdb = Join-Path $binDir 'initdb.exe'
        $pgCtl = Join-Path $binDir 'pg_ctl.exe'
        $psql = Join-Path $binDir 'psql.exe'
        foreach ($exe in @($initdb, $pgCtl, $psql)) {
            if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
                throw "Required PostgreSQL executable not found: $exe"
            }
        }
        $pgVersion = (& $pgCtl --version).Trim()
        Assert-NativeSuccess 'pg_ctl --version'
        if ($pgVersion -notmatch '(?i)^(?:pg_ctl\s+\(PostgreSQL\)|PostgreSQL)\s+17(?:\.\d+)?$') {
            throw "Expected PostgreSQL 17, found: $pgVersion"
        }

        # Obtain an unoccupied ephemeral TCP port. PostgreSQL still fails safely
        # if the port is claimed between the probe and the subsequent start.
        $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
        try {
            $probe.Start()
            $port = ([Net.IPEndPoint]$probe.LocalEndpoint).Port
        } finally {
            $probe.Stop()
        }
        if ($port -lt 1024 -or $port -eq 5432) {
            throw "Unacceptable isolated test port: $port"
        }

        # Isolate ALL inherited PG* settings before initdb OR pg_ctl. Set only
        # validated defaults on this process; restore the originals in finally.
        # Never edit machine/user environment or the running Windows PG service.
        $oldPortState = if ($null -eq $oldEnv['PGPORT']) {
            'UNSET'
        } elseif ($oldEnv['PGPORT'].Length -eq 0) {
            'EMPTY'
        } else {
            'SET_NONEMPTY'
        }
        $oldEncodingState = if ($null -eq $oldEnv['PGCLIENTENCODING']) {
            'UNSET'
        } elseif ($oldEnv['PGCLIENTENCODING'].Length -eq 0) {
            'EMPTY'
        } else {
            'SET_NONEMPTY'
        }
        foreach ($name in $pgEnvNames) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        $leaked = @(
            [Environment]::GetEnvironmentVariables('Process').Keys |
                ForEach-Object { [string]$_ } |
                Where-Object { $_.StartsWith('PG', [StringComparison]::OrdinalIgnoreCase) }
        )
        if ($leaked.Count -ne 0) {
            throw 'Unable to isolate inherited PostgreSQL process environment'
        }
        $env:PGPORT = [string]$port
        $env:PGCLIENTENCODING = 'UTF8'
        if ($env:PGPORT -ne [string]$port -or $env:PGCLIENTENCODING -ne 'UTF8') {
            throw 'Isolated PostgreSQL process defaults did not take effect'
        }
        Write-Host "INHERITED_PGPORT_STATE=$oldPortState (value withheld)"
        Write-Host "INHERITED_PGCLIENTENCODING_STATE=$oldEncodingState (value withheld)"
        Write-Host "SANITIZED_PG_ENVIRONMENT=PASS ($($pgEnvNames.Count) inherited PG* keys)"

        $runRoot = Join-Path $env:TEMP ('evcowbbe-s6-pg17-' + [guid]::NewGuid().ToString('N'))
        [void](New-Item -Path $runRoot -ItemType Directory -ErrorAction Stop)
        $dataDir = Join-Path $runRoot 'cluster'
        $passwordFile = Join-Path $runRoot 'initdb-password.txt'
        $logPath = Join-Path $runRoot 'postgres.log'
        $password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
        [IO.File]::WriteAllText($passwordFile, $password + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false))
        Write-Host "ISOLATED_RUN_DIR=$runRoot"
        Write-Host "ISOLATED_PORT=$port (existing service unchanged)"
        Write-Host "POSTGRESQL_VERSION=$pgVersion"

        # -c port=... is an official PG17 initdb option. Set it for BOTH
        # catalog bootstrap and generated postgresql.conf (no implicit 5432).
        & $initdb -D $dataDir -U $role --auth-host=scram-sha-256 --auth-local=scram-sha-256 --pwfile=$passwordFile --encoding=UTF8 -c "port=$port" -c 'listen_addresses=127.0.0.1'
        Assert-NativeSuccess 'initdb'
        $clusterCreated = $true
        # This uses ONLY the fresh cluster directory. No service registration.
        & $pgCtl -D $dataDir -l $logPath -o "-h 127.0.0.1 -p $port -c max_connections=30 -c client_encoding=UTF8" -w -t 60 start
        if ($LASTEXITCODE -ne 0) {
            $startExit = $LASTEXITCODE
            Write-Host "ISOLATED_POSTGRES_START_FAILED_EXIT=$startExit"
            if (Test-Path -LiteralPath $logPath -PathType Leaf) {
                Write-Host '=== ISOLATED SERVER STARTUP LOG (LAST 80 LINES) ==='
                Get-Content -LiteralPath $logPath -Tail 80
            } else {
                Write-Host 'ISOLATED_SERVER_LOG_MISSING'
            }
            throw "isolated pg_ctl start failed (exit code $startExit)"
        }

        # The PG* environment was isolated BEFORE initdb. Keep PGPORT and
        # PGCLIENTENCODING explicit for pg_ctl and libpq.
        $env:PGPASSWORD = $password
        # Positively verify that the server is *our* new cluster before DDL.
        $observedDir = (& $psql -X -w -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$port" -U $role -d postgres -c 'SHOW data_directory;')
        Assert-NativeSuccess 'isolated server identity query'
        $observedDir = ($observedDir | Select-Object -First 1).Trim()
        $expectedPath = [IO.Path]::GetFullPath($dataDir).TrimEnd('\', '/')
        $observedPath = [IO.Path]::GetFullPath($observedDir).TrimEnd('\', '/')
        if (-not [string]::Equals($expectedPath, $observedPath,
            [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing DDL: connected server does not use the fresh isolated data directory"
        }
        Write-Host 'ISOLATED_SERVER_IDENTITY=PASS'
        # Enable the guarded integration fixture only for this positively
        # identified, disposable PostgreSQL 17 cluster.
        $env:COW_PG_TEST_HARNESS_READY = '1'

        & $psql -X -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$port" -U $role -d postgres -c "CREATE DATABASE $dbname;"
        Assert-NativeSuccess 'isolated CREATE DATABASE'
        $env:APP_ENV = 'test'
        $env:PASSWORD_PEPPER = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
        $env:OTP_HMAC_PEPPER = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
        $env:DATABASE_URL = "postgresql://${role}:${password}@127.0.0.1:$port/${dbname}?sslmode=disable"
        $env:PUBLIC_BASE_URL = 'http://127.0.0.1:18280'
        $env:HTTP_LISTEN_ADDR = '127.0.0.1:18280'
        $env:SMTP_TLS_MODE = 'starttls'
        # manage.py uses os.environ.setdefault(), which does not replace an
        # inherited empty DJANGO_SETTINGS_MODULE. Set the value explicitly for
        # Django management commands and pytest, then restore it in finally.
        $env:DJANGO_SETTINGS_MODULE = 'config.settings'
        if ($env:DJANGO_SETTINGS_MODULE -ne 'config.settings') {
            throw 'Django settings module was not established in the isolated child'
        }
        # Secret file no longer needed after initdb.
        Remove-Item -LiteralPath $passwordFile -Force
        $passwordFile = $null

        Write-Host "`n=== DJANGO CHECK ==="
        & $python manage.py check
        Assert-NativeSuccess 'Django check'
        Write-Host "`n=== DJANGO MODEL/MIGRATION PARITY ==="
        & $python manage.py makemigrations cow --check --dry-run --noinput
        Assert-NativeSuccess 'Django makemigrations parity'
        # Cow migrations alone do not create Django auth/contenttypes tables.
        # pytest-django flush/post_migrate require those framework tables.
        Write-Host "`n=== DJANGO FRAMEWORK MIGRATIONS ==="
        & $python manage.py migrate auth --noinput
        Assert-NativeSuccess 'Django auth/contenttypes migrations'
        Write-Host "`n=== MIGRATION 0007 BASELINE ==="
        & $python manage.py migrate cow 0007_rbac_pricing_consequential_parity --noinput
        Assert-NativeSuccess 'migration 0007 baseline'
        Write-Host "`n=== MIGRATION 0008 FORWARD ==="
        & $python manage.py migrate cow 0008_zone_commercial_registry --noinput
        Assert-NativeSuccess 'migration 0008 forward'
        Write-Host "`n=== MIGRATION 0009 ZONE ADMIN OPERATIONS ==="
        & $python manage.py migrate cow 0009_zone_admin_operations --noinput
        Assert-NativeSuccess 'migration 0009 forward'

        $catalogSQL = @'
SELECT
  (SELECT count(*) FROM django_migrations WHERE app='cow' AND name='0008_zone_commercial_registry'),
  (SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace
    AND relname IN ('cow_servicezone','cow_dutyzoneassignment','cow_chargerzoneassignment',
                    'cow_zonegstassignment','cow_zonetariffpayload') AND relkind='r'),
  (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN
    ('cow_zone_identity_guard','cow_duty_zone_guard','cow_charger_zone_guard',
     'cow_zone_gst_guard','cow_zone_tariff_payload_guard')),
  (SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN
    ('cow_zone_identity_guard','cow_zone_assignment_guard','cow_zone_gst_guard',
     'cow_zone_tariff_payload_guard'));
'@
        $catalog = (& $psql -X -w -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$port" -U $role -d $dbname -c $catalogSQL)
        Assert-NativeSuccess '0008 catalog query'
        $counts = ($catalog | Select-Object -First 1).Trim()
        Write-Host "CATALOG_COUNTS(migration|tables|triggers|functions)=$counts"
        if ($counts -ne '1|5|5|4') { throw '0008 catalog shape did not match expected five models, five triggers and four functions' }

        # Fail earlier and more clearly if the framework ledger is missing.
        $frameworkSQL = "SELECT CASE WHEN to_regclass('public.django_content_type') IS NOT NULL AND to_regclass('public.auth_user') IS NOT NULL THEN 'OK' ELSE 'MISSING' END"
        $frameworkCheck = (& $psql -X -w -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$port" -U $role -d $dbname -c $frameworkSQL)
        Assert-NativeSuccess 'Django framework catalog check'
        if (($frameworkCheck | Select-Object -First 1).Trim() -ne 'OK') { throw 'Django framework tables are missing before pytest' }
        Write-Host 'DJANGO_FRAMEWORK_CATALOG=PASS'
        Write-Host "`n=== POSTGRESQL INTEGRATION TESTS ==="
        & $python -m pytest tests/integration -q
        Assert-NativeSuccess 'Full PostgreSQL integration acceptance including zone registry'
        Write-Host "`n=== MIGRATION 0008 REVERSE / REAPPLY ON DISPOSABLE DB ==="
        & $python manage.py migrate cow 0007_rbac_pricing_consequential_parity --noinput
        Assert-NativeSuccess 'migration 0008 reverse'
        & $python manage.py migrate cow 0009_zone_admin_operations --noinput
        Assert-NativeSuccess 'migration 0009 reapply'
        $after = (& $psql -X -w -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$port" -U $role -d $dbname -c $catalogSQL)
        Assert-NativeSuccess '0008 replay catalog query'
        if (($after | Select-Object -First 1).Trim() -ne '1|5|5|4') {
            throw '0008 replay catalog mismatch'
        }
        Write-Host 'MIGRATION_REVERSE_REAPPLY=PASS'
        Write-Host "`n=== SOURCE WORKTREE SAFETY ==="
        & git diff --check
        Assert-NativeSuccess 'git diff --check'
        & git status --short
        Assert-NativeSuccess 'git status'
        $acceptanceChecksPassed = $true
        Write-Host "`nISOLATED_PG17_SCHEMA_WORKFLOW_TESTS=PASS"
    } finally {
        Pop-Location
    }
}
finally {
    # Only inspect/stop the uniquely created cluster. Never Stop-Service,
    # never target a service-owned data directory, never drop a real DB.
    if ($clusterCreated -and $null -ne $pgCtl -and $null -ne $dataDir -and
        (Test-Path -LiteralPath (Join-Path $dataDir 'PG_VERSION'))) {
        try {
            & $pgCtl -D $dataDir status *> $null
            $statusExit = $LASTEXITCODE
            if ($statusExit -eq 0) {
                & $pgCtl -D $dataDir -m fast -w -t 30 stop
                if ($LASTEXITCODE -eq 0) {
                    $clusterShutdownConfirmed = $true
                    Write-Host 'ISOLATED_CLUSTER_STOPPED=PASS'
                } else {
                    Write-Warning "Isolated cluster shutdown requires review: $dataDir"
                }
            } elseif ($statusExit -eq 3) {
                # pg_ctl status exit 3 means this cluster is not running.
                $clusterShutdownConfirmed = $true
                Write-Host 'ISOLATED_CLUSTER_NOT_RUNNING=PASS'
            } else {
                Write-Warning "Isolated cluster status could not be verified: $dataDir"
            }
        } catch {
            Write-Warning "Isolated cluster cleanup needs review: $($_.Exception.Message)"
        }
    }
    if ($passwordFile -and (Test-Path -LiteralPath $passwordFile)) {
        Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue
    }
    # Restore exactly the original environment key membership and values,
    # without comparing absent values against empty-string representations.
    foreach ($key in $envNames) {
        if ($oldEnvPresent[$key]) {
            [Environment]::SetEnvironmentVariable($key, [string]$oldEnv[$key], 'Process')
        } else {
            [Environment]::SetEnvironmentVariable($key, $null, 'Process')
        }
    }
    $restoredEnvironment = [Environment]::GetEnvironmentVariables('Process')
    $restoreDifferences = 0
    foreach ($key in $envNames) {
        $present = $restoredEnvironment.Contains($key)
        if ($present -ne [bool]$oldEnvPresent[$key]) {
            $restoreDifferences++
        } elseif ($present -and [string]$restoredEnvironment[$key] -cne [string]$oldEnv[$key]) {
            $restoreDifferences++
        }
    }
    if ($restoreDifferences -eq 0) {
        Write-Host 'PROCESS_ENVIRONMENT_RESTORED=YES'
    } else {
        Write-Warning "Process environment restoration mismatch for $restoreDifferences variables (values withheld)"
    }
    if ($runRoot) { Write-Host "ISOLATED_EVIDENCE_DIR=$runRoot" }
}
if ($acceptanceChecksPassed) {
    if (-not $clusterShutdownConfirmed) {
        throw 'Acceptance tests passed but isolated PostgreSQL shutdown was not confirmed'
    }
    Write-Host 'ISOLATED_PG17_SCHEMA_WORKFLOW_ACCEPTANCE=PASS'
}
