"""Historical common error import shim.

New code must import from cow.common.errors."""

from cow.common.errors import (
    DomainError as DomainError,
    reject as reject,
)
