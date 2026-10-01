"""Research objective: a goal compiled into measurable, scoreable criteria."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from ..computational.catalog import CATALOG, PropertySpec, describe_catalog
from ..utils.logging import get_logger

logger = get_logger("objective")

# A measured hard-constraint violation halves the score.
HARD_VIOLATION_FACTOR = 0.5


class Criterion(BaseModel):
    property: str = Field(description="property name from the catalog")
    direction: Literal["maximize", "minimize", "require_true"]
    target: Optional[float] = Field(
        description="value that counts as good, in the catalog unit; "
        "null for require_true"
    )
    weight: float = Field(description="relative importance, > 0")
    hard: bool = Field(
        description="true when missing the target disqualifies a candidate"
    )


class ObjectiveSpec(BaseModel):
    title: str = Field(description="short campaign title")
    summary: str = Field(description="one-paragraph restatement of the goal")
    required_elements: List[str] = Field(
        description="element symbols every explored system must contain"
    )
    forbidden_elements: List[str] = Field(
        description="element symbols that must never appear"
    )
    criteria: List[Criterion]
    unsupported: List[str] = Field(
        description="parts of the goal that no catalog property can measure"
    )
    notes: str = Field(
        description="domain guidance for the search that the criteria don't capture"
    )

    def properties(self, tier: Optional[str] = None) -> List[str]:
        names = []
        for c in self.criteria:
            spec = CATALOG.get(c.property)
            if spec and (tier is None or spec.tier == tier) and c.property not in names:
                names.append(c.property)
        return names

    def check_system(self, elements: List[str]) -> Optional[str]:
        """Return why a chemical system violates the objective, or None."""
        present = set(elements)
        banned = present & set(self.forbidden_elements)
        if banned:
            return f"contains forbidden elements {sorted(banned)}"
        missing = set(self.required_elements) - present
        if missing:
            return f"missing required elements {sorted(missing)}"
        return None

    def complete(self, properties: Dict[str, Any]) -> bool:
        """True when every criterion has a measured value."""
        return all(properties.get(c.property) is not None for c in self.criteria)

    def gaps(self, properties: Dict[str, Any]) -> List[str]:
        """What keeps these properties from meeting the goal; empty when met."""
        gaps = []
        for c in self.criteria:
            value = properties.get(c.property)
            if value is None:
                gaps.append(f"{c.property} not measured")
            elif not _meets(c, value):
                unit = CATALOG[c.property].unit if c.property in CATALOG else ""
                wanted = (
                    "must be true"
                    if c.direction == "require_true"
                    else f"target {'≥' if c.direction == 'maximize' else '≤'} "
                    f"{c.target:g} {unit}"
                )
                gaps.append(f"{c.property} is {value:g} ({wanted})")
        return gaps

    def ceiling(self, properties: Dict[str, Any]) -> float:
        """The score if every unmeasured criterion came out perfectly."""
        return self.score(properties, optimistic=True)[0]

    def reachable(self, properties: List[str]) -> float:
        """Highest score possible when only ``properties`` are measured."""
        total = sum(c.weight for c in self.criteria)
        known = sum(c.weight for c in self.criteria if c.property in properties)
        return known / total if total > 0 else 0.0

    def score(
        self, properties: Dict[str, Any], optimistic: bool = False
    ) -> Tuple[float, Dict[str, float]]:
        """Score measured properties in [0, 1] with a per-criterion breakdown.

        Unmeasured properties earn nothing but still count in the denominator,
        so a half-evaluated candidate cannot outrank a fully evaluated one.
        ``optimistic`` gives them full credit instead (see ``ceiling``).
        """
        total = sum(c.weight for c in self.criteria)
        if total <= 0:
            return 0.0, {}

        breakdown: Dict[str, float] = {}
        violated = False
        for c in self.criteria:
            value = properties.get(c.property)
            credit = 1.0 if optimistic and value is None else _credit(c, value)
            breakdown[c.property] = round(credit, 4)
            if c.hard and value is not None and not _meets(c, value):
                violated = True

        score = sum(c.weight * breakdown[c.property] for c in self.criteria) / total
        if violated:
            score *= HARD_VIOLATION_FACTOR
        return max(0.0, min(1.0, score)), breakdown

    def describe(self) -> str:
        lines = [self.summary, "", "Criteria:"]
        for c in self.criteria:
            unit = CATALOG[c.property].unit if c.property in CATALOG else ""
            target = "" if c.target is None else f" target {c.target:g} {unit}"
            hard = " (hard constraint)" if c.hard else ""
            lines.append(
                f"- {c.direction} {c.property}{target}, weight {c.weight:g}{hard}"
            )
        if self.required_elements:
            lines.append(f"Required elements: {', '.join(self.required_elements)}")
        if self.forbidden_elements:
            lines.append(f"Forbidden elements: {', '.join(self.forbidden_elements)}")
        if self.unsupported:
            lines.append(f"Not measurable here: {'; '.join(self.unsupported)}")
        if self.notes:
            lines += ["", f"Notes: {self.notes}"]
        return "\n".join(lines)


def _credit(c: Criterion, value: Any) -> float:
    """Soft saturation: ~0.63 at the target, still rewarding beyond it."""
    if value is None:
        return 0.0
    if c.direction == "require_true":
        return 1.0 if value else 0.0
    if not c.target or c.target <= 0:
        return 0.0
    value = float(value)
    if c.direction == "maximize":
        return 1.0 - math.exp(-value / c.target) if value > 0 else 0.0
    return math.exp(-max(value, 0.0) / c.target)


def _meets(c: Criterion, value: Any) -> bool:
    if c.direction == "require_true":
        return bool(value)
    if c.target is None:
        return True
    return value >= c.target if c.direction == "maximize" else value <= c.target


def parse_elements(system_or_formula: str) -> List[str]:
    """Element symbols in a chemical system ("Fe-Co-Bi") or formula ("Fe2CoBi")."""
    return sorted(set(re.findall(r"[A-Z][a-z]?", system_or_formula or "")))


COMPILE_SYSTEM = """You turn a materials research goal into a precise search objective.

The search can only optimise what it can measure. The measurable properties are:
{catalog}

Write criteria only over those properties, using their units. Choose targets a
domain expert would call good for this goal, and weights that reflect what the
goal emphasises. Mark a criterion hard only when the goal states it as a
requirement. Stability criteria (e_hull, dynamic_stability) belong in almost
every objective because an unstable material is not a result.

Translate element language into explicit lists: "rare-earth-free" forbids every
lanthanide plus Sc and Y; "Gd-based" requires Gd. Anything in the goal that no
property can measure goes in `unsupported` rather than being approximated
silently. Use `notes` for guidance the criteria cannot express, such as
structural motifs worth favouring or elements to avoid for practical reasons."""


def compile_objective(
    llm, goal: str, catalog: Optional[Dict[str, PropertySpec]] = None
) -> ObjectiveSpec:
    """Compile a natural-language goal into an ObjectiveSpec via the LLM."""
    catalog = catalog or CATALOG
    spec = llm.structured(
        COMPILE_SYSTEM.format(catalog=describe_catalog(catalog)),
        f"Research goal:\n{goal}",
        ObjectiveSpec,
    )

    known = [c for c in spec.criteria if c.property in catalog and c.weight > 0]
    for c in spec.criteria:
        if c not in known:
            logger.warning(f"Dropping unusable criterion: {c.property}")
            spec.unsupported.append(f"criterion on '{c.property}'")
    spec.criteria = known
    if not spec.criteria:
        raise ValueError(
            "This goal cannot be pursued with the current property catalog. "
            f"Unsupported: {spec.unsupported}"
        )
    return spec
