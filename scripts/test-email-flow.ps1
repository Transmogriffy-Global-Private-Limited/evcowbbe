# Real SMTP is used. Registration sends a verification link/token to the address entered below.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:18080'
$email = Read-Host 'Your REAL test email (must be able to receive mail)'
$secure = Read-Host 'Password (12+ characters)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  $json = @{email=$email;password=$password} | ConvertTo-Json -Compress
  Invoke-RestMethod -Uri "$base/api/v1/app/auth/register" -Method Post -ContentType 'application/json' -Body $json
} finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); Remove-Variable password -ErrorAction SilentlyContinue }
Write-Host 'Check the actual email inbox (including spam folder) for a verification token.'
$token = Read-Host 'Paste the email verification token'
Invoke-RestMethod -Uri "$base/api/v1/auth/verify-email" -Method Post -ContentType 'application/json' -Body (@{token=$token} | ConvertTo-Json -Compress)
Write-Host 'Email verified. Log in at /api/v1/app/auth/login.'
