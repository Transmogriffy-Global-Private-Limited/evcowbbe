$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$email = Read-Host 'New admin email'
$secure = Read-Host 'New admin password (12+ characters)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $password | & go run ./cmd/bootstrap-admin -email $email
    if ($LASTEXITCODE -ne 0) { throw 'Admin bootstrap failed.' }
}
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); Remove-Variable password -ErrorAction SilentlyContinue }
