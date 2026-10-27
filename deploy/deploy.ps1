<#
Send the committed code to the server and (re)install it.

    .\deploy\deploy.ps1                 # code only
    .\deploy\deploy.ps1 -WithEnv        # also copy your local .env (first time, or after changing it)

The server address is read from $env:CRYPTOBOT_SERVER (set it once, see docs/deploy-oracle.md),
so it never ends up in the repository. Only committed files are sent (git archive).
#>
param(
    [string]$Server = $env:CRYPTOBOT_SERVER,
    [string]$Key = "$HOME\.ssh\oracle_cryptobot",
    [switch]$WithEnv
)
$ErrorActionPreference = "Stop"
if (-not $Server) { throw "Set `$env:CRYPTOBOT_SERVER to the server's public IP first." }

$root = Split-Path $PSScriptRoot -Parent
$archive = Join-Path $env:TEMP "cryptobot.tar.gz"
git -C $root archive --format=tar.gz -o $archive HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

$files = @($archive)
if ($WithEnv) { $files += (Join-Path $root ".env") }
scp -i $Key @files "ubuntu@${Server}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw "copy failed" }

# setup.sh is taken from the archive, so the server always runs the committed version.
ssh -i $Key "ubuntu@$Server" ("chmod 600 /tmp/.env 2>/dev/null; " +
    "tar -xzf /tmp/cryptobot.tar.gz -O deploy/setup.sh > /tmp/setup.sh && " +
    "sudo bash /tmp/setup.sh /tmp/cryptobot.tar.gz; rm -f /tmp/cryptobot.tar.gz /tmp/setup.sh /tmp/.env")
Remove-Item $archive
