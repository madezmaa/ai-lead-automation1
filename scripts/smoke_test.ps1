# Smoke test: boots the API (if needed) and verifies the full HTTP flow.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\smoke_test.ps1
param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Python = ""
)
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $repo ".venv\Scripts\python.exe" }
$work = Join-Path $env:TEMP "ai-lead-smoke"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$outFile = Join-Path $work "body.json"

$script:failed = 0
$script:startedServer = $false

function Check([string]$name, [bool]$ok, [string]$detail = "") {
    if ($ok) { Write-Host "PASS  $name" }
    else { Write-Host "FAIL  $name  $detail"; $script:failed++ }
}

function Api {
    param(
        [string]$Method,
        [string]$Path,
        [string]$Body = $null,
        [hashtable]$Headers = @{},
        [int]$Timeout = 200
    )
    $cargs = @("--noproxy=*", "-sS", "-m", "$Timeout", "-X", $Method, "-o", $outFile, "-w", "%{http_code}", "$BaseUrl$Path")
    Remove-Item $outFile -ErrorAction SilentlyContinue
    if ($null -ne $Body) {
        $tmpBody = Join-Path $work "req.json"
        [IO.File]::WriteAllText($tmpBody, $Body, [Text.UTF8Encoding]::new($false))
        $cargs += @("-H", "Content-Type: application/json", "--data-binary", "@$tmpBody")
    }
    foreach ($k in $Headers.Keys) { $cargs += @("-H", "${k}: $($Headers[$k])") }
    $raw = & curl.exe @cargs
    $code = "$raw".Trim()
    if ($LASTEXITCODE -ne 0 -or -not $code) { $code = "000" }
    $content = ""
    if (Test-Path $outFile) { $content = Get-Content $outFile -Raw -ErrorAction SilentlyContinue }
    return [pscustomobject]@{ Code = $code; Body = $content }
}

function Get-Json([string]$text) {
    try { return ($text | ConvertFrom-Json) } catch { return $null }
}

# --- 0. start server if not already up -------------------------------------
$health = Api -Method GET -Path "/health" -Timeout 5
if ($health.Code -ne 200) {
    $log = Join-Path $work "server.log"
    $psi = Start-Process -FilePath $Python -ArgumentList @(
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $repo -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru -NoNewWindow
    $script:startedServer = $true
    $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
        $health = Api -Method GET -Path "/health" -Timeout 5
        if ($health.Code -eq 200) { break }
    }
}
Check "server up (GET /health -> 200)" ($health.Code -eq 200) "(got $($health.Code))"
$h = Get-Json $health.Body
Check "health reports database + ollama" (
    $h -and $h.database -eq "up" -and $null -ne $h.ollama
) ($health.Body)

# --- 1. create lead ---------------------------------------------------------
$stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$email = "smoke.$stamp@example.com"
$leadPayload = @{
    email = $email; first_name = "Sara"; last_name = "Doe"; company = "SmokeCo"
    job_title = "CTO"; phone = "+1 415 555 0100"; website = "smokeco.io"
    industry = "saas"; country = "United States"; source = "website"
    message = "We want a demo and pricing for our team."; budget = 50000; company_size = 120
} | ConvertTo-Json

$created = Api -Method POST -Path "/api/v1/leads" -Body $leadPayload
Check "create lead -> 201" ($created.Code -eq 201) "(got $($created.Code))"
$lead = Get-Json $created.Body
$leadId = $lead.id
Check "created lead has id" ($null -ne $leadId) $created.Body

# --- 2. duplicate -----------------------------------------------------------
$dup = Api -Method POST -Path "/api/v1/leads" -Body $leadPayload
Check "duplicate email -> 409 duplicate_lead" (
    $dup.Code -eq 409 -and (Get-Json $dup.Body).code -eq "duplicate_lead"
) "(got $($dup.Code)) $($dup.Body)"

# --- 3. validation ----------------------------------------------------------
$invalid = Api -Method POST -Path "/api/v1/leads" -Body '{"first_name":"NoEmail"}'
Check "invalid payload -> 422" ($invalid.Code -eq 422) "(got $($invalid.Code))"

# --- 4. idempotency (fresh email so it does not hit the duplicate guard) ----
$idemEmail = "idem.$stamp@example.com"
$idemPayload = @{
    email = $idemEmail; first_name = "Ida"; company = "IdemCo"; source = "website"
} | ConvertTo-Json
$key = "smoke-key-$stamp"
$idem1 = Api -Method POST -Path "/api/v1/leads" -Body $idemPayload -Headers @{"Idempotency-Key" = $key }
$idem2 = Api -Method POST -Path "/api/v1/leads" -Body $idemPayload -Headers @{"Idempotency-Key" = $key }
Check "idempotent create -> 201" ($idem1.Code -eq 201) "(got $($idem1.Code))"
Check "idempotent replay -> 200, same id" (
    $idem2.Code -eq 200 -and (Get-Json $idem2.Body).id -eq (Get-Json $idem1.Body).id
) "(got $($idem2.Code)) $($idem2.Body)"

# --- 5. qualification with real Ollama --------------------------------------
$qual = Api -Method POST -Path "/api/v1/leads/$leadId/qualify" -Body '{"use_ai": true}' -Timeout 300
Check "qualify -> 200" ($qual.Code -eq 200) "(got $($qual.Code)) $($qual.Body)"
$result = Get-Json $qual.Body
Check "qualification has decision + score" (
    $result -and $result.decision -in @("qualified", "nurture", "disqualified") -and $null -ne $result.score
) $qual.Body
if ($result) {
    Write-Host "      decision=$($result.decision) score=$($result.score) ai_used=$($result.ai_used) fallback=$($result.fallback_used)"
    Check "AI layer actually used (ai_used=true)" ($result.ai_used -eq $true) $qual.Body
}

# --- 6. history -------------------------------------------------------------
$hist = Api -Method GET -Path "/api/v1/leads/$leadId/qualifications"
$histJson = Get-Json $hist.Body
Check "qualifications history -> 1 result" ($hist.Code -eq 200 -and $histJson.total -ge 1) $hist.Body

# --- 7. state machine -------------------------------------------------------
$arch = Api -Method PATCH -Path "/api/v1/leads/$leadId/status" -Body '{"status":"archived","reason":"smoke done"}'
Check "archive lead -> 200" ($arch.Code -eq 200) "(got $($arch.Code)) $($arch.Body)"
Check "lead now archived" ((Get-Json $arch.Body).status -eq "archived") $arch.Body

$bad = Api -Method PATCH -Path "/api/v1/leads/$leadId/status" -Body '{"status":"qualifying"}'
Check "archived -> qualifying rejected -> 409" (
    $bad.Code -eq 409 -and (Get-Json $bad.Body).code -eq "invalid_transition"
) "(got $($bad.Code)) $($bad.Body)"

# --- 8. follow-up draft (deterministic fallback + AI) -----------------------
$draft = Api -Method POST -Path "/api/v1/leads/$leadId/follow-up-draft" -Body '{"use_ai": false, "tone": "friendly"}'
$draftJson = Get-Json $draft.Body
Check "follow-up draft (template) -> 200" (
    $draft.Code -eq 200 -and $draftJson.subject -and $draftJson.body
) "(got $($draft.Code)) $($draft.Body)"
Check "template draft marked fallback" ($draftJson.fallback_used -eq $true) $draft.Body

$draftAi = Api -Method POST -Path "/api/v1/leads/$leadId/follow-up-draft" -Body '{"use_ai": true}' -Timeout 300
Check "follow-up draft (AI) -> 200" (
    $draftAi.Code -eq 200 -and (Get-Json $draftAi.Body).subject
) "(got $($draftAi.Code)) $($draftAi.Body)"
Write-Host "      ai_used=$((Get-Json $draftAi.Body).ai_used)"

# --- 9. list / search / 404 / openapi ---------------------------------------
$list = Api -Method GET -Path "/api/v1/leads?q=$email"
Check "list/search finds lead" ($list.Code -eq 200 -and (Get-Json $list.Body).total -ge 1) $list.Body

$missing = Api -Method GET -Path "/api/v1/leads/00000000-0000-0000-0000-000000000000"
Check "unknown lead -> 404" ($missing.Code -eq 404) "(got $($missing.Code))"

$openapi = Api -Method GET -Path "/openapi.json"
Check "openapi.json -> 200" ($openapi.Code -eq 200) "(got $($openapi.Code))"

# --- teardown ---------------------------------------------------------------
if ($script:startedServer) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*uvicorn app.main:app*127.0.0.1*8000*" } |
        ForEach-Object { taskkill /T /F /PID $_.ProcessId *> $null }
}

Write-Host ""
if ($script:failed -eq 0) { Write-Host "SMOKE TEST PASSED"; exit 0 }
Write-Host "SMOKE TEST FAILED ($script:failed failures)"; exit 1
