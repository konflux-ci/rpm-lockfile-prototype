import logging
import threading
import sys

# This is a mapping from thread id to an architecture being processed by that
# thread. Used to enrich the logs.
_thread_to_arch = {}
_thread_to_arch_lock = threading.Lock()


def setup(debug=False):
    """Configure logging: errors go to stderr, everything else goes to stdout.
    """

    class ExcludeErrorsFilter(logging.Filter):
        def filter(self, record):
            """Only lets through log messages with log level below ERROR."""
            return record.levelno < logging.ERROR

    formatter = ArchDetailFormatter(
        fmt="%(levelname)s:%(name)s:%(arch)s:%(message)s",
    )

    console_stdout = logging.StreamHandler(stream=sys.stdout)
    console_stdout.addFilter(ExcludeErrorsFilter())
    console_stdout.setLevel(logging.DEBUG if debug else logging.INFO)
    console_stdout.setFormatter(formatter)

    console_stderr = logging.StreamHandler(stream=sys.stderr)
    console_stderr.setLevel(logging.ERROR)
    console_stderr.setFormatter(formatter)

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        handlers=[console_stdout, console_stderr],
    )


class ArchDetailFormatter(logging.Formatter):
    """Custom formatter that looks up architecture handled by the current
    thread and adds it to the log record. If not available (i.e. on the main
    thread), empty string is used.
    """
    def format(self, record):
        with _thread_to_arch_lock:
            record.arch = _thread_to_arch.get(threading.get_ident(), "")
        return super().format(record)


def set_thread_arch(arch):
    """Set the architecture processed by the current thread."""
    with _thread_to_arch_lock:
        _thread_to_arch[threading.get_ident()] = arch
