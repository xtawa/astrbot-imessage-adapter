from astrbot.api.star import Star

from .imessage.adapter import IMessageAdapter  # noqa: F401 - registers the platform


class IMessagePlugin(Star):
    """Load the platform registration; AstrBot owns platform lifecycle."""
