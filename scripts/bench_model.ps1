# scripts\bench_model.ps1 - BG-1a model-only benchmark wrapper (plan v2.3 section 10.3).
# D12 too-slow floor: 4 tok/s, CONFIRMED by the owner 2026-10-19 (docs\RESOURCE_BUDGET.md).
# ASCII-only on purpose (R14: PS 5.1 misreads UTF-8-no-BOM files as ANSI).
#
# Run from the project root:        .\scripts\bench_model.ps1
# Manual abort while running:       New-Item -ItemType File data\tmp\BG1A_ABORT
#   (the driver unloads the model, records status 'blocked', and exits 2)
# Ctrl+C also triggers the driver's finally-block unload; any doubt is cleared with:
#   Invoke-RestMethod -Method Post -Uri http://localhost:11434/api/generate -Body '{"model":"qwen3:8b","keep_alive":0}' -ContentType 'application/json'
#
# Safety properties (owner-approved 2026-10-19):
#   - only the approved model qwen3:8b may be benchmarked (no automatic fallback run)
#   - only the local Ollama API is contacted (egress stays off; Z3/I3/Z4)
#   - no downloads, no global settings changes, all evidence written under D:\rag-platform
#   - stop conditions: available RAM < 3 GB (normal) or < 2 GB (emergency backstop),
#     any new swap activity vs baseline, total runtime > 15 minutes, manual abort/sentinel
#   - start gate: while idle free RAM is below 3 GB, the wrapper refuses to invoke
#     the driver and the driver refuses to load the model (resource safety first)
#   - on abort: model unloaded (keep_alive=0), verdict 'blocked' unless the RAM floor
#     itself was breached, which honest-ly maps to verdict 'too-heavy' (section 10.3 step 7)

param(
    [string]$Model = 'qwen3:8b',
    [double]$TooSlowFloor = 4.0,     # D12 confirmed 2026-10-19
    [int]$RamStopGb = 3,             # normal stop threshold (owner-tightened 2026-10-19)
    [int]$RamEmergencyGb = 2,        # emergency backstop (plan section 10.3 step 6)
    [int]$MaxRuntimeMinutes = 15,    # hard cap (plan section 10.3 step 6)
    [int]$SwapEpsilonMB = 4,         # jitter margin for "any new swap activity"
    [switch]$SelfTest                # offline verification only: no benchmark, no Ollama traffic
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = 'D:\rag-platform'
Set-Location $ProjectRoot
. (Join-Path $ProjectRoot 'scripts\env.ps1')

# --- Self-test mode: verifies stop conditions, guards, and output paths only ---
# (no Ollama request, no model load, no benchmark test - see scripts\bench_model.py)
if ($SelfTest) {
    $DriverSelf = Join-Path $ProjectRoot 'scripts\bench_model.py'
    $eapS = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv run --no-sync python $DriverSelf --selftest
    $codeS = $LASTEXITCODE
    $ErrorActionPreference = $eapS
    exit $codeS
}

# --- Guardrail 1: only the approved model (owner decision 2026-10-19; D3 stays deferred) ---
if ($Model -ne 'qwen3:8b') {
    throw "Only the approved model qwen3:8b may be benchmarked. Fallback-model benchmarking requires separate owner approval."
}

# --- Guardrail 2: localhost only (egress off) - STRICT validation ---
# Exact scheme + host + port. Prefix matching is NOT accepted: a host such as
# http://localhost.attacker.example must never pass a StartsWith check.
$Base = 'http://localhost:11434'
if ($env:OLLAMA_BASE_URL) { $Base = $env:OLLAMA_BASE_URL }
$uri = $null
if (-not [System.Uri]::TryCreate($Base, [System.UriKind]::Absolute, [ref]$uri)) {
    throw "Refusing Ollama endpoint '$Base': not a valid absolute URI (Z3/I3)."
}
if ($uri.Scheme -ne 'http') {
    throw "Refusing Ollama endpoint '$Base': scheme must be http (Z3/I3)."
}
if ($uri.Host -notin @('localhost', '127.0.0.1')) {
    throw "Refusing Ollama endpoint '$Base': host must be exactly 'localhost' or '127.0.0.1' (Z3/I3)."
}
if ($uri.Port -ne 11434) {
    throw "Refusing Ollama endpoint '$Base': port must be 11434 (Z3/I3)."
}

# --- Guardrail 3: fresh abort sentinel state ---
$Sentinel = Join-Path $ProjectRoot 'data\tmp\BG1A_ABORT'
if (Test-Path $Sentinel) {
    Remove-Item $Sentinel -Force
    Write-Host 'note: cleared a stale abort sentinel before starting'
}

# --- Guardrail 4: evidence directories exist (all on D:) ---
foreach ($d in 'reports', 'data\tmp') {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $d) | Out-Null
}

# --- Guardrail 5: start-gate pre-flight (resource safety first) ---
# Refuse to even invoke the driver while the machine fails the 3 GB floor, so no
# model-adjacent code runs on a memory-pressured machine. The driver re-checks
# with psutil. The threshold is NEVER lowered automatically (owner requirement).
$os = Get-CimInstance Win32_OperatingSystem
$freeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)   # FreePhysicalMemory is KB
if ($freeGB -lt $RamStopGb) {
    Write-Host "BG-1a BLOCKED (start gate): free RAM $freeGB GB < $RamStopGb GB stop threshold. No model was loaded and the driver was not invoked. Free memory and re-run; the threshold is never lowered automatically."
    exit 2
}

Write-Host "BG-1a starting: model=$Model floor=$TooSlowFloor tok/s ram-stop=${RamStopGb}GB (emergency ${RamEmergencyGb}GB) cap=${MaxRuntimeMinutes}min"
Write-Host "Manual abort during the run: New-Item -ItemType File data\tmp\BG1A_ABORT"

# --- Run the driver (EAP Continue so uv's stderr progress cannot throw spuriously; R14) ---
$Driver = Join-Path $ProjectRoot 'scripts\bench_model.py'
$eap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& uv run --no-sync python $Driver --model $Model --base-url $Base --too-slow-floor $TooSlowFloor --ram-stop-gb $RamStopGb --ram-emergency-gb $RamEmergencyGb --max-minutes $MaxRuntimeMinutes --swap-epsilon-mb $SwapEpsilonMB --sentinel $Sentinel
$code = $LASTEXITCODE
$ErrorActionPreference = $eap

switch ($code) {
    0 { Write-Host 'BG-1a complete: verdict recorded (see reports\bg1a_*.md and *.json)' }
    2 { Write-Host 'BG-1a ABORTED: model unloaded; status recorded as blocked or too-heavy per stop reason (see reports\bg1a_*)' }
    default { Write-Host "BG-1a driver error (exit $code) - check reports\bg1a_* for the stop log" }
}
exit $code
