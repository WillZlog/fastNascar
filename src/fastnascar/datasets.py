"""Historical pit timing and race commentary parsers."""
import pandas as pd

from ._time import seconds_to_timedelta
from .exceptions import InvalidDataError

PIT_FIELDS = {
    "vehicle_number": "DriverNumber", "driver_name": "Driver",
    "lap_count": "LapNumber", "leader_lap": "LeaderLapNumber",
    "pit_in_race_time": "PitInTime", "pit_out_race_time": "PitOutTime",
    "total_duration": "PitRoadTime", "pit_stop_duration": "ServiceTime",
    "box_stop_race_time": "BoxInTime", "box_leave_race_time": "BoxOutTime",
    "positions_gained_lost": "PositionsGained", "pit_in_rank": "PositionIn",
    "pit_out_rank": "PositionOut", "pit_stop_type": "StopType",
    "left_front_tire_changed": "LeftFrontChanged", "left_rear_tire_changed": "LeftRearChanged",
    "right_front_tire_changed": "RightFrontChanged", "right_rear_tire_changed": "RightRearChanged",
}


def parse_pit_stops(payload):
    """Normalize historical pit records, excluding warm-up and empty placeholders.

    Args:
        payload (list of dict): Decoded ``live-pit-data.json``.

    Returns:
        pandas.DataFrame: Car/leader laps, timedeltas, ranks, tire-change fields,
        and stop types. PitRoadTime is total pit-road time; ServiceTime is only
        the stationary service. Nonpositive timing sentinels become NaT.

    Raises:
        InvalidDataError: Payload is not a list of pit record dictionaries.
    """
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise InvalidDataError("Expected a list of pit records")
    frame = pd.DataFrame([{target: row.get(source) for source, target in PIT_FIELDS.items()}
                          for row in payload], columns=list(PIT_FIELDS.values()))
    frame["DriverNumber"] = frame.DriverNumber.astype("string")
    for column in ("LapNumber", "LeaderLapNumber", "PositionsGained", "PositionIn", "PositionOut"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    for column in ("PitInTime", "PitOutTime", "PitRoadTime", "ServiceTime", "BoxInTime", "BoxOutTime"):
        numeric = pd.to_numeric(frame[column], errors="coerce")
        frame[column] = seconds_to_timedelta(numeric.where(numeric > 0))
    for column in ("LeftFrontChanged", "LeftRearChanged", "RightFrontChanged", "RightRearChanged"):
        frame[column] = frame[column].astype("boolean")
    observed = (frame.LapNumber > 0) & (frame.PitInTime.notna() | frame.PitRoadTime.notna())
    return frame.loc[observed].sort_values(["DriverNumber", "LapNumber", "PitInTime"], kind="stable").reset_index(drop=True)


def parse_messages(payload):
    """Flatten NASCAR race notes while preserving source IDs and driver references.

    Args:
        payload (dict): Decoded ``lap-notes.json`` with a leader-lap mapping.

    Returns:
        pandas.DataFrame: LeaderLapNumber, FlagState, Message, MessageId, DriverIds.
        Messages are commentary, not guaranteed official race-control decisions.

    Raises:
        InvalidDataError: The mapping, lap keys, or note records are malformed.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("laps"), dict):
        raise InvalidDataError("Expected a race-note laps mapping")
    rows = []
    for lap, notes in payload["laps"].items():
        try:
            number = int(lap)
        except (ValueError, TypeError) as exc:
            raise InvalidDataError("Race-note lap keys must be integers") from exc
        if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
            raise InvalidDataError("Expected lists of race-note dictionaries")
        for note in notes:
            rows.append({"LeaderLapNumber": number, "FlagState": note.get("FlagState"),
                         "Message": note.get("Note"), "MessageId": note.get("NoteID"),
                         "DriverIds": note.get("DriverIDs", [])})
    frame = pd.DataFrame(rows, columns=["LeaderLapNumber", "FlagState", "Message", "MessageId", "DriverIds"])
    for column in ("LeaderLapNumber", "FlagState", "MessageId"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame.sort_values("LeaderLapNumber", kind="stable").reset_index(drop=True)
