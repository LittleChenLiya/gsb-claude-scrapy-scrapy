"""
Explicit, safe copying of :class:`~scrapy.http.Request` objects.

Unlike :meth:`Request.copy` and :meth:`Request.replace`, which shallow-copy
``meta`` and ``cb_kwargs``, this module implements an *explicit copy
policy*:

-   Only allowed keys of ``meta`` and ``cb_kwargs`` are copied.
-   Values that are safe to share (immutable scalars, callables) are shared
    by identity; callables (e.g. ``callback``) keep their identity and are
    never called during copying.
-   Mutable containers (``dict``, ``list``, ``set``, ``frozenset``,
    ``tuple``) are recursively (deep) copied, so mutating the copy never
    affects the original request.
-   Values that cannot be safely copied cause an explicit
    :class:`UnsafeRequestCopyError` reporting the full key path, instead of
    being silently shallow-copied. If copying fails, the original request is
    left untouched.
-   Registered *internal* meta keys (e.g. the retry protocol keys used by
    the retry middleware) are handled according to their registered rule
    and are never dropped just because they look internal.

Run the focused tests with::

    python -m pytest tests/test_request_copy_policy.py -v
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scrapy.http import Request

#: Rule applied to a registered internal meta key: copy it (following the
#: regular value rules) or drop it from the copy.
InternalKeyRule = Literal["copy", "drop"]

#: Default registry of internal meta keys and how to handle them. These are
#: the retry protocol keys used by the built-in retry middleware. Registered
#: internal keys are never silently dropped.
DEFAULT_INTERNAL_KEY_RULES: dict[str, InternalKeyRule] = {
    "retry_times": "copy",
    "max_retry_times": "copy",
    "dont_retry": "copy",
    "retry_reason": "copy",
}

#: Key prefixes considered unauthorized debug keys, dropped by default.
DEFAULT_DENIED_KEY_PREFIXES: tuple[str, ...] = ("debug_", "_debug")

#: Immutable value types that are safe to share between the original request
#: and its copy.
_SHAREABLE_TYPES: tuple[type, ...] = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
)


class UnsafeRequestCopyError(TypeError):
    """Raised when a request value cannot be safely copied.

    The error message reports the full key path of the offending value,
    e.g. ``meta['session']['token']`` or ``cb_kwargs['client']``.
    """


@dataclass(frozen=True)
class RequestCopyPolicy:
    """Explicit policy controlling how a request is copied.

    :param allowed_meta_keys: If not ``None``, only these ``meta`` keys are
        copied (registered internal keys are still handled by their own
        rule). If ``None``, all non-denied keys are copied.
    :param allowed_cb_kwargs_keys: Same as *allowed_meta_keys*, but for
        ``cb_kwargs``.
    :param denied_keys: Keys that are never copied.
    :param denied_key_prefixes: Keys starting with any of these prefixes are
        never copied. Defaults to the debug key prefixes ``("debug_",
        "_debug")``.
    :param internal_key_rules: Registered internal keys mapped to ``"copy"``
        or ``"drop"``. Registered keys take precedence over the allow/deny
        rules, so internal protocol keys (e.g. retry keys) are not dropped
        by accident.

    Example copy rules::

        policy = RequestCopyPolicy(
            # copy only these meta keys (plus registered internal keys)
            allowed_meta_keys=frozenset({"item", "retry_times"}),
            # copy only these cb_kwargs keys
            allowed_cb_kwargs_keys=frozenset({"page"}),
            # extra keys to drop explicitly
            denied_keys=frozenset({"session"}),
            # keys with these prefixes are dropped (default: debug keys)
            denied_key_prefixes=("debug_", "_debug"),
            # registered internal keys and their rule: "copy" or "drop"
            internal_key_rules={
                "retry_times": "copy",
                "max_retry_times": "copy",
                "dont_retry": "copy",
                "retry_reason": "copy",
            },
        )
    """

    allowed_meta_keys: frozenset[str] | None = None
    allowed_cb_kwargs_keys: frozenset[str] | None = None
    denied_keys: frozenset[str] = frozenset()
    denied_key_prefixes: tuple[str, ...] = DEFAULT_DENIED_KEY_PREFIXES
    internal_key_rules: Mapping[str, InternalKeyRule] = field(
        default_factory=lambda: dict(DEFAULT_INTERNAL_KEY_RULES)
    )

    def key_action(self, key: str, allowed: frozenset[str] | None) -> InternalKeyRule:
        """Return whether *key* must be copied or dropped."""
        if key in self.internal_key_rules:
            return self.internal_key_rules[key]
        if key in self.denied_keys or any(
            key.startswith(prefix) for prefix in self.denied_key_prefixes
        ):
            return "drop"
        if allowed is not None and key not in allowed:
            return "drop"
        return "copy"


def _copy_value(value: Any, path: str) -> Any:
    """Return a safe copy of *value*, or raise :class:`UnsafeRequestCopyError`.

    *path* is the key path of the value, used in error messages.
    """
    if isinstance(value, _SHAREABLE_TYPES):
        # Immutable scalar: safe to share.
        return value
    if callable(value):
        # Callables are shared by identity and never called during copying.
        return value
    if isinstance(value, dict):
        new_dict: dict[Any, Any] = {}
        for k, v in value.items():
            if not isinstance(k, _SHAREABLE_TYPES) or callable(k):
                raise UnsafeRequestCopyError(
                    f"Cannot safely copy request value at {path}: "
                    f"mapping key {k!r} of type {type(k).__name__!r} is not "
                    f"an immutable scalar"
                )
            new_dict[k] = _copy_value(v, f"{path}[{k!r}]")
        return new_dict
    if isinstance(value, list):
        return [_copy_value(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, tuple):
        return tuple(_copy_value(v, f"{path}[{i}]") for i, v in enumerate(value))
    if isinstance(value, (set, frozenset)):
        return type(value)(_copy_value(v, f"{path}[{v!r}]") for v in value)
    raise UnsafeRequestCopyError(
        f"Cannot safely copy request value at {path}: "
        f"object of type {type(value).__name__!r} is neither an immutable "
        f"scalar, a callable, nor a container of safely copyable values; "
        f"copy it explicitly yourself instead of relying on a shallow copy"
    )


def _copy_mapping(
    mapping: Mapping[str, Any],
    allowed: frozenset[str] | None,
    policy: RequestCopyPolicy,
    kind: str,
) -> dict[str, Any]:
    """Copy *mapping* (``meta`` or ``cb_kwargs``) according to *policy*."""
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        if policy.key_action(key, allowed) == "drop":
            continue
        result[key] = _copy_value(value, f"{kind}[{key!r}]")
    return result


def copy_request(request: Request, policy: RequestCopyPolicy | None = None) -> Request:
    """Return a copy of *request* following *policy*.

    ``meta`` and ``cb_kwargs`` are copied key by key according to *policy*
    (defaults to :class:`RequestCopyPolicy` with its default rules). All
    other attributes keep the same behavior as :meth:`Request.replace`; in
    particular, ``callback`` and ``errback`` keep their identity and are
    never called.

    If any value cannot be safely copied, :class:`UnsafeRequestCopyError` is
    raised reporting the key path, and the original request is left
    untouched.
    """
    if policy is None:
        policy = RequestCopyPolicy()
    # Copy into brand-new mappings first, so that a failure leaves the
    # original request untouched. Access the private attributes to avoid
    # lazily creating empty dicts on the original request.
    new_meta = _copy_mapping(
        request._meta or {}, policy.allowed_meta_keys, policy, "meta"
    )
    new_cb_kwargs = _copy_mapping(
        request._cb_kwargs or {}, policy.allowed_cb_kwargs_keys, policy, "cb_kwargs"
    )
    return request.replace(meta=new_meta, cb_kwargs=new_cb_kwargs)
