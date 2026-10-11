"""Conversions shared by historical and live timing parsers."""
import pandas as pd


def seconds_to_timedelta(values):
    """Convert a Series of seconds to nanosecond-resolution pandas timedeltas.

    Args:
        values (pandas.Series): Numeric seconds or strings that represent seconds.

    Returns:
        pandas.Series: Timedeltas with the original index. Missing, nonnumeric,
        and infinite values become ``NaT``. Empty Series keep the same dtype.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.mask(numeric.isin([float("inf"), -float("inf")]))
    converted = pd.to_timedelta(numeric.fillna(0), unit="s").astype("timedelta64[ns]")
    return converted.mask(numeric.isna())
