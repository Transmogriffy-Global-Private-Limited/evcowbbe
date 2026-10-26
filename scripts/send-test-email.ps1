# Optional explicit end-to-end email delivery test. Sends ONE real email.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$recipient = Read-Host 'Actual email inbox to receive a ONE-TIME test email'
if ([string]::IsNullOrWhiteSpace($recipient)) { throw 'Recipient required. No message sent.' }
Write-Host 'This WILL send one real security test email.'
$confirm = Read-Host 'Type SEND to continue'
if ($confirm -cne 'SEND') { throw 'Canceled. No email sent.' }
& go run ./cmd/send-test-email -to $recipient
if ($LASTEXITCODE -ne 0) { throw 'SMTP server did not accept the test message.' }
Write-Host 'Check the recipient inbox and spam folder for actual delivery.'
