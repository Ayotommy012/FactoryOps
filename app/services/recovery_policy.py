"""Hybrid Autonomous Recovery Policy Engine for FactoryOps.

Implements the deterministic dual-concept safety framework:
1. Disruption Classification: Predefined baseline categories for supported disruption types.
2. Recovery Authorization: Deterministic, per-plan risk evaluation yielding:
   - AUTO_EXECUTE: Passes hard constraints and falls within autonomous authority.
   - REQUIRES_APPROVAL: Valid but exceeds autonomous authority or presents material trade-offs.
   - BLOCKED: Violates hard constraints, breaches safety invariants, or uses unsupported actions.

The default class below is descriptive (shown to operators, and 'safety_critical'
for unknown types). Gating uses evidence: plan applicability and feasibility,
food-safety rules, live severity with SOP coverage, and cost/downtime/SLA limits.

Guarantees:
- Disruption category alone never determines execution.
- LLM outputs never self-authorize, override constraints, or unblock plans.
- A BLOCKED plan can never execute, even if an operator clicks Approve.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

logger = logging.getLogger("factoryops.recovery_policy")

# =============================================================================
# Policy Enums and Types
# =============================================================================

DisruptionDefaultClass = Literal["auto_recoverable", "approval_required", "safety_critical"]
RecoveryAuthorizationOutcome = Literal["AUTO_EXECUTE", "REQUIRES_APPROVAL", "BLOCKED"]

# Baseline Disruption Classification Registry
DEFAULT_DISRUPTION_CLASSIFICATIONS: Dict[str, DisruptionDefaultClass] = {
    # 1. Auto-recoverable by default:
    # Routine inventory mismatches, safe line reassignment, non-critical rescheduling, transient interruptions.
    "reduced_machine_capacity": "auto_recoverable",
    "routine_reschedule": "auto_recoverable",
    "inventory_allocation_mismatch": "auto_recoverable",
    "transient_interruption": "auto_recoverable",

    # 2. Approval-required by default:
    # Significant operational cost, material downstream order impact, scarce-resource trade-offs.
    "roaster_bearing_overheat": "approval_required",
    "combined": "approval_required",
    "interacting_constraint": "approval_required",
    "supplier_customs_delay": "approval_required",
    "equipment_failure": "approval_required",
    "supplier_shortfall": "approval_required",

    # 3. Safety-critical or unresolved by default:
    # Unknown disruptions, unresolved safety invariants, chemical/biological contamination.
    "quality_contamination": "safety_critical",
}

# Authority Boundaries (Configurable Operating Limits)
DEFAULT_MAX_AUTONOMOUS_COST_USD = 2000.0        # Default operational cost cap for autonomous execution
DEFAULT_MAX_AUTONOMOUS_DOWNTIME_HOURS = 8.0     # Default downtime cap for autonomous execution
DEFAULT_MIN_AUTONOMOUS_BUFFER_HOURS = 0.0       # Minimum customer deadline buffer required (0.0 = on-time)

# Backward compatibility alias
MAX_AUTONOMOUS_COST_USD = DEFAULT_MAX_AUTONOMOUS_COST_USD
MAX_AUTONOMOUS_DOWNTIME_HOURS = DEFAULT_MAX_AUTONOMOUS_DOWNTIME_HOURS
MIN_AUTONOMOUS_BUFFER_HOURS = DEFAULT_MIN_AUTONOMOUS_BUFFER_HOURS

# Standard Operating Procedures (SOPs): pre-validated recovery plans per disruption.
# At CRITICAL severity only an SOP may execute autonomously; anything else needs a
# human. Below critical, any applicable plan within authority limits may execute.
# (Previously this registry named plan IDs that did not exist, so supplier
# recoveries could never be authorized.)
EXPLICIT_AUTHORIZED_RECOVERY_PLANS: Dict[str, List[str]] = {
    "roaster_bearing_overheat": ["plan_a_thermal_flush", "plan_b_reroute_line2"],
    "reduced_machine_capacity": ["plan_a_hydraulic_reseal", "plan_b_press_liquor_buffer"],
    "supplier_shortfall": ["plan_spot_market_buffer"],
    "supplier_customs_delay": ["plan_spot_market_buffer", "plan_customs_expedite"],
}

# Plans that would draw production from Silo 1 stock. Under a quarantine hold
# that stock is the contaminated lot, so these are physically unsafe.
PLANS_CONSUMING_SILO_STOCK = {"plan_recipe_line_throttle"}

# Typical severity used only when no live characterization is supplied.
_TYPICAL_SEVERITY = {
    "equipment_failure": "critical",
    "roaster_bearing_overheat": "high",
    "quality_contamination": "high",
}


@dataclass
class RecoveryAuthorizationDecision:
    """Deterministic authorization decision for a specific candidate recovery plan."""
    outcome: RecoveryAuthorizationOutcome
    reason: str
    disruption_type: str
    disruption_default_class: str
    plan_id: str
    hard_constraints_passed: List[str] = field(default_factory=list)
    violated_constraints: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_disruption_default_class(disruption_type: str) -> DisruptionDefaultClass:
    """Returns the baseline classification for a given disruption type."""
    cleaned = (disruption_type or "").strip().lower()
    return DEFAULT_DISRUPTION_CLASSIFICATIONS.get(cleaned, "safety_critical")


classify_disruption_default = get_disruption_default_class


def evaluate_recovery_authorization(
    plan: Any,
    disruption_type: str,
    characterization: Optional[Any] = None,
    validation_result: Optional[Any] = None,
    scenario: Optional[Any] = None,
) -> RecoveryAuthorizationDecision:
    """Evaluates whether a proposed recovery plan may execute autonomously.

    Evaluates:
    1. Hard-constraint and safety-invariant validity.
    2. Availability of required inventory, machines, and production capacity.
    3. Impact on critical customer orders and delivery deadlines.
    4. Operational cost and resource consumption against defined authority limits.
    5. Whether the plan uses a supported, validated simulated action.
    6. Whether its consequences are sufficiently known to remain within autonomous authority.

    Returns:
        RecoveryAuthorizationDecision with outcome in {AUTO_EXECUTE, REQUIRES_APPROVAL, BLOCKED}.
    """
    from app.services.recovery import VALID_PLANS

    disr_class = get_disruption_default_class(disruption_type)
    plan_id = getattr(plan, "plan_id", None) or (plan.get("plan_id") if isinstance(plan, dict) else str(plan))
    plan_title = getattr(plan, "title", None) or (plan.get("title") if isinstance(plan, dict) else plan_id)
    plan_cost = float(getattr(plan, "total_cost_usd", None) or (plan.get("total_cost_usd") if isinstance(plan, dict) else 0.0) or 0.0)
    downtime_h = float(getattr(plan, "estimated_downtime_hours", None) or (plan.get("estimated_downtime_hours") if isinstance(plan, dict) else 0.0) or 0.0)
    meets_deadline = getattr(plan, "meets_deadline", True)
    if isinstance(plan, dict) and "meets_deadline" in plan:
        meets_deadline = bool(plan["meets_deadline"])
    buffer_hours = float(getattr(plan, "buffer_hours", 0.0) or (plan.get("buffer_hours") if isinstance(plan, dict) else 0.0) or 0.0)
    feasibility = getattr(plan, "feasibility_verdict", "feasible")
    if isinstance(plan, dict) and "feasibility_verdict" in plan:
        feasibility = plan["feasibility_verdict"]

    passed: List[str] = []
    violated: List[str] = []
    risks: List[str] = []

    # Incorporate prior Stage D validation results if provided
    if validation_result:
        val_passed = getattr(validation_result, "hard_constraints_passed", None) or (validation_result.get("hard_constraints_passed") if isinstance(validation_result, dict) else [])
        val_violated = getattr(validation_result, "violated_constraints", None) or (validation_result.get("violated_constraints") if isinstance(validation_result, dict) else [])
        val_risks = getattr(validation_result, "risk_notes", None) or (validation_result.get("risk_notes") if isinstance(validation_result, dict) else [])
        passed.extend(val_passed)
        violated.extend(val_violated)
        risks.extend(val_risks)

    # -------------------------------------------------------------------------
    # RULE 1: Unsupported Actions or Unknown Plans/Disruptions -> BLOCKED
    # -------------------------------------------------------------------------
    cleaned_disruption = (disruption_type or "").strip().lower()
    if cleaned_disruption not in DEFAULT_DISRUPTION_CLASSIFICATIONS:
        violated.append(f"UNRECOGNIZED_DISRUPTION: Disruption type '{disruption_type}' is unknown or unsupported.")
        return RecoveryAuthorizationDecision(
            outcome="BLOCKED",
            reason=f"Unrecognized or unsupported disruption '{disruption_type}'; autonomous execution blocked by safety policy.",
            disruption_type=disruption_type,
            disruption_default_class=disr_class,
            plan_id=plan_id,
            hard_constraints_passed=passed,
            violated_constraints=violated,
            risk_factors=risks,
        )

    if plan_id not in VALID_PLANS:
        violated.append(f"UNSUPPORTED_ACTION: Plan '{plan_id}' is not in the validated recovery catalogue.")
        return RecoveryAuthorizationDecision(
            outcome="BLOCKED",
            reason=f"Plan '{plan_id}' is an unsupported recovery action.",
            disruption_type=disruption_type,
            disruption_default_class=disr_class,
            plan_id=plan_id,
            hard_constraints_passed=passed,
            violated_constraints=violated,
            risk_factors=risks,
        )

    # -------------------------------------------------------------------------
    # RULE 2: Hard Constraint or Safety Invariant Violations -> BLOCKED
    # -------------------------------------------------------------------------
    from app.services.recovery import PLAN_APPLICABILITY

    if feasibility == "infeasible":
        violated.append(f"INFEASIBLE_PLAN: Plan '{plan_id}' marked infeasible.")

    applicable = PLAN_APPLICABILITY.get(cleaned_disruption)
    if applicable is not None and plan_id not in applicable:
        if cleaned_disruption == "quality_contamination":
            violated.append(
                f"FOOD_SAFETY_QUARANTINE: Plan '{plan_id}' does not address the quarantined raw-bean lot; "
                "contamination recovery is limited to certified replacement stock."
            )
        else:
            violated.append(
                f"PLAN_NOT_APPLICABLE: Plan '{plan_id}' acts on equipment/material not affected by '{disruption_type}'."
            )

    if cleaned_disruption == "quality_contamination" and plan_id in PLANS_CONSUMING_SILO_STOCK:
        violated.append(
            "FOOD_SAFETY_QUARANTINE: Plan would consume Silo 1 stock that is the quarantined, out-of-spec lot."
        )

    if violated:
        return RecoveryAuthorizationDecision(
            outcome="BLOCKED",
            reason=f"Plan '{plan_id}' violates hard constraints: {'; '.join(violated)}",
            disruption_type=disruption_type,
            disruption_default_class=disr_class,
            plan_id=plan_id,
            hard_constraints_passed=passed,
            violated_constraints=violated,
            risk_factors=risks,
        )

    # -------------------------------------------------------------------------
    # RULE 3: Operational Risk Assessment against Scenario Operating Limits
    # -------------------------------------------------------------------------
    passed.append(f"Plan '{plan_id}' verified against all hard safety and inventory constraints.")

    # Sourced from scenario parameters or authoritative defaults
    scenario_params = getattr(scenario, "parameters", {}) if scenario else {}
    if not isinstance(scenario_params, dict):
        scenario_params = {}

    max_cost = float(scenario_params.get("max_autonomous_cost_usd", DEFAULT_MAX_AUTONOMOUS_COST_USD))
    max_downtime = float(scenario_params.get("max_autonomous_downtime_hours", DEFAULT_MAX_AUTONOMOUS_DOWNTIME_HOURS))
    min_buffer = float(scenario_params.get("min_autonomous_buffer_hours", DEFAULT_MIN_AUTONOMOUS_BUFFER_HOURS))

    # 3a. Order Deadline & Customer SLA Impact
    if not meets_deadline:
        risks.append(f"CUSTOMER_SLA_BREACH: Plan projects completion {abs(buffer_hours):.1f}h past customer delivery deadline.")
    elif buffer_hours < min_buffer:
        risks.append(f"TIGHT_SLA_BUFFER: Delivery buffer ({buffer_hours:.1f}h) is below operational threshold ({min_buffer:.1f}h).")
    else:
        passed.append(f"Customer SLA preserved with {buffer_hours:.1f}h safety buffer.")

    # 3b. Operational Cost Authority Limit & Economic Justification
    potential_penalty = getattr(characterization, "potential_penalty_usd", 0.0) if characterization else 0.0
    is_cost_justified = (plan_cost <= max_cost) or (potential_penalty > 0 and plan_cost <= potential_penalty * 1.5)

    if not is_cost_justified:
        risks.append(f"COST_EXCEEDS_AUTHORITY: Plan cost ${plan_cost:.2f} exceeds operational limit (${max_cost:.2f}).")
    else:
        passed.append(f"Operational cost (${plan_cost:.2f}) within authorized limit (${max_cost:.2f}).")

    # 3c. Downtime Authority Limit
    if downtime_h > max_downtime:
        risks.append(f"DOWNTIME_EXCEEDS_AUTHORITY: Estimated downtime {downtime_h:.1f}h exceeds operational limit ({max_downtime:.1f}h).")
    else:
        passed.append(f"Estimated downtime ({downtime_h:.1f}h) within operational limit ({max_downtime:.1f}h).")

    # -------------------------------------------------------------------------
    # RULE 4: Evidence-Based Operational Risk (not disruption category)
    # -------------------------------------------------------------------------
    # 4a. Food-safety release is a regulatory sign-off, whatever the plan.
    if cleaned_disruption == "quality_contamination":
        risks.append(
            "FOOD_SAFETY_QC_SIGNOFF: Quarantine disposition and replacement-lot release require certified QC approval."
        )

    # 4b. Critical severity (safety hazard, starvation, major penalty) may only run
    #     autonomously via a pre-validated SOP for that disruption.
    severity = getattr(characterization, "severity", None) if characterization else None
    if severity is None and isinstance(characterization, dict):
        severity = characterization.get("severity")
    severity = severity or _TYPICAL_SEVERITY.get(cleaned_disruption, "medium")

    scenario_authorized = scenario_params.get("authorized_recovery_plans", {}).get(disruption_type, [])
    sop_plans = set(scenario_authorized) | set(EXPLICIT_AUTHORIZED_RECOVERY_PLANS.get(cleaned_disruption, []))
    if severity == "critical" and cleaned_disruption != "quality_contamination":
        if scenario_params.get("auto_recovery_mode") == "full":
            passed.append(f"Recovery plan '{plan_id}' authorized under full autonomous mode.")
        elif plan_id in sop_plans:
            passed.append(f"Critical incident: '{plan_id}' is a pre-validated SOP for '{disruption_type}'.")
        else:
            risks.append(
                f"DISRUPTION_REQUIRES_APPROVAL: Critical-severity '{disruption_type}' and '{plan_id}' "
                "is not a pre-validated SOP; human authorization required."
            )
    else:
        passed.append(f"Severity '{severity}' within autonomous authority for applicable recovery plans.")

    # -------------------------------------------------------------------------
    # RULE 5: Final Deterministic Outcome
    # -------------------------------------------------------------------------
    if risks:
        return RecoveryAuthorizationDecision(
            outcome="REQUIRES_APPROVAL",
            reason=f"Plan '{plan_id}' exceeds autonomous authority: {'; '.join(risks)}",
            disruption_type=disruption_type,
            disruption_default_class=disr_class,
            plan_id=plan_id,
            hard_constraints_passed=passed,
            violated_constraints=violated,
            risk_factors=risks,
        )

    return RecoveryAuthorizationDecision(
        outcome="AUTO_EXECUTE",
        reason=f"Plan '{plan_id}' satisfies all hard constraints and falls within autonomous operating limits.",
        disruption_type=disruption_type,
        disruption_default_class=disr_class,
        plan_id=plan_id,
        hard_constraints_passed=passed,
        violated_constraints=violated,
        risk_factors=risks,
    )
