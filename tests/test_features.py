"""Analysis, multi-series, historical pit data, and recording regression tests."""
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

import fastnascar as fn
from fastnascar.core import parse_laps
from fastnascar.datasets import parse_messages, parse_pit_stops
from fastnascar.live import parse_snapshot
from test_fastnascar import FixtureClient, fixture


def lap_table(records):
    """Build explicit per-car lap records for algorithm boundary tests."""
    return parse_laps({"laps": [{"Number": number, "FullName": f"Driver {number}",
                                "Laps": [{"Lap": lap, "LapTime": seconds, "RunningPos": 1}
                                         for lap, seconds in times]} for number, times in records.items()]})


class AnalysisTests(unittest.TestCase):
    """Check analysis against hand-calculated pace and pit intervals."""

    def test_rolling_windows_break_at_missing_laps_and_times(self):
        """Require actual consecutive laps rather than consecutive table rows."""
        laps = lap_table({"77": [(1, 10), (2, 12), (4, 8), (5, 10), (6, None), (7, 9)]})
        pace = fn.rolling_pace(laps, 2).set_index("LapNumber")
        self.assertEqual(pace.loc[2, "MeanLapTime"], pd.Timedelta(seconds=11))
        self.assertTrue(pd.isna(pace.loc[4, "MeanLapTime"]))
        self.assertEqual(pace.loc[5, "MeanLapTime"], pd.Timedelta(seconds=9))
        self.assertTrue(pd.isna(pace.loc[7, "MeanLapTime"]))
        best = fn.best_lap_average(laps, 2).iloc[0]
        self.assertEqual((best.StartLap, best.EndLap), (4, 5))
        self.assertEqual(best.AverageLapTime, pd.Timedelta(seconds=9))

    def test_empty_windows_and_invalid_sizes(self):
        """Return empty averages for insufficient data and reject invalid windows."""
        laps = lap_table({"77": [(1, 10)]})
        self.assertTrue(fn.best_lap_average(laps, 10).empty)
        self.assertTrue(fn.rolling_pace(lap_table({}), 2).empty)
        for window in (0, True, 1.5):
            with self.assertRaises(ValueError):
                fn.rolling_pace(laps, window)

    def test_compare_drivers_has_correct_sign_and_keeps_missing_laps(self):
        """Positive deltas mean slower laps; unmatched car laps remain missing."""
        laps = lap_table({"77": [(1, 12), (2, 9)], "5": [(1, 10), (3, 8)]})
        compared = fn.compare_drivers(laps, "77", "5")
        self.assertEqual(compared.iloc[0].Delta, pd.Timedelta(seconds=2))
        self.assertTrue(pd.isna(compared.iloc[1].ReferenceLapTime))
        self.assertEqual(len(compared), 3)
        with self.assertRaises(fn.DataNotAvailableError):
            fn.compare_drivers(laps, "missing", "5")

    def test_stints_exclude_entry_and_first_post_entry_laps(self):
        """Separate pit intervals without treating missing pit records as tire data."""
        laps = lap_table({"77": [(1, 10), (2, 10), (3, 60), (4, 30), (5, 11), (6, 12)]})
        pits = pd.DataFrame({"DriverNumber": ["77"], "LapNumber": [3]})
        stints = fn.summarize_stints(laps, pits)
        self.assertEqual(stints.StartLap.tolist(), [1, 4])
        self.assertEqual(stints.TimedLaps.tolist(), [2, 2])
        self.assertEqual(stints.iloc[1].MedianLapTime, pd.Timedelta(seconds=11.5))

    def test_flag_periods_do_not_bridge_unknown_laps(self):
        """Split periods at missing leader laps and flag transitions."""
        status = pd.DataFrame({"LeaderLapNumber": [1, 2, 4, 5], "FlagState": [2, 2, 2, 1]})
        periods = fn.flag_periods(status)
        self.assertEqual(periods.RecordedLaps.tolist(), [2, 1, 1])
        self.assertEqual(periods.EndLap.tolist(), [2, 4, 5])


class DatasetTests(unittest.TestCase):
    """Check source timing units and optional dataset loading."""

    def test_real_pit_timing_and_notes(self):
        """Keep service time distinct from pit-road time and preserve note IDs."""
        pits = parse_pit_stops(fixture("live-pit-data.json"))
        larson = pits.loc[pits.DriverNumber == "5"].iloc[0]
        self.assertEqual(larson.PitRoadTime, pd.Timedelta(seconds=53.026))
        self.assertEqual(larson.ServiceTime, pd.Timedelta(seconds=9.316))
        self.assertTrue(larson.LeftFrontChanged)
        self.assertEqual(len(pits), 3)
        notes = parse_messages(fixture("lap-notes.json"))
        self.assertEqual(notes.iloc[2].LeaderLapNumber, 41)
        self.assertEqual(notes.iloc[2].Message, "Competition caution waved")

    def test_optional_load_is_atomic(self):
        """A failed notes load must not replace an existing pit table."""
        client = FixtureClient()
        client.pit_stops = lambda *a, **kw: fixture("live-pit-data.json")
        client.notes = lambda *a, **kw: fixture("lap-notes.json")
        session = fn.get_session(2024, 5, client=client).load(pit_stops=True, messages=True)
        previous = session.pit_stops
        with patch.object(client, "notes", return_value={}):
            with self.assertRaises(fn.InvalidDataError):
                session.load(pit_stops=True, messages=True)
        self.assertIs(previous, session.pit_stops)

    def test_empty_and_malformed_datasets(self):
        """Empty feeds retain columns while malformed structures raise errors."""
        self.assertTrue(parse_pit_stops([]).empty)
        self.assertTrue(parse_messages({"laps": {}}).empty)
        with self.assertRaises(fn.InvalidDataError):
            parse_messages({"laps": {"bad": []}})
        with self.assertRaises(fn.InvalidDataError):
            parse_pit_stops({})

    def test_series_routes_and_schedule(self):
        """Use the selected schedule key and series-specific endpoint path."""
        client = FixtureClient()
        schedule = fixture("race_list_basic.json")
        schedule["series_2"] = copy.deepcopy(schedule["series_1"])
        with patch.object(client, "schedule", return_value=schedule):
            session = fn.get_session(2024, 5, series="Xfinity", client=client)
            self.assertEqual(session.event.SeriesId, 2)
        with patch.object(fn.NASCARClient, "get_json", return_value={}) as request:
            fn.NASCARClient().laps(2026, 5686, series="Trucks")
            self.assertIn("/3/5686/", request.call_args.args[0])
        with self.assertRaises(ValueError):
            fn.get_event_schedule(2024, series=4, client=client)


class HistoryTests(unittest.TestCase):
    """Validate live lap corrections, missed laps, and resumed recordings."""

    def test_deduplication_gaps_corrections_and_stale_updates(self):
        """Never synthesize missed lap times or replace newer times with stale ones."""
        raw = fixture("live-feed.json")
        history = fn.LiveHistory()
        self.assertTrue(history.laps.empty)
        history.update(parse_snapshot(raw))
        self.assertEqual(history.update(parse_snapshot(raw)), 0)
        self.assertTrue(history.laps.MissedLaps.isna().all())
        second = copy.deepcopy(raw)
        second["time_of_day_os"] = "2026-10-11T16:27:27-04:00"
        second["vehicles"][0]["laps_completed"] += 3
        history.update(parse_snapshot(second))
        newest = history.laps.loc[history.laps.DriverNumber == "5"].iloc[-1]
        self.assertEqual(newest.MissedLaps, 2)
        second["vehicles"][0]["last_lap_time"] = 30.5
        self.assertEqual(history.update(parse_snapshot(second)), 1)
        self.assertEqual(history.update(parse_snapshot(raw)), 0)
        self.assertEqual(history.laps.loc[history.laps.DriverNumber == "5"].iloc[-1].LapTime, pd.Timedelta(seconds=30.5))

    def test_history_rejects_other_run(self):
        """Protect a history from silently combining two timing runs."""
        raw = fixture("live-feed.json")
        history = fn.LiveHistory()
        history.update(parse_snapshot(raw))
        raw["run_id"] += 1
        with self.assertRaises(fn.DataNotAvailableError):
            history.update(parse_snapshot(raw))

    def test_recording_resume_deduplicates_and_rejects_other_race(self):
        """Preserve an existing recording when a resumed poll has another race."""
        with tempfile.TemporaryDirectory() as folder, patch("fastnascar.live.time.sleep"):
            path = Path(folder) / "race.jsonl"
            client = FixtureClient()
            self.assertEqual(fn.record_live(path, max_updates=2, deduplicate=True, client=client), 1)
            self.assertEqual(fn.record_live(path, max_updates=1, deduplicate=True, client=client), 0)
            self.assertEqual(len(fn.LiveHistory.from_recording(path).laps), 3)
            previous = path.read_bytes()
            raw = fixture("live-feed.json")
            raw["race_id"] += 1
            with patch.object(client, "live", return_value=raw):
                with self.assertRaises(fn.DataNotAvailableError):
                    fn.record_live(path, max_updates=1, client=client)
            self.assertEqual(path.read_bytes(), previous)

    def test_other_series_snapshot_is_supported_explicitly(self):
        """Only accept an Xfinity snapshot when requested as series two."""
        raw = fixture("live-feed.json")
        raw["series_id"] = 2
        snapshot = parse_snapshot(raw, series="Xfinity")
        self.assertEqual(snapshot.series_id, 2)


class SeasonTests(unittest.TestCase):
    """Check driver-ID grouping and explicit reporting of unavailable races."""

    def test_collect_and_aggregate_results(self):
        """Compute earned points and finish counts from a restricted race selection."""
        results = fn.get_season_results(2024, race_ids=[5392], client=FixtureClient())
        stats = fn.driver_standings(results)
        hamlin = stats.loc[stats.DriverId == 1361].iloc[0]
        self.assertEqual(hamlin.Points, 44)
        self.assertEqual(hamlin.Wins, 1)
        self.assertEqual(hamlin.AverageFinish, 1)
        with self.assertRaises(ValueError):
            fn.driver_standings(pd.concat([results, results]))

    def test_unavailable_races_are_reported(self):
        """Skip only unavailable feeds when explicitly requested, with diagnostics."""
        client = FixtureClient()
        with patch.object(client, "weekend", side_effect=fn.DataNotAvailableError("unavailable")):
            results = fn.get_season_results(2024, race_ids=[5392], errors="skip", client=client)
            self.assertTrue(results.empty)
            self.assertEqual(results.attrs["unavailable_races"][0]["RaceId"], 5392)
            with self.assertRaises(fn.DataNotAvailableError):
                fn.get_season_results(2024, race_ids=[5392], client=client)
