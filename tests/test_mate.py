"""Offline tests for everything with a right answer.

No network: these cover the arithmetic and the ordering, which are the parts
the agent is forbidden from doing in its head.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mate import constraints as cons
from mate import costs, sms, tolls
from mate.cluster import infer_mode
from mate.geo import CAR, FOOT, Matrix, haversine, point_to_path_km
from mate.ordering import order_day, to_hhmm, to_min
from mate.plan_state import read_state, write_state
from mate.schema import (ESTIMATED, MEASURED, Coord, Intake, Provenance, Stop,
                         TripState, state_from_dict, to_dict)
from mate.settings import Settings
from mate.text import fold

S = Settings()
RIO = Coord(-22.9068, -43.1729)
PETRO = Coord(-22.5076, -43.1785)


def stop(sid, lat, lon, kind="sight", dwell=60):
    return Stop(id=sid, name=sid, lat=lat, lon=lon, kind=kind, dwell_min=dwell)


def matrix_from(coords, speed_kmh=30.0):
    dist = [[haversine(a, b) for b in coords] for a in coords]
    return Matrix(
        distances_km=dist,
        durations_min=[[(d / speed_kmh) * 60 for d in row] for row in dist],
        provenance=Provenance(MEASURED, "test"),
    )


class TestCosts(unittest.TestCase):
    def test_fuel_is_distance_over_consumption_times_price(self):
        brl, liters = costs.fuel_brl(110.0, S)
        self.assertAlmostEqual(liters, 10.0, places=2)
        self.assertAlmostEqual(brl, 62.0, places=2)

    def test_car_cost_adds_tolls(self):
        c = costs.car_cost(110.0, 90.0, 36.60, S)
        self.assertAlmostEqual(c.cost_brl, 98.60, places=2)

    def test_walking_over_the_ceiling_warns(self):
        self.assertTrue(costs.foot_cost(12.0, 150.0, S).note)
        self.assertFalse(costs.foot_cost(3.0, 40.0, S).note)

    def test_ridehail_is_never_sold_as_a_quote(self):
        c = costs.ridehail_cost(10.0, 20.0, S)
        self.assertEqual(c.provenance.source, ESTIMATED)
        self.assertTrue(c.note)

    def test_ridehail_respects_minimum(self):
        self.assertEqual(costs.ridehail_cost(0.1, 1.0, S).cost_brl, S.ridehail_minimum_brl)

    def test_food_scales_with_party_and_days(self):
        total, _ = costs.food_cost(4, 2, S)
        self.assertAlmostEqual(total, 4 * 2 * S.food_brl_per_person_per_day, places=2)


class TestTolls(unittest.TestCase):
    def test_serves_fallback_finds_br040_for_petropolis(self):
        found, _ = tolls.tolls_on_route([RIO, PETRO], destination="Petrópolis, RJ",
                                        geometry_measured=False)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(tolls.total_brl(found), 36.60, places=2)

    def test_unaccented_osm_town_still_matches(self):
        found, _ = tolls.tolls_on_route([RIO, PETRO], destination="petropolis",
                                        geometry_measured=False)
        self.assertTrue(found)

    def test_one_way_is_half_of_round_trip(self):
        there, _ = tolls.tolls_on_route([RIO, PETRO], destination="Petrópolis",
                                        round_trip=False, geometry_measured=False)
        self.assertAlmostEqual(tolls.total_brl(there), 18.30, places=2)

    def test_unrelated_destination_has_no_tolls(self):
        found, _ = tolls.tolls_on_route([RIO, PETRO], destination="Búzios",
                                        geometry_measured=False)
        self.assertEqual(found, [])

    def test_geometry_match_needs_the_road(self):
        through_xerem = [RIO, Coord(-22.5386, -43.3069), PETRO]
        found, _ = tolls.tolls_on_route(through_xerem, geometry_measured=True)
        self.assertTrue(found)


class TestGeometry(unittest.TestCase):
    def test_haversine_rio_to_petropolis(self):
        self.assertAlmostEqual(haversine(RIO, PETRO), 44.4, delta=1.0)

    def test_point_to_path(self):
        path = [Coord(0, 0), Coord(0, 1)]
        self.assertLess(point_to_path_km(Coord(0, 0.5), path), 0.01)
        self.assertGreater(point_to_path_km(Coord(1, 0.5), path), 100)


class TestModeInference(unittest.TestCase):
    def test_tight_cluster_is_walkable(self):
        base = Coord(-22.5050, -43.1790)
        stops = [stop("a", -22.5060, -43.1800), stop("b", -22.5070, -43.1780)]
        mode, spread, why = infer_mode(stops, base, has_car=True, s=S)
        self.assertEqual(mode, FOOT)
        self.assertLess(spread, S.foot_cluster_max_km)
        self.assertIn("pé", why)

    def test_spread_out_day_needs_wheels(self):
        base = Coord(-22.5050, -43.1790)
        stops = [stop("a", -22.4000, -43.1000), stop("b", -22.6000, -43.3000)]
        mode, _, _ = infer_mode(stops, base, has_car=True, s=S)
        self.assertEqual(mode, CAR)

    def test_no_car_falls_to_ridehail_not_walking(self):
        base = Coord(-22.5050, -43.1790)
        stops = [stop("a", -22.4000, -43.1000)]
        mode, _, _ = infer_mode(stops, base, has_car=False, s=S)
        self.assertEqual(mode, "ridehail")


class TestOrdering(unittest.TestCase):
    def test_does_not_zigzag(self):
        base = Coord(0.0, 0.0)
        # Handed over in the worst order. Collinear stops have two optimal
        # answers — out and back — so what matters is that neither doubles back.
        far, mid, near = stop("far", 0.0, 0.30), stop("mid", 0.0, 0.20), stop("near", 0.0, 0.10)
        m = matrix_from([base] + [Coord(s.lat, s.lon) for s in (far, near, mid)])
        result = order_day([far, near, mid], m, S, mode=CAR, start_time="09:00")
        order = [s.id for s in result.stops]
        self.assertIn(order, (["near", "mid", "far"], ["far", "mid", "near"]))

    def test_beats_the_naive_input_order(self):
        base = Coord(0.0, 0.0)
        far, mid, near = stop("far", 0.0, 0.30), stop("mid", 0.0, 0.20), stop("near", 0.0, 0.10)
        stops = [far, near, mid]
        m = matrix_from([base] + [Coord(s.lat, s.lon) for s in stops])
        naive = sum(m.distances_km[a][b] for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)))
        self.assertLess(order_day(stops, m, S, mode=CAR).distance_km, naive)

    def test_every_stop_gets_a_clock_time(self):
        base = Coord(0.0, 0.0)
        stops = [stop("a", 0.0, 0.05), stop("b", 0.0, 0.10)]
        m = matrix_from([base] + [Coord(s.lat, s.lon) for s in stops])
        result = order_day(stops, m, S, mode=CAR, start_time="09:00")
        for s in result.stops:
            self.assertRegex(s.arrive, r"^\d{2}:\d{2}$")
            self.assertGreater(to_min(s.depart), to_min(s.arrive))

    def test_legs_start_and_end_at_the_base(self):
        base = Coord(0.0, 0.0)
        stops = [stop("a", 0.0, 0.05), stop("b", 0.0, 0.10)]
        m = matrix_from([base] + [Coord(s.lat, s.lon) for s in stops])
        result = order_day(stops, m, S, mode=CAR, start_time="09:00")
        self.assertEqual(result.legs[0].from_id, "base")
        self.assertEqual(result.legs[-1].to_id, "base")
        self.assertEqual(len(result.legs), len(stops) + 1)

    def test_meal_lands_inside_a_window(self):
        base = Coord(0.0, 0.0)
        stops = [stop("sight1", 0.0, 0.02), stop("lunch", 0.0, 0.04, kind="meal", dwell=75),
                 stop("sight2", 0.0, 0.06)]
        m = matrix_from([base] + [Coord(s.lat, s.lon) for s in stops])
        result = order_day(stops, m, S, mode=CAR, start_time="09:00")
        meal = next(s for s in result.stops if s.kind == "meal")
        self.assertGreaterEqual(to_min(meal.arrive), to_min(S.lunch_window_start))
        self.assertLessEqual(to_min(meal.arrive), to_min(S.dinner_window_end))

    def test_empty_day_is_not_a_crash(self):
        self.assertEqual(order_day([], matrix_from([Coord(0, 0)]), S, mode=FOOT).stops, [])

    def test_hhmm_round_trip(self):
        self.assertEqual(to_hhmm(to_min("14:35")), "14:35")


class TestSchema(unittest.TestCase):
    def test_state_survives_a_json_round_trip(self):
        state = TripState(intake=Intake(
            origin="Rio de Janeiro, RJ", region="Serra", start_date="2026-10-31",
            end_date="2026-11-01", party_size=2, budget_total_brl=2000.0,
            towns=["Petrópolis, RJ", "Teresópolis, RJ"],
        ))
        back = state_from_dict(to_dict(state))
        self.assertEqual(back.intake.towns, state.intake.towns)
        self.assertEqual(back.intake.party_size, 2)
        self.assertEqual(back.intake.nights, 1)

    def test_optional_coord_inflates(self):
        back = state_from_dict(to_dict(TripState(intake=Intake(origin_coord=RIO))))
        self.assertAlmostEqual(back.intake.origin_coord.lat, RIO.lat)

    def test_unknown_keys_are_ignored(self):
        self.assertIsInstance(state_from_dict({"version": 1, "nonsense": 42}), TripState)

    def test_locking_hides_the_other_options(self):
        from mate.schema import PlanOption
        state = TripState(options=[PlanOption(id="a"), PlanOption(id="b")])
        self.assertEqual(len(state.visible_options()), 2)
        state.selected_option_id = "b"
        self.assertEqual([o.id for o in state.visible_options()], ["b"])


class TestPersistence(unittest.TestCase):
    def test_write_then_read(self):
        import tempfile
        state = TripState(intake=Intake(region="Petrópolis", start_date="2026-10-31",
                                        end_date="2026-11-01"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nested" / "plan.json"
            write_state(path, state)
            self.assertEqual(read_state(path).intake.region, "Petrópolis")


class TestText(unittest.TestCase):
    def test_fold_ignores_accents_and_case(self):
        self.assertEqual(fold("Petrópolis"), fold("PETROPOLIS"))
        self.assertEqual(fold("Cachoeira Véu de Noiva"), fold("Cachoeira Veu de Noiva"))

    def test_fold_separates_real_differences(self):
        self.assertNotEqual(fold("Petrópolis"), fold("Teresópolis"))


class TestOptionLabels(unittest.TestCase):
    """An option may only claim an axis it is actually the best of the set on."""

    def setUp(self):
        from mate.planner import _only_honest_labels
        from mate.schema import CostBreakdown, PlanOption
        self.fn = _only_honest_labels
        self.rankers = {
            "cheapest": lambda p: (p.costs.total_brl, p.total_travel_min),
            "least_travel": lambda p: (p.total_travel_min, p.costs.total_brl),
            "most_to_see": lambda p: (-p.stop_count, p.costs.total_brl),
        }
        self.make = lambda i, axis, total, travel, stops: PlanOption(
            id=i, axis=axis, costs=CostBreakdown(total_brl=total),
            total_travel_min=travel, stop_count=stops,
        )

    def test_drops_an_option_that_does_not_own_its_axis(self):
        # "a" is both the cheapest and the least travel, so "b" is lying.
        a = self.make("a", "cheapest", 900.0, 200.0, 10)
        b = self.make("b", "least_travel", 950.0, 230.0, 10)
        kept = self.fn([a, b], self.rankers)
        self.assertEqual([o.id for o in kept], ["a"])

    def test_keeps_genuinely_different_options(self):
        a = self.make("a", "cheapest", 900.0, 300.0, 8)
        b = self.make("b", "least_travel", 980.0, 200.0, 8)
        c = self.make("c", "most_to_see", 1000.0, 320.0, 14)
        kept = self.fn([a, b, c], self.rankers)
        self.assertEqual({o.id for o in kept}, {"a", "b", "c"})

    def test_never_returns_nothing(self):
        a = self.make("a", "cheapest", 900.0, 200.0, 10)
        b = self.make("b", "least_travel", 950.0, 230.0, 9)
        c = self.make("c", "most_to_see", 960.0, 240.0, 9)
        self.assertTrue(self.fn([a, b, c], self.rankers))


class TestNearDuplicateOptions(unittest.TestCase):
    """Two stays in one town fifty centavos apart are one option shown twice."""

    def setUp(self):
        from mate.planner import _drop_near_duplicates
        from mate.schema import CostBreakdown, Lodging, PlanOption
        self.fn = _drop_near_duplicates
        self.make = lambda i, city, total, stops=12: PlanOption(
            id=i, base=Lodging(name=i, city=city),
            costs=CostBreakdown(total_brl=total), stop_count=stops,
        )

    def test_same_town_and_same_price_collapses(self):
        a = self.make("Casablanca Palace", "Petrópolis", 937.54)
        b = self.make("Casablanca Imperial", "Petrópolis", 938.09)
        self.assertEqual([o.id for o in self.fn([a, b])], ["Casablanca Palace"])

    def test_different_towns_both_survive(self):
        a = self.make("Igarapé", "Miguel Pereira", 897.09)
        b = self.make("Casablanca", "Petrópolis", 937.54)
        self.assertEqual(len(self.fn([a, b])), 2)

    def test_same_town_but_a_real_price_gap_survives(self):
        a = self.make("Simples", "Petrópolis", 900.0)
        b = self.make("Chique", "Petrópolis", 1400.0)
        self.assertEqual(len(self.fn([a, b])), 2)

    def test_same_town_same_price_but_much_more_to_see_survives(self):
        a = self.make("A", "Petrópolis", 930.0, stops=8)
        b = self.make("B", "Petrópolis", 931.0, stops=13)
        self.assertEqual(len(self.fn([a, b])), 2)


class TestPlanMessages(unittest.TestCase):
    """The first reply is a plan, not a menu."""

    def _state(self):
        from mate.schema import (CostBreakdown, DayPlan, Intake, Lodging,
                                 PlanOption, TripState)
        day = DayPlan(date="2026-10-31", travel_within="foot",
                      mode_reason="paradas a até 1,2 km — dá para fazer a pé",
                      stops=[Stop(id="s1", name="Museu Imperial", arrive="10:00", dwell_min=60)])
        opt = PlanOption(id="a", label="Mais barato", why="menor custo total",
                         base=Lodging(name="Pousada X", city="Petrópolis"),
                         days=[day], stop_count=1,
                         costs=CostBreakdown(total_brl=900.0, per_person_brl=450.0,
                                             budget_brl=2000.0, within_budget=True))
        return TripState(intake=Intake(region="Serra", start_date="2026-10-31",
                                       end_date="2026-11-01", party_size=2,
                                       budget_total_brl=2000.0), options=[opt])

    def test_names_the_attractions(self):
        body = "\n".join(sms.plan_messages(self._state()))
        self.assertIn("Museu Imperial", body)
        self.assertIn("10:00", body)

    def test_leads_with_the_recommendation_and_its_total(self):
        first = sms.plan_messages(self._state())[0]
        self.assertIn("Pousada X", first)
        self.assertIn("R$ 900,00", first)

    def test_survives_having_no_options(self):
        from mate.schema import TripState
        self.assertTrue(sms.plan_messages(TripState()))


class TestExclusions(unittest.TestCase):
    """"Tira as cachoeiras" has to reach a stop named Cascata and tagged waterfall."""

    def _pois(self):
        return [
            Stop(id="1", name="Cachoeira Véu de Noiva", category="waterfall"),
            Stop(id="2", name="Cascata Fischer", category="waterfall"),
            Stop(id="3", name="Poço Dois Irmãos", category="waterfall"),
            Stop(id="4", name="Castelo Montebello", category="castle"),
            Stop(id="5", name="Museu Imperial", category="museum"),
        ]

    def _filters(self, *terms):
        f = cons.Filters()
        f.exclude.extend(terms)
        return f

    def test_cachoeira_removes_every_waterfall_however_it_is_named(self):
        kept = cons.filter_pois(self._pois(), self._filters("cachoeira"))
        self.assertEqual([s.id for s in kept], ["4", "5"])

    def test_exclusion_ignores_accents_and_case(self):
        kept = cons.filter_pois(self._pois(), self._filters("MUSEU"))
        self.assertNotIn("5", [s.id for s in kept])

    def test_unknown_term_still_matches_literally(self):
        kept = cons.filter_pois(self._pois(), self._filters("Montebello"))
        self.assertNotIn("4", [s.id for s in kept])

    def test_no_exclusions_keeps_everything(self):
        self.assertEqual(len(cons.filter_pois(self._pois(), cons.Filters())), 5)


class TestDayDeduping(unittest.TestCase):
    """A national park mapped at two entrances is one place to visit."""

    def setUp(self):
        from mate.cluster import _unique_across_trip
        self.fn = _unique_across_trip

    def test_not_twice_in_one_day(self):
        day = [
            Stop(id="a", name="Parque Nacional da Serra dos Órgãos", lat=-22.45, lon=-43.00),
            Stop(id="b", name="Poço Dois Irmãos", lat=-22.44, lon=-43.01),
            Stop(id="c", name="Parque Nacional da Serra dos Orgaos", lat=-22.49, lon=-43.05),
        ]
        self.assertEqual([s.id for s in self.fn([day])[0]], ["a", "b"])

    def test_not_again_on_another_day(self):
        d1 = [Stop(id="a", name="Parque Nacional da Serra dos Órgãos"),
              Stop(id="b", name="Olicio's")]
        d2 = [Stop(id="c", name="Parque Nacional da Serra dos Órgãos"),
              Stop(id="d", name="Olicio's"),
              Stop(id="e", name="Mirante Borandá")]
        out = self.fn([d1, d2])
        self.assertEqual([s.id for s in out[0]], ["a", "b"])
        self.assertEqual([s.id for s in out[1]], ["e"])


class TestCli(unittest.TestCase):
    def setUp(self):
        from mate.cli import build_parser
        self.parser = build_parser()

    def test_region_is_optional_when_a_town_is_given(self):
        """The agent dropped --region on a single-town call and argparse
        rejected it. It is a label, so it must not be able to fail a call."""
        a = self.parser.parse_args([
            "plan", "--origin", "Rio de Janeiro, RJ", "--town", "Teresópolis, RJ",
            "--start", "2026-10-31", "--end", "2026-11-02",
        ])
        self.assertEqual(a.region, "")
        self.assertEqual(a.town, ["Teresópolis, RJ"])

    def test_towns_accumulate(self):
        a = self.parser.parse_args([
            "plan", "--origin", "Rio", "--town", "A", "--town", "B",
            "--start", "2026-10-31", "--end", "2026-11-01",
        ])
        self.assertEqual(a.town, ["A", "B"])

    def test_replanning_over_an_existing_plan_is_refused(self):
        """The agent imitates its own earlier calls, so it will reach for `plan`
        again after a plan exists. That silently discards every constraint."""
        import io, json, tempfile
        from contextlib import redirect_stdout
        from mate.cli import main
        from mate.schema import PlanOption, TripState
        from mate.plan_state import write_state

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.json"
            state = TripState(options=[PlanOption(id="a")])
            cons.add(state, "exclude", "museu", "nada de museu")
            write_state(path, state)

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["plan", "--origin", "Rio", "--town", "Teresópolis, RJ",
                             "--start", "2026-10-31", "--end", "2026-11-02",
                             "--out", str(path)])
            self.assertEqual(code, 2)
            out = json.loads(buf.getvalue())
            self.assertFalse(out["ok"])
            self.assertIn("mate edit", out["use_instead"])
            self.assertIn("nada de museu", out["would_discard"])

    def test_force_allows_starting_over(self):
        from mate.cli import build_parser
        a = build_parser().parse_args([
            "plan", "--origin", "Rio", "--town", "X", "--start", "2026-10-31",
            "--end", "2026-11-02", "--force",
        ])
        self.assertTrue(a.force)

    def test_every_command_the_skills_invoke_is_valid(self):
        """A skill telling the agent to run a flag the CLI dropped fails only
        in production, as an argparse usage dump in somebody's chat."""
        import shlex
        checked = 0
        for skill in sorted(Path(__file__).resolve().parent.parent.rglob("skills/**/SKILL.md")):
            for block in re.findall(r"```\n(.*?)```", skill.read_text(encoding="utf-8"), re.S):
                joined = " ".join(l.strip().rstrip("\\") for l in block.strip().splitlines())
                if "bin/mate" not in joined:
                    continue
                argv = shlex.split(joined)[1:]
                with self.subTest(skill=skill.parent.name, cmd=" ".join(argv[:2])):
                    self.parser.parse_args(argv)
                checked += 1
        self.assertGreater(checked, 0, "no mate commands found in the skills")


class TestSms(unittest.TestCase):
    def test_brl_uses_brazilian_notation(self):
        self.assertEqual(sms.brl(1234.5), "R$ 1.234,50")

    def test_long_message_splits_on_a_line_break(self):
        chunks = sms._fit(["\n".join(f"linha {i}" for i in range(2000))])
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= sms.SMS_LIMIT for c in chunks))

    def test_duration_reads_like_a_person_wrote_it(self):
        self.assertEqual(sms.dur(45), "45min")
        self.assertEqual(sms.dur(60), "1h")
        self.assertEqual(sms.dur(80), "1h20")


if __name__ == "__main__":
    unittest.main(verbosity=2)
