"""A synthetic materials world for exercising the search loop without Ouro.

Stands in for GGen and the property routes with instant, deterministic
answers, so a whole campaign costs a few LLM calls and no compute. The world
is loosely physical (magnetic 3d elements carry moment, heavy elements bring
anisotropy and cost) but its details are invented per seed: which element
pairs help or hurt each other is hidden, and can only be learned by measuring.
Because the whole world can be enumerated, ``SimWorld.oracle`` gives the best
score any search could reach, to compare a run against.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..utils.logging import get_logger
from .catalog import CATALOG, EXPENSIVE
from .evaluator import Measurement
from .explorer import (
    _CRYSTAL_SYSTEMS,
    Exploration,
    crystal_system,
    identity_key,
    normalize_system,
)

logger = get_logger("sim")

# symbol → (atomic number, magnetic moment in μB, price in USD/kg)
ELEMENTS: Dict[str, Tuple[int, float, float]] = {
    "B": (5, 0, 4), "C": (6, 0, 0.5), "N": (7, 0, 0.2), "Al": (13, 0, 2.5),
    "Si": (14, 0, 2), "P": (15, 0, 3), "S": (16, 0, 0.1), "Ti": (22, 0, 11),
    "V": (23, 0, 30), "Cr": (24, 0.6, 9), "Mn": (25, 2.4, 2), "Fe": (26, 2.2, 0.4),
    "Co": (27, 1.7, 30), "Ni": (28, 0.6, 16), "Cu": (29, 0, 9), "Zn": (30, 0, 3),
    "Ga": (31, 0, 300), "Ge": (32, 0, 1000), "Y": (39, 0, 30), "Zr": (40, 0, 35),
    "Nb": (41, 0, 60), "Mo": (42, 0, 40), "Rh": (45, 0, 150000),
    "Pd": (46, 0, 40000), "Ag": (47, 0, 800), "Sn": (50, 0, 25), "Sb": (51, 0, 12),
    "La": (57, 0, 5), "Ce": (58, 0.5, 5), "Nd": (60, 2.0, 70), "Sm": (62, 0.7, 14),
    "Gd": (64, 3.0, 30), "Tb": (65, 3.2, 1000), "Dy": (66, 3.4, 300),
    "Hf": (72, 0, 900), "Ta": (73, 0, 300), "W": (74, 0, 35),
    "Ir": (77, 0, 150000), "Pt": (78, 0, 30000), "Au": (79, 0, 60000),
    "Bi": (83, 0, 6),
}  # fmt: skip

# How strongly each crystal system supports a single easy axis.
_ANISOTROPY = {
    "tetragonal": 1.0,
    "hexagonal": 1.0,
    "trigonal": 0.7,
    "orthorhombic": 0.4,
    "monoclinic": 0.2,
    "triclinic": 0.2,
    "cubic": 0.03,
}

MAX_FORMULA_ATOMS = 12  # largest reduced formula the world contains
MAX_POLYMORPHS = 3

# Simulated (seconds, USD) per call, so the cost ledger has something to add up.
_EXPLORE_COST = (1800.0, 2.0)
_ROUTE_COST = {EXPENSIVE: (3 * 3600.0, 6.0)}
_DEFAULT_ROUTE_COST = (60.0, 0.02)

WORLD_NOTE = (
    "Every calculation in this campaign is answered by a simulator, not by "
    "real physics. Its chemistry is loosely realistic, but which element "
    "combinations help or hurt a property is invented, so literature values "
    "do not carry over: treat your prior knowledge as a first guess and trust "
    "what has been measured here. Only these elements exist: "
    + ", ".join(ELEMENTS)
    + f". Compounds have at most {MAX_FORMULA_ATOMS} atoms per formula unit."
)


@dataclass(frozen=True)
class Phase:
    """One near-hull structure of the world."""

    system: str
    counts: Tuple[Tuple[str, int], ...]
    space_group: int
    e_hull: float
    luck: float  # how hard the structure is to find; lower is easier

    @property
    def formula(self) -> str:
        return "".join(f"{el}{n if n > 1 else ''}" for el, n in self.counts)

    @property
    def num_atoms(self) -> int:
        return sum(n for _, n in self.counts)

    def fraction(self, element: str) -> float:
        return dict(self.counts).get(element, 0) / self.num_atoms


def _compositions(parts: int, total: int) -> Iterator[Tuple[int, ...]]:
    """Positive integer tuples of length ``parts`` summing to at most ``total``."""
    if parts == 0:
        yield ()
        return
    for first in range(1, total - parts + 2):
        for rest in _compositions(parts - 1, total - first):
            yield (first, *rest)


class SimWorld:
    """Ground truth for a seeded synthetic world. Stateless apart from charges."""

    def __init__(self, seed: int = 0, failure_rate: float = 0.0) -> None:
        self.seed = seed
        self.failure_rate = failure_rate
        self._attempts: Counter = Counter()
        self._charges: List[Dict[str, Any]] = []
        self.phases = lru_cache(maxsize=None)(self._phases)

    def _u(self, *key: Any) -> float:
        """Uniform [0, 1) number fixed by the seed and ``key``."""
        digest = hashlib.blake2b(repr((self.seed, key)).encode(), digest_size=8)
        return int.from_bytes(digest.digest(), "big") / 2**64

    # -- structures -------------------------------------------------------

    def _phases(self, system: str) -> Tuple[Phase, ...]:
        """Every structure that exists in a chemical system ("Co-Fe")."""
        elements = system.split("-")
        unknown = [e for e in elements if e not in ELEMENTS]
        if unknown:
            raise ValueError(f"elements {unknown} do not exist in the simulated world")

        phases = []
        for counts in _compositions(len(elements), MAX_FORMULA_ATOMS):
            if math.gcd(*counts) != 1:
                continue
            formula = tuple(zip(elements, counts))
            # Simple stoichiometries are far more likely to form a compound.
            excess = (sum(counts) - len(elements)) * max(1, len(elements) - 1)
            if self._u("exists", formula) > 0.8 * math.exp(-excess / 2.5):
                continue
            e_hull = 0.0
            for k in range(MAX_POLYMORPHS):
                if k == 0:
                    if self._u("hull", formula) > 0.35:
                        e_hull = -0.05 * math.log(1 - self._u("e0", formula))
                else:
                    if self._u("polymorph", formula, k) > 0.5:
                        break
                    e_hull += 0.01 + 0.06 * self._u("de", formula, k)
                phases.append(
                    Phase(
                        system=system,
                        counts=formula,
                        space_group=self._space_group(formula, k),
                        e_hull=round(e_hull, 4),
                        luck=self._u("luck", formula, k),
                    )
                )
        return tuple(sorted(phases, key=lambda p: p.e_hull))

    def _space_group(self, formula: Any, k: int) -> int:
        index = int(self._u("system", formula, k) * len(_CRYSTAL_SYSTEMS))
        lower = _CRYSTAL_SYSTEMS[index - 1][0] + 1 if index else 1
        upper = _CRYSTAL_SYSTEMS[index][0]
        return lower + int(self._u("sg", formula, k) * (upper - lower + 1))

    # -- properties -------------------------------------------------------

    def synergy(self, a: str, b: str) -> float:
        """Hidden pair effect: a fifth of pairs help, a fifth hurt."""
        pair = tuple(sorted((a, b)))
        kind, size = self._u("pair", pair), self._u("pair-size", pair)
        if kind < 0.2:
            return 0.3 + 0.5 * size
        if kind > 0.8:
            return -(0.3 + 0.3 * size)
        return 0.0

    def value(self, name: str, formula: str, space_group: int, e_hull: float) -> Any:
        """True value of a catalog property for one structure."""
        counts = {
            el: int(n or 1) for el, n in re.findall(r"([A-Z][a-z]?)(\d*)", formula)
        }
        total = sum(counts.values())
        x = {el: n / total for el, n in counts.items()}
        moment = sum(f * ELEMENTS[el][1] for el, f in x.items())
        pairs = list(combinations(sorted(x), 2))
        synergy = sum(self.synergy(a, b) for a, b in pairs) / max(1, len(pairs))
        jitter = 1 + 0.15 * (2 * self._u("jitter", name, formula, space_group) - 1)

        if name == "e_hull":
            return e_hull
        if name == "dynamic_stability":
            stable = 0.85 if e_hull < 0.05 else 0.5
            return self._u("phonons", formula, space_group) < stable
        if name == "cost":
            mass = {el: f * ELEMENTS[el][0] for el, f in x.items()}
            price = sum(m * ELEMENTS[el][2] for el, m in mass.items())
            return round(price / sum(mass.values()), 2)
        if name == "saturation_magnetization":
            return round(0.95 * moment * jitter, 3)
        if name == "curie_temperature":
            tc = 1050 * math.tanh(moment / 1.5) * (1 + 0.5 * synergy) * jitter
            return round(max(0.0, tc), 1)
        if name == "magnetic_anisotropy_energy":
            spin_orbit = sum(f * (ELEMENTS[el][0] / 83) ** 2 for el, f in x.items())
            axis = _ANISOTROPY[crystal_system(space_group)]
            sign = -1 if self._u("easy-plane", formula, space_group) < 0.25 else 1
            mae = 6 * axis * (moment / 2.2) * spin_orbit * (1 + synergy) * jitter
            return round(sign * mae, 3)
        raise KeyError(f"the simulated world has no property '{name}'")

    def measure(self, name: str, formula: str, space_group: int, e_hull: float) -> Any:
        """A route call: the true value, a charge, and sometimes a failure."""
        seconds, cost = _ROUTE_COST.get(CATALOG[name].tier, _DEFAULT_ROUTE_COST)
        self._attempts[(name, formula, space_group)] += 1
        attempt = self._attempts[(name, formula, space_group)]
        failed = self._u("fail", name, formula, space_group, attempt) < self.failure_rate
        self._charge(f"sim/{name}", seconds, cost, "error" if failed else "success")
        if failed:
            raise RuntimeError("simulated calculation failure")
        return self.value(name, formula, space_group, e_hull)

    def _charge(self, route: str, seconds: float, cost: float, status: str) -> None:
        self._charges.append(
            {
                "route": route,
                "action_id": None,
                "status": status,
                "cost_usd": cost,
                "cost_sats": 0,
                "seconds": seconds,
            }
        )

    def drain_charges(self) -> List[Dict[str, Any]]:
        charges, self._charges = self._charges, []
        return charges

    # -- oracle -----------------------------------------------------------

    def oracle(self, objective, max_elements: int = 3, top: int = 5) -> List[Dict[str, Any]]:
        """Best fully-measured structures in the world for an objective.

        Enumerates every allowed system of up to ``max_elements`` elements, so
        the first entry is what a perfect search would have found.
        """
        allowed = [e for e in ELEMENTS if e not in objective.forbidden_elements]
        names = [p for p in objective.properties() if CATALOG[p].route]
        best: List[Dict[str, Any]] = []
        for size in range(1, max_elements + 1):
            for elements in combinations(sorted(allowed), size):
                if objective.check_system(list(elements)):
                    continue
                for phase in self.phases("-".join(elements)):
                    properties = {
                        "num_atoms": phase.num_atoms,
                        "space_group": phase.space_group,
                        **{
                            n: self.value(
                                n, phase.formula, phase.space_group, phase.e_hull
                            )
                            for n in names
                        },
                    }
                    score, _ = objective.score(properties)
                    best.append(
                        {
                            "formula": phase.formula,
                            "system": phase.system,
                            "score": score,
                            "properties": properties,
                        }
                    )
            best = sorted(best, key=lambda b: -b["score"])[:top]
        return best


class SimExplorer:
    """Drop-in for ``SystemExplorer`` backed by a ``SimWorld``."""

    def __init__(
        self,
        world: SimWorld,
        max_atoms: int = 16,
        num_trials: int = 10,
        e_hull_cutoff: float = 0.15,
        max_candidates: int = 60,
    ) -> None:
        self.world = world
        self.max_atoms = max_atoms
        self.num_trials = num_trials
        self.e_hull_cutoff = e_hull_cutoff
        self.max_candidates = max_candidates

    def explore(
        self,
        chemical_system: str,
        crystal_systems: Optional[List[str]] = None,
        min_fraction: Optional[Dict[str, float]] = None,
        max_fraction: Optional[Dict[str, float]] = None,
        max_atoms: Optional[int] = None,
        num_trials: Optional[int] = None,
        skip_existing: bool = False,
    ) -> Exploration:
        system = normalize_system(chemical_system)
        max_atoms = max_atoms or self.max_atoms
        num_trials = num_trials or self.num_trials
        self.world._charge("sim/explore", *_EXPLORE_COST, "success")

        def in_bounds(fractions: Dict[str, float]) -> bool:
            return all(
                fractions[el] >= f for el, f in (min_fraction or {}).items()
            ) and all(fractions[el] <= f for el, f in (max_fraction or {}).items())

        elements = system.split("-")
        tried = sum(
            1
            for counts in _compositions(
                len(elements), min(max_atoms, MAX_FORMULA_ATOMS)
            )
            if math.gcd(*counts) == 1
            and in_bounds({el: n / sum(counts) for el, n in zip(elements, counts)})
        )
        # More trials turn up the structures that are harder to find.
        reach = 1 - 0.8**num_trials
        found = [
            p
            for p in self.world.phases(system)
            if p.e_hull <= self.e_hull_cutoff
            and p.num_atoms <= max_atoms
            and p.luck < reach
            and (
                not crystal_systems
                or crystal_system(p.space_group) in crystal_systems
            )
            and in_bounds({el: p.fraction(el) for el in elements})
        ][: self.max_candidates]

        candidates = []
        for phase in found:
            properties = {
                "e_hull": phase.e_hull,
                "num_atoms": phase.num_atoms,
                "space_group": phase.space_group,
            }
            candidates.append(
                {
                    "formula": phase.formula,
                    "cif": f"data_sim_{phase.formula}_sg{phase.space_group}",
                    "identity": identity_key(phase.formula, properties),
                    "origin": "this exploration",
                    "properties": properties,
                }
            )
        summary = {
            "stoichiometries_tried": tried,
            "structures_generated": tried * num_trials,
            "near_hull": len(candidates),
            "on_hull": sum(1 for p in found if p.e_hull <= 1e-6),
            "crystal_systems": dict(
                Counter(crystal_system(p.space_group) for p in found)
            ),
        }
        logger.info(f"{system}: {len(candidates)} simulated near-hull candidates")
        return Exploration(system, candidates, summary)


class SimEvaluator:
    """Drop-in for ``Evaluator`` backed by a ``SimWorld``."""

    def __init__(self, world: SimWorld) -> None:
        self.world = world

    def measure(self, candidate: Dict[str, Any], properties: List[str]) -> Measurement:
        known = candidate.get("properties") or {}
        result = Measurement(file_id=candidate.get("file_id"))
        for name in properties:
            spec = CATALOG.get(name)
            if not spec or not spec.route or known.get(name) is not None:
                continue
            try:
                result.values[name] = self.world.measure(
                    name,
                    candidate["formula"],
                    known["space_group"],
                    known.get("e_hull", 0.0),
                )
            except Exception as e:
                result.errors[name] = str(e)
        return result
