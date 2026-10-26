# ==============================================================================
# FACTORYOPS CONTROLLED LIVE RECOVERY TEST RUNNER
# TARGET: scn_prepared_live_01 | PROVIDER: meterless | JOBS: EXACTLY 1 (n=2)
# Windows PowerShell 5.1 and PowerShell 7+ Compatible
# ==============================================================================
[CmdletBinding()]
param(
    [string]$AuthorizationPhrase = ""
)

$ErrorActionPreference = "Stop"

# Guardrail 1: Abort immediately if cloud gate is already active
$initialGate = [Environment]::GetEnvironmentVariable("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "Process")
if (-not [string]::IsNullOrWhiteSpace($initialGate) -and $initialGate -match "^(true|1|yes)$") {
    Write-Error "ABORT: NIGHTSHIFT_ALLOW_CLOUD_JOBS is already enabled in this process. Clear environment first."
    exit 1
}

# Guardrail 2: Strict Preflight Validation against live server
Write-Host "Executing preflight checks on http://127.0.0.1:8000/simulations/scn_prepared_live_01..."
try {
    $scn = Invoke-RestMethod -Uri "http://127.0.0.1:8000/simulations/scn_prepared_live_01" -Method Get -ErrorAction Stop
} catch {
    Write-Error "ABORT: Unable to query simulation scenario: $_"
    exit 1
}

if ($null -eq $scn -or $scn.id -ne "scn_prepared_live_01") {
    Write-Error "ABORT: Scenario ID mismatch: '$($scn.id)'"
    exit 1
}
if ($scn.current_step -ne 6 -or $scn.status -ne "running" -or $scn.disruption_type -ne "roaster_bearing_overheat") {
    Write-Error "ABORT: Scenario state mismatch: Step=$($scn.current_step), Status=$($scn.status), Disruption=$($scn.disruption_type)"
    exit 1
}
if ($scn.simulation_time -notlike "2026-10-22T07:30:00*") {
    Write-Error "ABORT: Simulation clock mismatch: $($scn.simulation_time) (expected 2026-10-22T07:30:00)"
    exit 1
}
if ($null -eq $scn.snapshot -or $null -eq $scn.snapshot.machines -or $null -eq $scn.snapshot.orders) {
    Write-Error "ABORT: Scenario snapshot structure is incomplete."
    exit 1
}

# Machine verification (ROAST-01)
$roast = $scn.snapshot.machines.'machine:plant:ROAST-01'
if ($null -eq $roast) {
    Write-Error "ABORT: Machine 'machine:plant:ROAST-01' not found in snapshot."
    exit 1
}
if ($roast.status -ne "degraded" -or [math]::Round([double]$roast.temperature_c, 1) -ne 142.5 -or [math]::Round([double]$roast.vibration_rms, 1) -ne 4.2) {
    Write-Error "ABORT: ROAST-01 readings mismatch! Status=$($roast.status), Temp=$($roast.temperature_c), Vib=$($roast.vibration_rms)"
    exit 1
}

# Order verification (ORD-9402)
$ord = $scn.snapshot.orders.'customer_order:erp:ORD-9402'
if ($null -eq $ord -or $ord.status -ne "at_risk" -or $ord.deadline -notlike "2026-10-22T14:00:00*") {
    Write-Error "ABORT: Order ORD-9402 state mismatch! Status=$($ord.status), Deadline=$($ord.deadline)"
    exit 1
}

# Recovery state verification
if ($null -ne $scn.parameters.autonomous_recovery_history) {
    Write-Error "ABORT: Scenario already has autonomous recovery history recorded."
    exit 1
}

Write-Host "Preflight PASSED: Scenario scn_prepared_live_01 is pristine at Step 6 (07:30:00) with ROAST-01 degraded at 142.5C (Deadline 14:00:00, 6.5h buffer remaining).`n"

# Guardrail 3: Operator Confirmation
if ([string]::IsNullOrWhiteSpace($AuthorizationPhrase)) {
    $confirmation = Read-Host "Type 'AUTHORIZE-SINGLE-CLOUD-JOB' to execute exactly ONE live Nightshift cloud job"
} else {
    $confirmation = $AuthorizationPhrase
}
if ($confirmation -cne "AUTHORIZE-SINGLE-CLOUD-JOB") {
    Write-Error "ABORT: Execution cancelled by operator."
    exit 1
}

# Guardrail 4: Scoped execution with fail-safe teardown in finally
$origEnv = [Environment]::GetEnvironmentVariable("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "Process")
$pyExitCode = 0
$restorationVerified = $false

try {
    Write-Host "`n[GATE] Setting NIGHTSHIFT_ALLOW_CLOUD_JOBS = 'true' for child process..."
    [Environment]::SetEnvironmentVariable("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "true", "Process")

    & .\.venv\Scripts\python.exe -c @"
import json, sys
from dataclasses import asdict
from app.database import SessionLocal
from app.providers.meterless import MeterlessClient, resolve_meterless_token
from app.services.autonomous_recovery import (
    run_autonomous_recovery_lifecycle,
    verify_live_inference_provenance,
)

db = SessionLocal()
try:
    # 1. Execute recovery lifecycle (Stage A through H)
    record = run_autonomous_recovery_lifecycle(
        session=db,
        scenario_id='scn_prepared_live_01',
        max_rounds=3,
        provider='meterless',
        auto_advance_steps=True
    )
    clean_record = asdict(record)
    print('=== LIFECYCLE_EXECUTION_COMPLETED ===')
    print(json.dumps(clean_record, indent=2, default=str))

    # 2. Strict read-only post-run verification of genuine live inference
    token = resolve_meterless_token()
    client = MeterlessClient(token=token) if token else None
    verif = verify_live_inference_provenance(record, client=client, session=db)
    
    print('\n=== INDEPENDENT_PROVENANCE_VERIFICATION ===')
    print(f'Verdict: {verif.verdict}')
    print(f'Reason:  {verif.reason}')
    print(f'Details: {json.dumps(asdict(verif), indent=2, default=str)}')

    if not verif.verified:
        print('\n[ALERT] LIVE INFERENCE NOT VERIFIED: Recovery used deterministic fallback or lacks complete provenance.', file=sys.stderr)
        sys.exit(2)

except Exception as e:
    print(f'FATAL_ERROR: {e}', file=sys.stderr)
    sys.exit(1)
finally:
    db.close()
"@

    $pyExitCode = $LASTEXITCODE
}
finally {
    # Safeguard B: Guarantee gate is restored immediately and verified (PowerShell 5.1 compatible)
    if (-not [string]::IsNullOrWhiteSpace($origEnv)) {
        [Environment]::SetEnvironmentVariable("NIGHTSHIFT_ALLOW_CLOUD_JOBS", $origEnv, "Process")
    } else {
        [Environment]::SetEnvironmentVariable("NIGHTSHIFT_ALLOW_CLOUD_JOBS", $null, "Process")
    }

    $restoredGate = [Environment]::GetEnvironmentVariable("NIGHTSHIFT_ALLOW_CLOUD_JOBS", "Process")
    if ([string]::IsNullOrWhiteSpace($origEnv)) {
        $restorationVerified = [string]::IsNullOrWhiteSpace($restoredGate)
    } else {
        $restorationVerified = ($restoredGate -eq $origEnv)
    }

    $displayGate = if ([string]::IsNullOrWhiteSpace($restoredGate)) { "UNSET" } else { $restoredGate }
    if ($restorationVerified) {
        Write-Host "`n[GATE TEARDOWN] NIGHTSHIFT_ALLOW_CLOUD_JOBS successfully restored to: $displayGate"
    } else {
        Write-Error "`nCRITICAL SAFETY ERROR: Failed to restore NIGHTSHIFT_ALLOW_CLOUD_JOBS to original process state!"
    }
}

# Safeguard B Check: Fail if environment restoration could not be verified
if (-not $restorationVerified) {
    Write-Error "CRITICAL: Process exiting nonzero because cloud gate restoration could not be verified."
    exit 10
}

# Safeguard A: Propagate failed live-verification status and other errors
if ($pyExitCode -eq 2) {
    Write-Error "FAILED: Lifecycle completed but LIVE INFERENCE PROVENANCE WAS NOT VERIFIED (exit code 2)."
    exit 2
} elseif ($pyExitCode -ne 0) {
    Write-Error "FAILED: Python process exited with non-zero exit code: $pyExitCode"
    exit $pyExitCode
}

Write-Host "`n[SUCCESS] Single live Nightshift recovery lifecycle executed and genuine live inference verified."
exit 0
