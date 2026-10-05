"""
Explicit copy strategy for :class:`~scrapy.Request`.

The :class:`CopyStrategy` class provides an opt-in, explicit way of copying a
request's :attr:`~scrapy.Request.meta` and :attr:`~scrapy.Request.cb_kwargs`
attributes that differs from the default :meth:`~scrapy.Request.replace`
behavior in the following ways:

* only the keys in the configured allowed-keys whitelist are copied;
* immutable values (``str``, ``bytes``, ``int``, ``float``, ``bool`` and
  ``None``) are shared with the original request, while mutable containers
  (``dict``, ``list``, ``tuple``, ``set``, ``frozenset`` and ``bytearray``)
  are deep-copied so that mutating them on the copy never affects the
  original request;
* values of any other type cause an :class:`UncopyableValueError` to be
  raised (with the full key path of the offending value) instead of being
  shallow-copied;
* registered internal protocol meta keys (e.g. retry protocol keys such as
  ``retry_times``) are handled according to their registered rule, so they
  are not casually dropped;
* the callback and errback objects are preserved by identity but never
  executed.

If the copy fails (e.g. because of an unsupported value), the original
request is left untouched: the strategy builds the new request only after
all values have been copied successfully.

The default :meth:`~scrapy.Request.replace` behavior is unchanged; the
explicit strategy is only used when a :class:`CopyStrategy` is passed to
:meth:`~scrapy.Request.copy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from scrapy.http.request import Request


class CopyRule(Enum):
    """Rule that decides how a registered internal protocol meta key is
    handled by the explicit copy strategy.
    """

    #: Copy the value (deep-copying mutable values, sharing immutable ones).
    PRESERVE = "preserve"

    #: Reset the key to the rule's default value on the copy.
    RESET = "reset"

    #: Do not copy the key into the copy.
    DROP = "drop"


@dataclass(frozen=True)
class ProtocolKeyRule:
    """Rule registered for an internal protocol meta key.

    :param rule: the :class:`CopyRule` to apply.
    :param default: the default value used when ``rule`` is
        :attr:`CopyRule.RESET`.
    """

    rule: CopyRule
    default: Any = None


#: Default registry of internal protocol meta keys.
#:
#: These keys are handled according to their registered rule even when they
#: are not present in the explicit allowed-keys whitelist, so they are not
#: casually dropped.
DEFAULT_PROTOCOL_META_KEYS: Mapping[str, ProtocolKeyRule] = MappingProxyType(
    {
        # retry protocol
        "retry_times": ProtocolKeyRule(CopyRule.PRESERVE),
        "max_retry_times": ProtocolKeyRule(CopyRule.PRESERVE),
        "dont_retry": ProtocolKeyRule(CopyRule.PRESERVE),
        "priority_adjust": ProtocolKeyRule(CopyRule.PRESERVE),
    }
)


class UncopyableValueError(TypeError):
    """Raised when a value cannot be safely copied by the explicit copy
    strategy.

    The error message includes the full key path of the offending value,
    e.g. ``meta['key'][0]`` or ``cb_kwargs['key']``.
    """


#: Types whose values are immutable and can be shared with the original
#: request without risk.
_SHAREABLE_TYPES: tuple[type, ...] = (
    str,
    bytes,
    int,
    float,
    bool,
    type(None),
)

#: Types whose values are mutable (or may contain mutable values) and must be
#: deep-copied.
_COPYABLE_CONTAINER_TYPES: tuple[type, ...] = (
    dict,
    list,
    tuple,
    set,
    frozenset,
    bytearray,
)


def _copy_key(key: Any, path: str) -> Any:
    """Copy a single dict key, rejecting keys of unsupported types."""
    if type(key) not in _SHAREABLE_TYPES:
        raise UncopyableValueError(
            f"Cannot safely copy dict key at {path}: "
            f"{type(key).__name__} object is not of a supported type"
        )
    return key


def _copy_value(value: Any, path: str) -> Any:
    """Copy a single value, sharing immutable values and deep-copying
    mutable containers.

    Values of any other type are rejected with an
    :class:`UncopyableValueError` that includes the full key path.
    """
    value_type = type(value)
    if value_type in _SHAREABLE_TYPES:
        return value
    if value_type is dict:
        return {
            _copy_key(key, path): _copy_value(item, f"{path}[{key!r}]")
            for key, item in value.items()
        }
    if value_type is list:
        return [
            _copy_value(item, f"{path}[{index}]") for index, item in enumerate(value)
        ]
    if value_type is tuple:
        return tuple(
            _copy_value(item, f"{path}[{index}]") for index, item in enumerate(value)
        )
    if value_type is set:
        return {_copy_value(item, f"{path}[{item!r}]") for item in value}
    if value_type is frozenset:
        return frozenset(_copy_value(item, f"{path}[{item!r}]") for item in value)
    if value_type is bytearray:
        return bytearray(value)
    raise UncopyableValueError(
        f"Cannot safely copy value at {path}: "
        f"{value_type.__name__} object is not of a supported type"
    )


class CopyStrategy:
    """Explicit strategy for copying a :class:`~scrapy.Request`.

    Only the meta keys and cb_kwargs keys in the configured whitelist are
    copied, plus registered internal protocol meta keys which are handled
    according to their registered rule. Immutable values are shared with the
    original request, mutable containers are deep-copied, and values of
    unsupported types cause an :class:`UncopyableValueError` to be raised
    instead of being shallow-copied.

    :param allowed_meta_keys: the meta keys to copy from the original
        request. Keys not in this whitelist and not registered as protocol
        meta keys are dropped.
    :param allowed_cb_kwargs_keys: the cb_kwargs keys to copy from the
        original request. Keys not in this whitelist are dropped.
    :param protocol_meta_keys: a mapping of internal protocol meta keys to
        :class:`CopyRule` or :class:`ProtocolKeyRule` objects. If ``None``,
        :data:`DEFAULT_PROTOCOL_META_KEYS` is used.

    .. code-block:: python

        from scrapy import Request
        from scrapy.http.request.copy_strategy import CopyStrategy

        strategy = CopyStrategy(
            allowed_meta_keys={"source_url", "depth"},
            allowed_cb_kwargs_keys={"item"},
        )
        new_request = request.copy(strategy=strategy)
    """

    def __init__(
        self,
        *,
        allowed_meta_keys: Iterable[str] = (),
        allowed_cb_kwargs_keys: Iterable[str] = (),
        protocol_meta_keys: (Mapping[str, CopyRule | ProtocolKeyRule] | None) = None,
    ) -> None:
        self.allowed_meta_keys: frozenset[str] = frozenset(allowed_meta_keys)
        self.allowed_cb_kwargs_keys: frozenset[str] = frozenset(allowed_cb_kwargs_keys)
        if protocol_meta_keys is None:
            protocol_meta_keys = DEFAULT_PROTOCOL_META_KEYS
        self.protocol_meta_keys: dict[str, ProtocolKeyRule] = {}
        for key, rule in protocol_meta_keys.items():
            if isinstance(rule, ProtocolKeyRule):
                self.protocol_meta_keys[key] = rule
            else:
                self.protocol_meta_keys[key] = ProtocolKeyRule(rule)

    def _copy_mapping(
        self,
        source: Mapping[str, Any],
        *,
        allowed_keys: frozenset[str],
        path_prefix: str,
        use_protocol_rules: bool,
    ) -> dict[str, Any]:
        copied: dict[str, Any] = {}
        for key, value in source.items():
            path = f"{path_prefix}[{key!r}]"
            if key in allowed_keys:
                copied[key] = _copy_value(value, path)
            elif use_protocol_rules and key in self.protocol_meta_keys:
                rule = self.protocol_meta_keys[key]
                if rule.rule is CopyRule.PRESERVE:
                    copied[key] = _copy_value(value, path)
                elif rule.rule is CopyRule.RESET:
                    copied[key] = rule.default
                # CopyRule.DROP: do not copy the key
            # else: the key is not authorized, drop it
        return copied

    def copy_meta(self, meta: Mapping[str, Any]) -> dict[str, Any]:
        """Return a deep copy of *meta* following this strategy's rules."""
        return self._copy_mapping(
            meta,
            allowed_keys=self.allowed_meta_keys,
            path_prefix="meta",
            use_protocol_rules=True,
        )

    def copy_cb_kwargs(self, cb_kwargs: Mapping[str, Any]) -> dict[str, Any]:
        """Return a deep copy of *cb_kwargs* following this strategy's
        rules.
        """
        return self._copy_mapping(
            cb_kwargs,
            allowed_keys=self.allowed_cb_kwargs_keys,
            path_prefix="cb_kwargs",
            use_protocol_rules=False,
        )

    def copy(self, request: Request) -> Request:
        """Return a copy of *request* following this strategy's rules.

        The original request is left untouched: the new request is only
        built after all values have been copied successfully.
        """
        # Access the stored attributes directly to avoid the lazy
        # initialization side effects of the public properties.
        new_meta = self.copy_meta(request._meta or {})
        new_cb_kwargs = self.copy_cb_kwargs(request._cb_kwargs or {})
        return request.replace(meta=new_meta, cb_kwargs=new_cb_kwargs)
