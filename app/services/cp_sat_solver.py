from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError as exc:  # pragma: no cover - import check
    raise ModuleNotFoundError(
        "Das Modul 'ortools' fehlt. Bitte 'pip install -r requirements.txt' oder 'pip install ortools' im Projekt-Venv ausführen."
    ) from exc

logger = logging.getLogger(__name__)


@dataclass
class TeacherSpec:
    id: int
    target: int
    prio_rank: int = 10
    preferred_floor: Optional[int] = None
    floor_weights: Optional[Dict[int, int]] = None
    day_periods: Dict[int, frozenset[int]] = field(default_factory=dict)
    availability_days: int = 0
    nominal_target: int = 0

    def has_adjacent_lesson(
        self,
        day_index: int,
        before_period: Optional[int],
        after_period: Optional[int],
    ) -> bool:
        periods = self.day_periods.get(day_index)
        if not periods:
            return False
        if before_period is not None and before_period in periods:
            return True
        if after_period is not None and after_period in periods:
            return True
        return False


@dataclass(frozen=True)
class BreakSlotSpec:
    slot_id: str
    date: date
    day_index: int
    break_index: int
    before_period: Optional[int]
    after_period: Optional[int]
    needs: Dict[int, int]


@dataclass(frozen=True)
class AssignmentDecision:
    teacher_id: int
    slot_id: str
    floor_id: int
    day_index: int
    date: date
    break_index: int


@dataclass
class SolverResult:
    status: str
    assignments: List[AssignmentDecision]
    loads: Dict[int, int]
    max_dev: int
    priority_cost: int
    total_dev: int
    daily_excess: int
    band_violation: int
    total_shortfall: int
    shortfalls: Dict[Tuple[str, int], int]
    status_enum: int
    wall_time_seconds: float


class BreakSupervisionSolver:
    """Encapsulates the CP-SAT formulation for the supervision plan."""

    def __init__(
        self,
        *,
        teachers: Iterable[TeacherSpec],
        break_slots: Iterable[BreakSlotSpec],
        floor_ids: Iterable[int],
        fairness_band: Optional[int] = 1,
        max_one_per_day: bool = False,
        time_limit_s: float = 30.0,
        num_workers: int = 8,
        band_penalty: int = 200_000,
        max_extra_duties: Optional[int] = None,
    ) -> None:
        self.teachers: List[TeacherSpec] = list(teachers)
        self.break_slots: List[BreakSlotSpec] = list(break_slots)
        self.floor_ids: List[int] = list(floor_ids)
        self.fairness_band = fairness_band if fairness_band is None or fairness_band >= 0 else 0
        self.max_one_per_day = max_one_per_day
        self.time_limit_s = max(1.0, time_limit_s)
        self.num_workers = max(1, num_workers)
        self.band_penalty = max(0, int(band_penalty))
        self.max_extra_duties = max_extra_duties if max_extra_duties is None else max(0, int(max_extra_duties))

        self.slot_lookup: Dict[str, BreakSlotSpec] = {slot.slot_id: slot for slot in self.break_slots}
        self.teacher_lookup: Dict[int, TeacherSpec] = {t.id: t for t in self.teachers}

        self.total_need: int = sum(
            max(0, need)
            for slot in self.break_slots
            for need in slot.needs.values()
        )
        self.max_target: int = max((max(0, t.target) for t in self.teachers), default=0)
        self.total_target: int = sum(max(0, t.target) for t in self.teachers)

        self._eligibility: Dict[Tuple[int, str], bool] = {}
        self.eligible_counts: Dict[Tuple[str, int], int] = {}
        self._compute_eligibility()
        if self.total_target < self.total_need:
            logger.warning(
                "Gesamtsoll (%s) liegt unter dem Gesamtbedarf (%s). Planung kann dadurch scheitern oder Restriktionen verletzen.",
                self.total_target,
                self.total_need,
            )

    def _compute_eligibility(self) -> None:
        for teacher in self.teachers:
            teacher.target = max(0, int(teacher.target))

        for slot in self.break_slots:
            eligible_teachers = 0
            for teacher in self.teachers:
                is_eligible = teacher.has_adjacent_lesson(slot.day_index, slot.before_period, slot.after_period)
                self._eligibility[(teacher.id, slot.slot_id)] = is_eligible
                if is_eligible:
                    eligible_teachers += 1
            for floor_id, need in slot.needs.items():
                if need > 0:
                    self.eligible_counts[(slot.slot_id, floor_id)] = eligible_teachers

    def eligibility(self, teacher_id: int, slot_id: str) -> bool:
        return self._eligibility.get((teacher_id, slot_id), False)

    def _priority_cost(self, teacher: TeacherSpec, floor_id: int) -> int:
        if teacher.floor_weights:
            default_penalty = max(teacher.floor_weights.values(), default=3) + 1
            base = int(teacher.floor_weights.get(floor_id, default_penalty))
        else:
            if teacher.preferred_floor is None:
                base = 1
            elif teacher.preferred_floor == floor_id:
                base = 0
            else:
                base = 3

        prio = teacher.prio_rank if teacher.prio_rank is not None else 10
        prio = max(0, prio)
        multiplier = 100 + min(prio, 100)
        return base * multiplier

    def solve(self) -> SolverResult:
        if not self.teachers:
            logger.warning("Keine Lehrkräfte für die Planung vorhanden.")
            status = cp_model.INFEASIBLE if self.total_need > 0 else cp_model.OPTIMAL
            return SolverResult(
                status="INFEASIBLE" if status == cp_model.INFEASIBLE else "OPTIMAL",
                assignments=[],
                loads={},
                max_dev=0,
                priority_cost=0,
                total_dev=0,
                daily_excess=0,
                band_violation=0,
                total_shortfall=self.total_need,
                shortfalls={},
                status_enum=status,
                wall_time_seconds=0.0,
            )

        model = cp_model.CpModel()

        decision_vars: Dict[Tuple[int, str, int], cp_model.IntVar] = {}
        teacher_slot_vars: Dict[Tuple[int, str], List[cp_model.IntVar]] = {}
        teacher_vars: Dict[int, List[cp_model.IntVar]] = {t.id: [] for t in self.teachers}
        day_assignment_vars: Dict[Tuple[int, date], List[cp_model.IntVar]] = {}
        day_excess_terms: List[cp_model.IntVar] = []
        priority_terms: List[cp_model.IntVar] = []
        max_cost = 0

        shortfall_vars: Dict[Tuple[str, int], cp_model.IntVar] = {}
        shortfall_terms: List[cp_model.IntVar] = []

        for slot in self.break_slots:
            eligible_teachers = [t for t in self.teachers if self.eligibility(t.id, slot.slot_id)]
            for floor_id, need in slot.needs.items():
                if need <= 0:
                    continue
                floor_vars: List[cp_model.IntVar] = []
                for teacher in eligible_teachers:
                    var = model.NewBoolVar(f"x_t{teacher.id}_s{slot.slot_id}_f{floor_id}")
                    decision_vars[(teacher.id, slot.slot_id, floor_id)] = var
                    floor_vars.append(var)

                    teacher_slot_vars.setdefault((teacher.id, slot.slot_id), []).append(var)
                    teacher_vars[teacher.id].append(var)
                    day_assignment_vars.setdefault((teacher.id, slot.date), []).append(var)

                    cost = self._priority_cost(teacher, floor_id)
                    if cost:
                        priority_terms.append(var * cost)
                    if cost > max_cost:
                        max_cost = cost

                shortfall = model.NewIntVar(0, need, f"short_{slot.slot_id}_{floor_id}")
                shortfall_vars[(slot.slot_id, floor_id)] = shortfall
                shortfall_terms.append(shortfall)
                model.Add(sum(floor_vars) + shortfall == need)

        for vars_in_slot in teacher_slot_vars.values():
            model.Add(sum(vars_in_slot) <= 1)

        total_need = self.total_need
        deviation_cap = total_need + self.max_target
        max_dev = model.NewIntVar(0, deviation_cap, "max_dev")

        dev_pos: Dict[int, cp_model.IntVar] = {}
        dev_neg: Dict[int, cp_model.IntVar] = {}
        load_vars: Dict[int, cp_model.IntVar] = {}
        band_under_terms: List[cp_model.IntVar] = []
        band_over_terms: List[cp_model.IntVar] = []

        for teacher in self.teachers:
            load_var = model.NewIntVar(0, total_need, f"load_{teacher.id}")
            load_vars[teacher.id] = load_var
            model.Add(load_var == sum(teacher_vars[teacher.id])) if teacher_vars[teacher.id] else model.Add(load_var == 0)

            dev_pos_var = model.NewIntVar(0, total_need, f"dev_pos_{teacher.id}")
            dev_neg_var = model.NewIntVar(0, self.max_target, f"dev_neg_{teacher.id}")
            dev_pos[teacher.id] = dev_pos_var
            dev_neg[teacher.id] = dev_neg_var

            model.Add(load_var - teacher.target == dev_pos_var - dev_neg_var)
            model.Add(max_dev >= dev_pos_var)
            model.Add(max_dev >= dev_neg_var)

            if self.fairness_band is not None:
                min_load = max(0, teacher.target - self.fairness_band)
                max_load = teacher.target + self.fairness_band
                under = model.NewIntVar(0, total_need, f"band_under_{teacher.id}")
                over = model.NewIntVar(0, total_need, f"band_over_{teacher.id}")
                model.Add(load_var + under >= min_load)
                model.Add(load_var - over <= max_load)
                model.Add(under >= 0)
                model.Add(over >= 0)
                if self.max_extra_duties is not None:
                    model.Add(over <= self.max_extra_duties)
                band_under_terms.append(under)
                band_over_terms.append(over)
            else:
                if self.max_extra_duties is not None:
                    cap = teacher.target + self.max_extra_duties
                    over = model.NewIntVar(0, total_need, f"band_over_{teacher.id}")
                    model.Add(load_var - over <= cap)
                    model.Add(over >= 0)
                    model.Add(over <= self.max_extra_duties)
                    band_over_terms.append(over)

            if self.max_extra_duties is not None:
                extra_cap = teacher.target + (self.fairness_band or 0) + self.max_extra_duties
                if extra_cap >= 0:
                    cap_over = model.NewIntVar(0, total_need, f"cap_over_{teacher.id}")
                    model.Add(load_var <= extra_cap + cap_over)
                    band_over_terms.append(cap_over)

        if priority_terms:
            priority_upper = max_cost * total_need if max_cost else total_need
        else:
            priority_upper = 0
        P = model.NewIntVar(0, priority_upper, "priority_cost")
        if priority_terms:
            model.Add(P == sum(priority_terms))
        else:
            model.Add(P == 0)

        total_dev = model.NewIntVar(0, len(self.teachers) * deviation_cap * 5, "total_dev")
        total_dev_terms = []
        for teacher in self.teachers:
            over_weight = 1
            under_weight = max(1, teacher.availability_days or 1)
            total_dev_terms.append(dev_pos[teacher.id] * over_weight)
            total_dev_terms.append(dev_neg[teacher.id] * under_weight)
        model.Add(total_dev == sum(total_dev_terms))

        if band_under_terms or band_over_terms:
            total_band_violation = model.NewIntVar(0, len(self.teachers) * deviation_cap, "total_band_violation")
            model.Add(total_band_violation == sum(band_under_terms + band_over_terms))
        else:
            total_band_violation = model.NewIntVar(0, 0, "total_band_violation")
            model.Add(total_band_violation == 0)

        if shortfall_terms:
            total_shortfall = model.NewIntVar(0, self.total_need, "total_shortfall")
            model.Add(total_shortfall == sum(shortfall_terms))
        else:
            total_shortfall = model.NewIntVar(0, 0, "total_shortfall")
            model.Add(total_shortfall == 0)

        daily_excess_total: cp_model.IntVar
        if day_assignment_vars:
            for (teacher_id, duty_date), vars_for_day in day_assignment_vars.items():
                if not vars_for_day:
                    continue
                max_for_day = len(vars_for_day)
                day_load = model.NewIntVar(0, max_for_day, f"day_load_t{teacher_id}_{duty_date}")
                model.Add(day_load == sum(vars_for_day))

                day_over = model.NewBoolVar(f"day_over_t{teacher_id}_{duty_date}")
                model.Add(day_load <= 1 + day_over * max_for_day)
                model.Add(day_load <= 1).OnlyEnforceIf(day_over.Not())
                if max_for_day >= 2:
                    model.Add(day_load >= 2).OnlyEnforceIf(day_over)

                day_excess = model.NewIntVar(0, max(0, max_for_day - 1), f"day_excess_t{teacher_id}_{duty_date}")
                model.Add(day_excess == 0).OnlyEnforceIf(day_over.Not())
                if max_for_day >= 2:
                    model.Add(day_excess + 1 == day_load).OnlyEnforceIf(day_over)
                else:
                    model.Add(day_excess == 0).OnlyEnforceIf(day_over)

                day_excess_terms.append(day_excess)

            if day_excess_terms:
                daily_excess_total = model.NewIntVar(0, total_need, "daily_excess_total")
                model.Add(daily_excess_total == sum(day_excess_terms))
            else:
                daily_excess_total = model.NewIntVar(0, 0, "daily_excess_total")
                model.Add(daily_excess_total == 0)
        else:
            daily_excess_total = model.NewIntVar(0, 0, "daily_excess_total")
            model.Add(daily_excess_total == 0)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_s
        solver.parameters.num_search_workers = self.num_workers

        # Phase 1: minimize total shortfall -> deckt Bedarf bestmöglich
        model.Minimize(total_shortfall)
        status_phase1 = solver.Solve(model)
        status_name_phase1 = solver.StatusName(status_phase1)
        if status_phase1 not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            self._log_infeasibility()
            return SolverResult(
                status=status_name_phase1,
                assignments=[],
                loads={teacher.id: 0 for teacher in self.teachers},
                max_dev=0,
                priority_cost=0,
                total_dev=0,
                daily_excess=0,
                band_violation=0,
                total_shortfall=self.total_need,
                shortfalls={},
                status_enum=status_phase1,
                wall_time_seconds=solver.wall_time,
            )

        best_shortfall = int(solver.Value(total_shortfall))
        model.Add(total_shortfall == best_shortfall)

        # Phase 2: Prioritätskosten unter fixiertem Shortfall minimieren
        model.Minimize(P)
        status_phase2 = solver.Solve(model)
        status_name_phase2 = solver.StatusName(status_phase2)
        if status_phase2 not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            logger.warning("Prioritätsoptimierung konnte nicht abgeschlossen werden (Status: %s). Ergebnis aus Phase 1 wird verwendet.", status_name_phase2)
            best_priority = 0
        else:
            best_priority = int(solver.Value(P))
            model.Add(P == best_priority)

        # Phase 3: Fairness unter optimalem Shortfall und Priorität
        solver2 = cp_model.CpSolver()
        solver2.parameters.max_time_in_seconds = self.time_limit_s
        solver2.parameters.num_search_workers = self.num_workers

        weight_max_dev = 1_000_000
        weight_total_dev = 10_000
        weight_daily_excess = 500_000 if self.max_one_per_day else 100
        weight_band_violation = self.band_penalty if self.fairness_band is not None or self.max_extra_duties is not None else 0
        weight_shortfall = 50_000_000
        model.Minimize(
            max_dev * weight_max_dev
            + total_dev * weight_total_dev
            + daily_excess_total * weight_daily_excess
            + total_band_violation * weight_band_violation
            + total_shortfall * weight_shortfall
        )

        status2 = solver2.Solve(model)
        status_name2 = solver2.StatusName(status2)

        if status2 not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            logger.warning("Fairness-Optimierung konnte nicht abgeschlossen werden (Status: %s). Ergebnis aus voriger Phase wird verwendet.", status_name2)
            solver2 = solver
            status2 = status_phase2
            status_name2 = status_name_phase2

        assignments: List[AssignmentDecision] = []
        for (teacher_id, slot_id, floor_id), var in decision_vars.items():
            if solver2.Value(var):
                slot = self.slot_lookup[slot_id]
                assignments.append(
                    AssignmentDecision(
                        teacher_id=teacher_id,
                        slot_id=slot_id,
                        floor_id=floor_id,
                        day_index=slot.day_index,
                        date=slot.date,
                        break_index=slot.break_index,
                    )
                )

        loads = {teacher.id: solver2.Value(load_vars[teacher.id]) for teacher in self.teachers}
        max_dev_value = solver2.Value(max_dev)
        total_dev_value = solver2.Value(total_dev)
        daily_excess_value = solver2.Value(daily_excess_total)
        band_violation_value = solver2.Value(total_band_violation)
        total_shortfall_value = solver2.Value(total_shortfall)
        shortfall_values = {
            key: solver2.Value(var)
            for key, var in shortfall_vars.items()
            if solver2.Value(var) > 0
        }

        return SolverResult(
            status=status_name2,
            assignments=assignments,
            loads=loads,
            max_dev=max_dev_value,
            priority_cost=best_priority,
            total_dev=total_dev_value,
            daily_excess=daily_excess_value,
            band_violation=band_violation_value,
            total_shortfall=total_shortfall_value,
            shortfalls=shortfall_values,
            status_enum=status2,
            wall_time_seconds=solver2.wall_time,
        )

    def _log_infeasibility(self) -> None:
        for (slot_id, floor_id), eligible_count in self.eligible_counts.items():
            slot = self.slot_lookup.get(slot_id)
            if not slot:
                continue
            need = slot.needs.get(floor_id, 0)
            if need > eligible_count:
                logger.error(
                    "Slot %s (Tag %s, Pause %s, Stockwerk %s): Bedarf %s > verfügbare Lehrkräfte %s.",
                    slot.date,
                    slot.day_index,
                    slot.break_index,
                    floor_id,
                    need,
                    eligible_count,
                )
