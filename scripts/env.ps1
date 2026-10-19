# scripts\env.ps1 - project-local, process-scoped environment for the RAG Platform.
# Usage (dot-source, from the project root):
#   . .\scripts\env.ps1
# Sets NO user/machine variables and makes no global configuration changes (Z9, plan v2.3 section 12).
# All redirected paths resolve under D:\rag-platform (final storage decision 2026-10-18).
# NOTE (R14 finding): ASCII-only on purpose - Windows PowerShell 5.1 misreads
# UTF-8-without-BOM files as ANSI, and non-ASCII characters inside strings break parsing.

$ProjectRoot = 'D:\rag-platform'

# Dependency / interpreter caches (all writes land on D:)
$env:UV_CACHE_DIR          = Join-Path $ProjectRoot '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot '.python'
$env:HF_HOME               = Join-Path $ProjectRoot '.cache\huggingface'

# Temp files for the app AND any library honoring TMP/TEMP (plan section 12: data\tmp on D:)
$env:TEMP = Join-Path $ProjectRoot 'data\tmp'
$env:TMP  = Join-Path $ProjectRoot 'data\tmp'

# Hugging Face offline flags (plan section 12 / finding M-5): suppress download attempts by design.
# Per section 9.3 these are NOT proof of zero traffic - the socket-instrumented egress suite is.
$env:HF_HUB_OFFLINE            = '1'
$env:TRANSFORMERS_OFFLINE      = '1'
$env:HF_HUB_DISABLE_TELEMETRY  = '1'

# Application default: egress off (I3/Z4). Runtime .env may override; off is the default.
if (-not $env:EGRESS_MODE) { $env:EGRESS_MODE = 'off' }

Write-Host "env.ps1: project-local environment active (no global changes)."
Write-Host "  UV_CACHE_DIR          = $env:UV_CACHE_DIR"
Write-Host "  UV_PYTHON_INSTALL_DIR = $env:UV_PYTHON_INSTALL_DIR"
Write-Host "  HF_HOME               = $env:HF_HOME"
Write-Host "  TMP / TEMP            = $env:TEMP"
Write-Host "  EGRESS_MODE           = $env:EGRESS_MODE"
