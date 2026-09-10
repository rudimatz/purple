# Copyright The IETF Trust 2026, All Rights Reserved
"""Test runner that keeps log output off the console"""

import logging

from django.test.runner import DiscoverRunner


class QuietLogsRunner(DiscoverRunner):
    """Silence configured log handlers while tests run; --show-logs restores them.

    Handlers are raised rather than loggers so assertLogs, which attaches its own
    handler, keeps working regardless of the active LOGGING config.
    """

    def __init__(self, *, show_logs=False, **kwargs):
        super().__init__(**kwargs)
        self.show_logs = show_logs

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--show-logs",
            action="store_true",
            help="Let log output reach the console during tests.",
        )

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        if self.show_logs:
            return
        for logger in (logging.root, *logging.root.manager.loggerDict.values()):
            for handler in getattr(logger, "handlers", ()):
                handler.setLevel(logging.CRITICAL + 1)
