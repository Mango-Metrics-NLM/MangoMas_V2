from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

def given(*args: Any, **kwargs: Any) -> Callable[[_F], _F]: ...
def settings(*args: Any, **kwargs: Any) -> Callable[[_F], _F]: ...