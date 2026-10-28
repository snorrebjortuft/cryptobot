<#
Everyday control of the bot on the server, from your PC.

    .\deploy\remote.ps1 status      # is it running?
    .\deploy\remote.ps1 logs        # last 50 log lines
    .\deploy\remote.ps1 decisions   # last 20 decisions
    .\deploy\remote.ps1 trades      # all paper trades
    .\deploy\remote.ps1 kill        # KILL SWITCH: stop, keep coins
    .\deploy\remote.ps1 killsell    # KILL SWITCH: stop and sell everything
    .\deploy\remote.ps1 resume      # after review: delete STOP, clear halt, start again
    .\deploy\remote.ps1 download    # copy trades.csv to data\server\ (for the tax export)
#>
param(
    [Parameter(Mandatory)][ValidateSet("status", "logs", "decisions", "trades", "kill", "killsell", "resume", "download")]
    [string]$Action,
    [string]$Server = $env:CRYPTOBOT_SERVER,
    [string]$Key = "$HOME\.ssh\oracle_cryptobot"
)
if (-not $Server) { throw "Set `$env:CRYPTOBOT_SERVER to the server's public IP first." }

if ($Action -eq "download") {
    $dir = Join-Path (Split-Path $PSScriptRoot -Parent) "data\server"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $out = Join-Path $dir "trades.csv"
    $lines = ssh -i $Key "ubuntu@$Server" "sudo cat /opt/cryptobot/state/trades.csv"
    if ($LASTEXITCODE -ne 0 -or -not $lines) { throw "no trades.csv on the server yet" }
    [System.IO.File]::WriteAllLines($out, [string[]]$lines)  # UTF-8 without BOM
    Write-Host "Saved $($lines.Count - 1) trades to $out"
    Write-Host "Tax export: python -m cryptobot.tax --year <year> --trades data\server\trades.csv"
    return
}

$py = "cd /opt/cryptobot && sudo -u cryptobot .venv/bin/python -m"
$cmd = switch ($Action) {
    "status"    { "systemctl --no-pager status cryptobot | head -n 12" }
    "logs"      { "sudo tail -n 50 /opt/cryptobot/logs/bot.log" }
    "decisions" { "sudo tail -n 20 /opt/cryptobot/logs/decisions.jsonl" }
    "trades"    { "sudo cat /opt/cryptobot/state/trades.csv 2>/dev/null || echo 'no trades yet'" }
    "kill"      { "$py cryptobot.kill" }
    "killsell"  { "$py cryptobot.kill --sell" }
    "resume"    { "sudo rm -f /opt/cryptobot/STOP && $py cryptobot.bot --resume --once && sudo systemctl restart cryptobot" }
}
ssh -i $Key "ubuntu@$Server" $cmd
