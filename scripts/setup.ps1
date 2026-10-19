# scripts\setup.ps1 - Phase 0 environment checks (plan v2.3, sections 12 and 15 Phase 0).
# Run from the project root:   .\scripts\setup.ps1
# Destination-aware ONLY: gates on D: sufficiency/writability. There is NO C: free-space gate (Z9).
# Writes a dated report to reports\. Uses the frozen status semantics (section 9.1):
#   passed / failed / skipped / blocked / not-measured
# NOTE (R14 finding): this script is ASCII-only on purpose - Windows PowerShell 5.1 misreads
# UTF-8-without-BOM files as ANSI, and non-ASCII characters inside strings break parsing.

$ErrorActionPreference = 'Stop'
$ProjectRoot = 'D:\rag-platform'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Results = [System.Collections.Generic.List[object]]::new()

function Add-Result($check, $status, $observed) {
    $Results.Add([pscustomobject]@{ Check = $check; Status = $status; Observed = $observed })
    Write-Host ("[{0,-10}] {1} -- {2}" -f $status, $check, $observed)
}

# --- Activate project-local environment (process-scoped; no global changes) ---
. (Join-Path $ProjectRoot 'scripts\env.ps1')

# --- Create operational directories (all on D:) ---
$Dirs = @(
    'data\tmp', 'data\logs', 'data\traces', 'data\sqlite', 'data\qdrant',
    'data\cache', 'data\corpus', 'data\graph', 'data\artifacts',
    'reports', '.cache\uv', '.cache\huggingface'
)
foreach ($d in $Dirs) { New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $d) | Out-Null }
Add-Result 'operational directories created' 'passed' ($Dirs -join ', ')

# --- 1. D: free space: working minimum 10 GB (destination-aware; C: is never gated) ---
$D = Get-Volume -DriveLetter D
$DFreeGB = [math]::Round($D.SizeRemaining / 1GB, 2)
if ($DFreeGB -ge 10) {
    Add-Result 'D: free space >= 10 GB working minimum' 'passed' "$DFreeGB GB free (observed)"
} else {
    Add-Result 'D: free space >= 10 GB working minimum' 'failed' "$DFreeGB GB free (observed) -- below 10 GB working minimum; stopping"
    throw "D: free space $DFreeGB GB is below the 10 GB working minimum. Free space on D: and re-run."
}

# --- 2. D: writable (destination-aware) ---
$Probe = Join-Path $ProjectRoot "data\tmp\setup_probe_$Stamp.tmp"
try {
    'probe' | Set-Content -Path $Probe -Encoding utf8
    Remove-Item $Probe -Force
    Add-Result 'D: writable' 'passed' $Probe
} catch {
    Add-Result 'D: writable' 'failed' $_.Exception.Message
    throw
}

# --- 3. Resolved-path assertions: everything must resolve under D:\rag-platform ---
$Assert = @()
foreach ($v in 'UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR', 'HF_HOME', 'TEMP', 'TMP') {
    $val = [Environment]::GetEnvironmentVariable($v, 'Process')
    if (-not $val -or -not $val.StartsWith('D:\rag-platform')) {
        Add-Result "path assertion: $v under D:" 'failed' "resolved='$val' -- NOT under D:\rag-platform; stopping"
        throw "$v does not resolve under D:\rag-platform (resolved to '$val'). Refusing to continue (Z9)."
    }
    $Assert += "$v=$val"
}
foreach ($p in 'data\logs', 'data\tmp', 'reports') {
    $full = Join-Path $ProjectRoot $p
    if (-not $full.StartsWith('D:\rag-platform')) {
        Add-Result "path assertion: $p under D:" 'failed' $full
        throw "$p does not resolve under D:\rag-platform."
    }
    $Assert += $p
}
Add-Result 'resolved-path assertions (all under D:\rag-platform)' 'passed' ($Assert -join '; ')

# --- 4. FTS5 availability in the project python (R20) ---
# Script-file form on purpose: PS 5.1 strips embedded double quotes from native -c args (R14 finding).
# EAP temporarily Continue so uv's stderr progress never throws a spurious NativeCommandError.
$Fts5Script = Join-Path $ProjectRoot 'scripts\check_fts5.py'
$eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$out = & uv run --no-sync python $Fts5Script 2>&1
$code = $LASTEXITCODE
$ErrorActionPreference = $eap
if ($code -eq 0) {
    Add-Result 'FTS5 available (project python)' 'passed' "$out"
} else {
    Add-Result 'FTS5 available' 'failed' "exit $code : $out"
}

# --- 5. Python version (R7 record) ---
$eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$pyv = & uv run --no-sync python -c 'import sys; print(sys.version.split()[0])' 2>&1
$code = $LASTEXITCODE
$ErrorActionPreference = $eap
if ($code -eq 0) {
    Add-Result 'project python version' 'passed' "$pyv"
} else {
    Add-Result 'project python version' 'blocked' "exit $code : $pyv (run uv sync first)"
}

# --- 6. Ollama reachable (warn-only per plan) ---
try {
    $tags = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 5
    $names = ($tags.models | ForEach-Object { $_.name }) -join ', '
    Add-Result 'Ollama reachable (warn-only)' 'passed' "localhost:11434 -- models: $names"
} catch {
    Add-Result 'Ollama reachable (warn-only)' 'blocked' "not reachable now: $($_.Exception.Message)"
}

# --- 7. Informational snapshot (C: informational only -- never a gate) ---
$C = Get-Volume -DriveLetter C
$CFreeGB = [math]::Round($C.SizeRemaining / 1GB, 2)
Add-Result 'disk snapshot (informational)' 'passed' "C: $CFreeGB GB free (informational, NO gate); D: $DFreeGB GB free (primary)"

# --- Report ---
$ReportDir = Join-Path $ProjectRoot 'reports'
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$Report = Join-Path $ReportDir "setup_phase0_$Stamp.md"
$Lines = @()
$Lines += "# Setup report - Phase 0 ($Stamp)"
$Lines += ''
$Lines += "Generated by scripts\setup.ps1. Status semantics per plan v2.3 section 9.1. **No C: free-space gate exists (Z9).**"
$Lines += ''
$Lines += '| Check | Status | Observed |'
$Lines += '|---|---|---|'
foreach ($r in $Results) { $Lines += "| $($r.Check) | $($r.Status) | $($r.Observed) |" }
$Lines += ''
$Failed = @($Results | Where-Object Status -eq 'failed')
$Lines += "**Result:** $(if ($Failed.Count -eq 0) { 'all checks passed or warned-only - environment ready for Phase 0 work' } else { "$($Failed.Count) check(s) FAILED - see above" })"
$Lines | Set-Content -Path $Report -Encoding utf8
Write-Host "`nReport written: $Report"
if ($Failed.Count -gt 0) { exit 1 }
