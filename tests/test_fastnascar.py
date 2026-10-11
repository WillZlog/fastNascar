import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pandas as pd
import fastnascar as fn
from fastnascar.core import parse_laps
from fastnascar.live import parse_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    """Read a named UTF-8 feed excerpt from the test fixture directory."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureClient:
    """Replace HTTP requests with recorded NASCAR feed excerpts."""

    def schedule(self, *args, **kwargs):
        """Return the recorded 2024 season schedule."""
        return fixture("race_list_basic.json")

    def weekend(self, *args, **kwargs):
        """Return recorded Bristol race and run results."""
        return fixture("weekend-feed.json")

    def laps(self, *args, **kwargs):
        """Return recorded Bristol car laps and leader flags."""
        return fixture("lap-times.json")

    def live(self):
        """Return a recorded Cup live snapshot."""
        return fixture("live-feed.json")


class SessionTests(unittest.TestCase):
    def setUp(self):
        """Give each session test an independent fixture client."""
        self.client = FixtureClient()

    def test_schedule_rounds_exclude_exhibitions(self):
        """Check points-race numbering and opt-in exhibition rows."""
        schedule = fn.get_event_schedule(2024, client=self.client)
        self.assertEqual(len(schedule), 36)
        self.assertEqual(schedule.iloc[0].EventName, "DAYTONA 500")
        self.assertEqual(fn.get_event(2024, 5, client=self.client).RaceId, 5392)
        self.assertEqual(len(fn.get_event_schedule(2024, include_exhibition=True, client=self.client)), 41)

    def test_event_selection_is_explicit(self):
        """Check exact names, ambiguous tracks, missing events, and round zero."""
        with self.assertRaises(fn.AmbiguousEventError):
            fn.get_event(2024, "Bristol", client=self.client)
        with self.assertRaises(fn.EventNotFoundError):
            fn.get_event(2024, "does not exist", client=self.client)
        self.assertEqual(fn.get_event(2024, "Food City 500", client=self.client).RaceId, 5392)
        with self.assertRaises(ValueError):
            fn.get_event(2024, 0, client=self.client)

    def test_real_laps_results_and_flags(self):
        """Verify selectors and normalized timing/results against source excerpts."""
        session = fn.get_session(2024, 5, client=self.client)
        with self.assertRaises(fn.SessionNotLoadedError):
            _ = session.laps
        session.load()
        self.assertEqual(len(session.laps), 24)
        self.assertEqual(session.laps.LapNumber.min(), 1)
        self.assertEqual(session.get_driver("11").FullName, "Denny Hamlin")
        hamlin = session.laps.pick_driver(11)
        self.assertIsInstance(hamlin, fn.Laps)
        self.assertEqual(hamlin.pick_fastest().LapTime, pd.Timedelta(seconds=15.835))
        self.assertEqual(hamlin.pick_fastest().LapNumber, 2)
        self.assertEqual(len(session.laps.pick_drivers([11, "19"])), 16)
        self.assertEqual(len(hamlin.pick_laps([1, 2])), 2)
        self.assertTrue((hamlin.pick_quicklaps().LapTime < pd.Timedelta(seconds=15.835 * 1.07)).all())
        self.assertEqual(session.results.iloc[0].Points, 44)
        self.assertEqual(session.track_status.iloc[23].FlagState, 2)

    def test_practice_and_qualifying_results(self):
        """Check results-only loading and the unsupported non-race lap error."""
        practice = fn.get_session(2024, 5, "P", client=self.client)
        with self.assertRaises(fn.DataNotAvailableError):
            practice.load()
        practice.load(laps=False)
        self.assertEqual(practice.results.iloc[0].FullName, "Ryan Blaney")
        self.assertEqual(practice.results.iloc[0].BestLapTime, pd.Timedelta(seconds=15.028))
        qualifying = fn.get_session(2024, 5, "Q", client=self.client).load(laps=False)
        self.assertEqual(len(qualifying.results), 3)

    def test_failed_reload_is_atomic(self):
        """Ensure malformed lap data cannot replace previously loaded results."""
        session = fn.get_session(2024, 5, client=self.client).load()
        previous = session.results
        with patch.object(self.client, "laps", return_value={"broken": True}):
            with self.assertRaises(fn.InvalidDataError):
                session.load(refresh=True)
        self.assertIs(session.results, previous)

    def test_missing_zero_and_duplicate_laps(self):
        """Check missing times, last-record deduplication, and empty table dtype."""
        payload = {"laps": [{"Number": "01", "FullName": "Test", "Laps": [
            {"Lap": 0}, {"Lap": 1, "LapTime": 0}, {"Lap": 2, "LapTime": None},
            {"Lap": 3, "LapTime": 20}, {"Lap": 3, "LapTime": 21}]}]}
        laps = parse_laps(payload)
        self.assertEqual(len(laps), 3)
        self.assertEqual(laps.iloc[0].DriverNumber, "01")
        self.assertTrue(pd.isna(laps.iloc[0].LapTime))
        self.assertEqual(laps.pick_fastest().LapTime, pd.Timedelta(seconds=21))
        with self.assertRaises(fn.DataNotAvailableError):
            laps.pick_laps([1, 2]).pick_fastest()
        self.assertEqual(str(parse_laps({"laps": []}).LapTime.dtype), "timedelta64[ns]")


class CacheTests(unittest.TestCase):
    def tearDown(self):
        """Reset process-wide cache settings after each cache test."""
        fn.Cache.disable_cache()

    def test_cache_hit_refresh_offline_and_clear(self):
        """Check cache reuse, forced refresh, offline misses, and selective clearing."""
        with tempfile.TemporaryDirectory() as directory:
            fn.Cache.enable_cache(directory)
            client = fn.NASCARClient(retries=0)
            with patch("fastnascar.api.urlopen", side_effect=lambda *a, **kw: io.BytesIO(b'{"value": 1}')) as network:
                self.assertEqual(client.get_json("test"), {"value": 1})
                client.get_json("test")
                self.assertEqual(network.call_count, 1)
                client.get_json("test", refresh=True)
                self.assertEqual(network.call_count, 2)
            fn.Cache.enable_cache(directory, offline=True)
            self.assertEqual(client.get_json("test", refresh=True), {"value": 1})
            with self.assertRaises(fn.DataNotAvailableError):
                client.get_json("missing")
            with self.assertRaises(fn.DataNotAvailableError):
                client.live()
            unrelated = Path(directory) / "keep.json"
            unrelated.write_text("{}")
            fn.Cache.clear_cache()
            self.assertTrue(unrelated.exists())
            self.assertFalse(list(Path(directory).glob("fastnascar-*.json")))

    def test_corrupt_cache_recovers_or_errors_offline(self):
        """Check online recovery and explicit offline errors for corrupt JSON."""
        with tempfile.TemporaryDirectory() as directory:
            fn.Cache.enable_cache(directory)
            client = fn.NASCARClient(retries=0)
            with patch("fastnascar.api.urlopen", side_effect=lambda *a, **kw: io.BytesIO(b'{}')):
                client.get_json("test")
                cached = next(Path(directory).glob("fastnascar-*.json"))
                cached.write_text("invalid")
                client.get_json("test")
                self.assertEqual(json.loads(cached.read_text()), {})
            cached.write_text("invalid")
            fn.Cache.enable_cache(directory, offline=True)
            with self.assertRaises(fn.InvalidDataError):
                client.get_json("test")

    def test_http_errors_invalid_json_and_retry(self):
        """Distinguish permanent HTTP errors, transient retries, and invalid JSON."""
        client = fn.NASCARClient(retries=1)
        with patch("fastnascar.api.urlopen", side_effect=HTTPError("url", 404, "missing", {}, None)) as network:
            with self.assertRaises(fn.DataNotAvailableError):
                client.get_json("test")
            self.assertEqual(network.call_count, 1)
        with patch("fastnascar.api.urlopen", side_effect=[URLError("temporary"), io.BytesIO(b'{}')]), patch("fastnascar.api.time.sleep"):
            self.assertEqual(client.get_json("test"), {})
        with patch("fastnascar.api.urlopen", return_value=io.BytesIO(b'not json')):
            with self.assertRaises(fn.InvalidDataError):
                client.get_json("test")

    def test_live_bypasses_cache(self):
        """Require a new request for each live snapshot without writing cache files."""
        with tempfile.TemporaryDirectory() as directory:
            fn.Cache.enable_cache(directory)
            with patch("fastnascar.api.urlopen", side_effect=lambda *a, **kw: io.BytesIO(b'{}')) as network:
                fn.NASCARClient().live()
                fn.NASCARClient().live()
            self.assertEqual(network.call_count, 2)
            self.assertFalse(list(Path(directory).iterdir()))


class LiveTests(unittest.TestCase):
    def test_real_snapshot(self):
        """Verify live timing units, UTC timestamps, and observed pit stops."""
        snapshot = fn.get_live_session(client=FixtureClient())
        self.assertEqual(snapshot.race_id, 5629)
        self.assertEqual(snapshot.timing.iloc[0].Driver, "Kyle Larson (C)")
        self.assertEqual(snapshot.timing.iloc[0].LastLapTime, pd.Timedelta(seconds=30.411))
        self.assertEqual(len(snapshot.pit_stops), 6)
        self.assertIsNotNone(snapshot.timestamp.tzinfo)

    def test_wrong_series_and_race(self):
        """Reject snapshots from another series or an unexpected race."""
        payload = fixture("live-feed.json")
        payload["series_id"] = 2
        with self.assertRaises(fn.DataNotAvailableError):
            parse_snapshot(payload)
        with self.assertRaises(fn.DataNotAvailableError):
            fn.get_live_session(race_id=123, client=FixtureClient())

    def test_poll_record_replay_and_session_switch(self):
        """Check JSONL replay, run-switch protection, and invalid polling options."""
        with tempfile.TemporaryDirectory() as directory, patch("fastnascar.live.time.sleep"):
            path = Path(directory) / "recording.jsonl"
            self.assertEqual(fn.record_live(path, max_updates=2, client=FixtureClient()), 2)
            self.assertEqual(len(list(fn.replay_live(path))), 2)
        first = fixture("live-feed.json")
        second = copy.deepcopy(first)
        second["run_id"] += 1
        with patch.object(FixtureClient, "live", side_effect=[first, second]), patch("fastnascar.live.time.sleep"):
            iterator = fn.iter_live(max_updates=2, client=FixtureClient())
            next(iterator)
            with self.assertRaises(fn.DataNotAvailableError):
                next(iterator)
        for kwargs in ({"interval": 0}, {"max_updates": 0}, {"interval": float("nan")}):
            with self.assertRaises(ValueError):
                next(fn.iter_live(client=FixtureClient(), **kwargs))


if __name__ == "__main__":
    unittest.main()
