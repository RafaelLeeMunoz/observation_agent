# The true cold end-to-end retest (user, 24 Nov 2026): the one command, in a clean untrusted copy, nothing cached, timed.
#
# Usage (from the working repository, after the 5-hour window has reset):
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\run_cold_retest.ps1 [-Copy C:\Users\Xigua\GitHub\observation_agent_cold2]
#
# Needs: the copy made by `.venv\Scripts\python tools\make_cold_copy.py <copy>` from the CURRENT working tree and still without `data\` or `output\`;
# ANTHROPIC_API_KEY unset; port 8765 free (the run starts its dashboard there: stop any other dashboard on 8765 first).
# Writes start/end/wall seconds and the driver's own output to eval\results\m7_cold2\ in the working repository.
param([string]$Copy = 'C:\Users\Xigua\GitHub\observation_agent_cold2', [string]$OutName = 'm7_cold2')
$Repo = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Repo "eval\results\$OutName"
New-Item -ItemType Directory -Force $Out | Out-Null
& (Join-Path $Repo '.venv\Scripts\python.exe') (Join-Path $Repo 'tools\make_cold_copy.py') --verify $Copy
if ($LASTEXITCODE -ne 0) { throw "$Copy is not an exact, cold copy of the working tree (see above): make a fresh copy with tools\make_cold_copy.py" }
if (Test-Path Env:ANTHROPIC_API_KEY) { throw 'ANTHROPIC_API_KEY is set: unset it (the Pro subscription must pay, never an API key)' }
if ((Test-Path (Join-Path $Copy 'data')) -or (Test-Path (Join-Path $Copy 'output'))) { throw "$Copy already has data\ or output\: it is not cold. Make a fresh copy." }
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { throw 'port 8765 is in use: stop that dashboard first' }
Set-Location $Copy
$t0 = Get-Date
"start_utc=" + $t0.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') | Out-File -Encoding utf8 (Join-Path $Out 'cold_command.txt')
claude -p '/observe all' 2>&1 | Out-File -Encoding utf8 (Join-Path $Out 'cold_command_stdout.txt')
$t1 = Get-Date
'end_utc=' + $t1.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') + ' wall_s=' + [math]::Round(($t1 - $t0).TotalSeconds, 1) | Out-File -Append -Encoding utf8 (Join-Path $Out 'cold_command.txt')
Get-Content (Join-Path $Out 'cold_command.txt')
