"""Errors callers can handle without depending on the transport implementation."""


class FastNASCARException(Exception):
    """Base package error."""


class DataNotAvailableError(FastNASCARException):
    """The upstream feed or requested dataset is unavailable."""


class InvalidDataError(FastNASCARException):
    """An upstream payload does not match the supported schema."""


class SessionNotLoadedError(FastNASCARException):
    """Call Session.load before accessing this dataset."""


class EventNotFoundError(FastNASCARException):
    """No event matches the supplied selector."""


class AmbiguousEventError(FastNASCARException):
    """More than one event matches; select a round or race ID."""
