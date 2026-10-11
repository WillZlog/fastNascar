"""Optional plotting helpers. Importing fastnascar does not require matplotlib."""


def plot_lap_times(laps, *, drivers=None, ax=None):
    """Plot recorded lap times in seconds against each car's completed lap.

    Args:
        laps (fastnascar.Laps): Loaded laps, or a previously filtered selection.
        drivers (str, int, or iterable, optional): Exact full names or car numbers
            accepted by ``Laps.pick_drivers``. Omit to plot the whole selection.
        ax (matplotlib.axes.Axes, optional): Axes to draw on. Created if omitted.

    Returns:
        matplotlib.axes.Axes: Axes containing one line per selected car.

    Raises:
        ImportError: Matplotlib is not installed; install the ``plot`` extra.

    This helper includes slow laps. Call ``pick_quicklaps`` first to apply a
    pace threshold. Call ``plt.show()`` or ``ax.figure.savefig(...)`` to display
    or save the figure.
    """
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots()
    selected = laps if drivers is None else laps.pick_drivers(drivers)
    for number, rows in selected.groupby("DriverNumber", sort=False):
        rows = rows.sort_values("LapNumber")
        name = rows.Driver.iloc[0]
        ax.plot(rows.LapNumber, rows.LapTime.dt.total_seconds(), label=f"#{number} {name}")
    ax.set(xlabel="Car lap", ylabel="Lap time (seconds)")
    if len(selected):
        ax.legend()
    return ax


def plot_positions(laps, *, drivers=None, ax=None):
    """Plot running positions by car lap, with first place at the top.

    Args:
        laps (Laps): Loaded lap selection.
        drivers (str, int, or iterable, optional): Car numbers or exact names.
        ax (matplotlib.axes.Axes, optional): Existing axes.

    Returns:
        matplotlib.axes.Axes: Position plot. Car-lap axes are not synchronized
        race time; lapped cars can be on different race moments at the same x.
    """
    import matplotlib.pyplot as plt
    if ax is None:
        _, ax = plt.subplots()
    selected = laps if drivers is None else laps.pick_drivers(drivers)
    for number, group in selected.groupby("DriverNumber", sort=False):
        group = group.sort_values("LapNumber")
        ax.step(group.LapNumber, group.Position, where="post", label=f"#{number}")
    ax.set(xlabel="Car lap", ylabel="Running position")
    if not ax.yaxis_inverted():
        ax.invert_yaxis()
    if len(selected):
        ax.legend()
    return ax


def plot_comparison(laps, driver, reference, *, ax=None):
    """Plot per-lap pace differences between two cars.

    Args:
        laps (Laps): Loaded lap data.
        driver (str or int): Car number or exact driver name.
        reference (str or int): Reference car number or exact name.
        ax (matplotlib.axes.Axes, optional): Existing axes.

    Returns:
        matplotlib.axes.Axes: Delta seconds against matching car laps. Positive
        values mean driver was slower; these are not on-track gaps.

    Raises:
        DataNotAvailableError: A selector does not identify exactly one car.
    """
    import matplotlib.pyplot as plt
    from .analysis import compare_drivers
    comparison = compare_drivers(laps, driver, reference)
    if ax is None:
        _, ax = plt.subplots()
    ax.plot(comparison.LapNumber, comparison.Delta.dt.total_seconds(), label=f"{driver} vs {reference}")
    ax.axhline(0, color="grey", linewidth=0.8)
    ax.set(xlabel="Car lap", ylabel="Lap-time delta (seconds)")
    ax.legend()
    return ax


def plot_rolling_pace(laps, *, window=10, drivers=None, ax=None):
    """Plot consecutive-lap mean pace, leaving gaps at incomplete windows.

    Args:
        laps (Laps): Loaded lap selection.
        window (int): Required consecutive lap count.
        drivers (str, int, or iterable, optional): Car numbers or exact names.
        ax (matplotlib.axes.Axes, optional): Existing axes.

    Returns:
        matplotlib.axes.Axes: Average lap seconds against car lap.

    Raises:
        ValueError: Window is not a positive integer.
    """
    import matplotlib.pyplot as plt
    from .analysis import rolling_pace
    selected = laps if drivers is None else laps.pick_drivers(drivers)
    pace = rolling_pace(selected, window)
    if ax is None:
        _, ax = plt.subplots()
    for number, group in pace.groupby("DriverNumber", sort=False):
        # Reindex so missing car laps break the line, not just the rolling window.
        group = group.set_index("LapNumber").reindex(range(int(group.LapNumber.min()), int(group.LapNumber.max()) + 1))
        ax.plot(group.index, group.MeanLapTime.dt.total_seconds(), label=f"#{number}")
    ax.set(xlabel="Car lap", ylabel=f"{window}-lap mean time (seconds)")
    if len(pace):
        ax.legend()
    return ax
