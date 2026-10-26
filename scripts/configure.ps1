# Stage 1: configure EXISTING PostgreSQL and SMTP. No network calls or database changes.
# Go, Docker, PostgreSQL CLI and mail testing tools are NOT needed for this stage.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$envPath = Join-Path $root '.env'
$examplePath = Join-Path $root '.env.example'

function Read-Field([string] $Label, [string] $Default) {
    $shown = if ($Default -eq '') { '' } else { " [$Default]" }
    $answer = Read-Host "$Label$shown"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}
function Read-Secret([string] $Label) {
    $secure = Read-Host $Label -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
function Generate-Secret {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}
function Require-SingleLine([string] $Name, [string] $Value) {
    if ($Value.Contains("`n") -or $Value.Contains("`r") -or $Value.Contains([char]0)) {
        throw "$Name contains an invalid newline/NUL character."
    }
}
function Render-DotEnv([string[]] $Lines, [System.Collections.Specialized.OrderedDictionary] $Replacements) {
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $output = [Collections.Generic.List[string]]::new()
    foreach ($line in $Lines) {
        if ($line.Trim() -eq '' -or $line.TrimStart().StartsWith('#')) {
            $output.Add($line)
            continue
        }
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw 'Malformed .env line. No changes written.' }
        $key = $Matches[1]
        if (-not $seen.Add($key)) { throw "Duplicate .env field: $key" }
        if ($key -eq 'POSTGRES_PASSWORD') { continue } # obsolete Docker-only variable
        if ($Replacements.Contains($key)) { $output.Add("$key=$($Replacements[$key])") }
        else { $output.Add($line) }
    }
    foreach ($key in $Replacements.Keys) {
        if (-not $seen.Contains([string]$key)) { $output.Add("$key=$($Replacements[$key])") }
    }
    return ($output -join "`n") + "`n"
}

if (Test-Path -LiteralPath $envPath -PathType Leaf) {
    $originalLines = @(Get-Content -LiteralPath $envPath)
    Write-Host 'Existing .env found. Its peppers and unrelated settings will be preserved.'
} else {
    $originalLines = @(Get-Content -LiteralPath $examplePath)
    Write-Host 'Creating a new .env from .env.example.'
}

$old = @{}
foreach ($line in $originalLines) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
        if ($old.ContainsKey($Matches[1])) { throw "Duplicate .env key $($Matches[1]). Fix manually before continuing." }
        $old[$Matches[1]] = $Matches[2]
    }
}

Write-Host ''
Write-Host '=== EXISTING PostgreSQL DATABASE ==='
Write-Host 'Supply an EXISTING database and user. No database will be created.'
$dbHost = Read-Field 'PostgreSQL hostname or IP' '127.0.0.1'
$dbPort = Read-Field 'PostgreSQL port' '5432'
$dbName = Read-Field 'Database name' 'cow_auth'
$dbUser = Read-Field 'Database username' 'cow_auth'
if ($dbPort -notmatch '^\d{1,5}$' -or [int]$dbPort -lt 1 -or [int]$dbPort -gt 65535) { throw 'Invalid DB port.' }
if ($dbHost -notmatch '^[A-Za-z0-9.\-:]+$' -or $dbHost -eq '') { throw 'Invalid DB host.' }
if ($dbName -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'Invalid DB name.' }
if ($dbUser -notmatch '^[A-Za-z_][A-Za-z0-9_-]*$') { throw 'Invalid DB username.' }
$sslDefault = if ($dbHost -in @('127.0.0.1', 'localhost', '::1')) { 'disable' } else { 'verify-full' }
$dbSSL = (Read-Field 'PostgreSQL sslmode (verify-full/verify-ca/require/disable)' $sslDefault).ToLowerInvariant()
if ($dbSSL -notin @('verify-full','verify-ca','require','disable')) { throw 'Unsupported DB TLS setting.' }
if ($dbSSL -eq 'disable' -and $dbHost -notin @('127.0.0.1', 'localhost', '::1')) { throw 'Remote PostgreSQL must use TLS.' }
$dbPassword = Read-Secret 'PostgreSQL password (input hidden)'
if ([string]::IsNullOrEmpty($dbPassword)) { throw 'Database password is required by this setup wizard.' }
$uriHost = if ($dbHost.Contains(':')) { "[$dbHost]" } else { $dbHost }
$escapedUser = [Uri]::EscapeDataString($dbUser)
$escapedPassword = [Uri]::EscapeDataString($dbPassword)
$dbURL = "postgres://${escapedUser}:${escapedPassword}@${uriHost}:${dbPort}/${dbName}?sslmode=${dbSSL}"
Remove-Variable dbPassword, escapedPassword -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '=== REAL SMTP MAIL SERVICE ==='
$smtpHost = Read-Field 'SMTP host' ''
$smtpPort = Read-Field 'SMTP port' '587'
$smtpMode = (Read-Field 'SMTP TLS mode (starttls/tls/none)' 'starttls').ToLowerInvariant()
$smtpUser = Read-Field 'SMTP username (Enter if no SMTP AUTH)' ''
$smtpPassword = ''
if ($smtpUser -ne '') { $smtpPassword = Read-Secret 'SMTP password (input hidden)' }
$fromAddress = Read-Field 'Verified sender email address' ''
$fromName = Read-Field 'Sender display name' 'Charger on Wheels'
if ($smtpHost -notmatch '^[A-Za-z0-9.\-:]+$' -or $smtpHost -eq '') { throw 'Invalid SMTP hostname.' }
if ($smtpPort -notmatch '^\d{1,5}$' -or [int]$smtpPort -lt 1 -or [int]$smtpPort -gt 65535) { throw 'Invalid SMTP port.' }
if ($smtpMode -notin @('starttls','tls','none')) { throw 'Invalid SMTP TLS mode.' }
if ($smtpMode -eq 'none' -and $smtpHost -notin @('127.0.0.1','localhost','::1')) { throw 'SMTP encryption required for remote hosts.' }
if ($smtpUser -ne '' -and $smtpPassword -eq '') { throw 'SMTP password required when username is set.' }
if ($smtpMode -eq 'none' -and $smtpUser -ne '') { throw 'SMTP AUTH cannot be used without TLS.' }
try {
    $parsedFrom = [System.Net.Mail.MailAddress]::new($fromAddress)
    if ($parsedFrom.Address -cne $fromAddress) { throw 'Invalid sender address.' }
} catch { throw 'MAIL_FROM_ADDRESS must be a valid email address.' }

$replacements = [Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal)
$replacements['DATABASE_URL'] = $dbURL
$replacements['MAIL_PROVIDER'] = 'SMTP'
$replacements['SMTP_HOST'] = $smtpHost
$replacements['SMTP_PORT'] = $smtpPort
$replacements['SMTP_TLS_MODE'] = $smtpMode
$replacements['SMTP_USERNAME'] = $smtpUser
$replacements['SMTP_PASSWORD'] = $smtpPassword
$replacements['MAIL_FROM_ADDRESS'] = $fromAddress
$replacements['MAIL_FROM_NAME'] = $fromName
foreach ($key in @('PASSWORD_PEPPER', 'OTP_HMAC_PEPPER')) {
    $existing = if ($old.ContainsKey($key)) { [string]$old[$key] } else { '' }
    if ($existing.Length -ge 32 -and $existing -ne 'REPLACE_WITH_GENERATED_SECRET') {
        $replacements[$key] = $existing
    } else {
        $replacements[$key] = Generate-Secret
    }
}
if ($replacements['PASSWORD_PEPPER'] -ceq $replacements['OTP_HMAC_PEPPER']) {
    throw 'Password and OTP peppers cannot be identical.'
}
foreach ($key in $replacements.Keys) { Require-SingleLine ([string]$key) ([string]$replacements[$key]) }

# Print non-sensitive destination only; never print DATABASE_URL, passwords or peppers.
Write-Host ''
Write-Host "Database destination: ${dbHost}:${dbPort}/${dbName} ($dbSSL)"
Write-Host "SMTP destination: ${smtpHost}:${smtpPort} ($smtpMode)"
Write-Host "Email sender: $fromAddress"
$accept = Read-Host 'Write these settings to .env? Type YES'
if ($accept -cne 'YES') { throw 'Canceled; .env left unchanged.' }

$updated = Render-DotEnv -Lines $originalLines -Replacements $replacements
$backupPath = $null
if (Test-Path -LiteralPath $envPath -PathType Leaf) {
    $backupPath = Join-Path $root ('.env.backup-' + [Guid]::NewGuid().ToString('N'))
    [IO.File]::Copy($envPath, $backupPath, $false)
}
$temp = Join-Path $root ('.env.tmp-' + [Guid]::NewGuid().ToString('N'))
try {
    [IO.File]::WriteAllText($temp, $updated, [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temp, $envPath, $true)
} finally {
    if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
}
Write-Host 'CONFIGURATION_SAVED=YES (credentials not displayed)'
if ($backupPath) { Write-Host 'Previous .env preserved in a private, gitignored .env.backup-* file.' }
Write-Host 'NEXT: pwsh -NoProfile -File .\scripts\continue-setup.ps1'
