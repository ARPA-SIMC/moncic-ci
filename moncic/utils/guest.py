from typing import TypeVar, ParamSpec, Callable
import functools

P = ParamSpec("P")
R = TypeVar("R")

# Set to True when running in the guest system
in_guest = False


def host_only(f: Callable[P, R]) -> Callable[P, R]:
    """
    Mark a function to be run only in host systems
    """

    @functools.wraps(f)
    def wrapper(*args: P.args, **kw: P.kwargs) -> R:
        global in_guest
        if in_guest:
            raise RuntimeError(f"{f.__name__} called when in guest system")
        return f(*args, **kw)

    return wrapper


def guest_only(f: Callable[P, R]) -> Callable[P, R]:
    """
    Mark a function to be run only in guest systems
    """

    @functools.wraps(f)
    def wrapper(*args: P.args, **kw: P.kwargs) -> R:
        global in_guest
        if not in_guest:
            raise RuntimeError(f"{f.__name__} called when in host system")
        return f(*args, **kw)

    return wrapper
