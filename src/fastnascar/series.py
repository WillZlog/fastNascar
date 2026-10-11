"""Series identifiers shared by schedule and timing adapters."""
from numbers import Integral


def resolve_series(series):
    """Return a NASCAR series ID for 1/Cup, 2/Xfinity, or 3/Trucks.

    Args:
        series (int or str): Numeric ID or case-insensitive series alias.

    Returns:
        int: Validated series ID.

    Raises:
        ValueError: The series is unsupported.
    """
    aliases = {"cup": 1, "ncs": 1, "xfinity": 2, "nxs": 2, "oreilly": 2,
               "trucks": 3, "truck": 3, "ncts": 3}
    if isinstance(series, bool):
        raise ValueError("series must be Cup/1, Xfinity/2, or Trucks/3")
    if isinstance(series, str):
        series = aliases.get(series.strip().casefold(), series)
    if isinstance(series, Integral) and series in (1, 2, 3):
        return int(series)
    raise ValueError("series must be Cup/1, Xfinity/2, or Trucks/3")
