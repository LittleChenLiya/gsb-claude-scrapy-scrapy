"""
Tests for the explicit :class:`~scrapy.http.request.copy_strategy.CopyStrategy`
request copy strategy.

Run only these tests with::

    pytest tests/test_request_copy_strategy.py -v

Copy rules
----------

The explicit copy strategy is opt-in: pass a
:class:`~scrapy.http.request.copy_strategy.CopyStrategy` to
:meth:`~scrapy.Request.copy` to use it; the default
:meth:`~scrapy.Request.replace` behavior is unchanged.

The rules are:

* only the meta keys and cb_kwargs keys in the configured allowed-keys
  whitelist are copied;
* immutable values (``str``, ``bytes``, ``int``, ``float``, ``bool`` and
  ``None``) are shared with the original request;
* mutable containers (``dict``, ``list``, ``tuple``, ``set``, ``frozenset``
  and ``bytearray``) are deep-copied, so mutating them on the copy never
  affects the original request;
* values of any other type cause an
  :class:`~scrapy.http.request.copy_strategy.UncopyableValueError` to be
  raised with the full key path of the offending value (e.g.
  ``meta['key'][0]``);
* registered internal protocol meta keys (e.g. retry protocol keys such as
  ``retry_times``) are handled according to their registered rule
  (:class:`~scrapy.http.request.copy_strategy.CopyRule`), so they are not
  casually dropped;
* the callback and errback objects are preserved by identity but never
  executed;
* if the copy fails, the original request is left untouched.

Examples
--------

.. code-block:: python

    from scrapy import Request
    from scrapy.http.request.copy_strategy import CopyStrategy

    strategy = CopyStrategy(
        allowed_meta_keys={"source_url", "depth"},
        allowed_cb_kwargs_keys={"item"},
    )
    new_request = request.copy(strategy=strategy)
"""

from __future__ import annotations

import pytest

from scrapy import Request
from scrapy.http.request.copy_strategy import (
    CopyRule,
    CopyStrategy,
    ProtocolKeyRule,
    UncopyableValueError,
)


class Uncopyable:
    """A value of a type that the explicit copy strategy rejects."""


class TestCopyStrategy:
    def test_deep_copy_nested_meta(self):
        """Mutating nested meta on the copy does not affect the original."""
        request = Request(
            "http://example.com",
            meta={"nested": {"a": [1, 2, 3], "b": {"c": 4}}},
        )
        strategy = CopyStrategy(allowed_meta_keys={"nested"})
        copy = request.copy(strategy=strategy)

        assert copy.meta is not request.meta
        assert copy.meta["nested"] is not request.meta["nested"]
        assert copy.meta["nested"]["a"] is not request.meta["nested"]["a"]

        copy.meta["nested"]["a"].append(4)
        copy.meta["nested"]["b"]["c"] = 5
        assert request.meta["nested"]["a"] == [1, 2, 3]
        assert request.meta["nested"]["b"]["c"] == 4

    def test_deep_copy_cb_kwargs(self):
        """Mutating nested cb_kwargs on the copy does not affect the
        original.
        """
        request = Request(
            "http://example.com",
            cb_kwargs={"item": {"name": "foo", "tags": ["a", "b"]}},
        )
        strategy = CopyStrategy(allowed_cb_kwargs_keys={"item"})
        copy = request.copy(strategy=strategy)

        assert copy.cb_kwargs is not request.cb_kwargs
        assert copy.cb_kwargs["item"] is not request.cb_kwargs["item"]

        copy.cb_kwargs["item"]["tags"].append("c")
        assert request.cb_kwargs["item"]["tags"] == ["a", "b"]

    def test_immutable_values_are_shared(self):
        """Immutable values are shared with the original request."""
        request = Request(
            "http://example.com",
            meta={"s": "str", "i": 1, "f": 1.5, "b": True, "n": None},
        )
        strategy = CopyStrategy(allowed_meta_keys={"s", "i", "f", "b", "n"})
        copy = request.copy(strategy=strategy)

        assert copy.meta["s"] is request.meta["s"]
        assert copy.meta["i"] is request.meta["i"]
        assert copy.meta["f"] is request.meta["f"]
        assert copy.meta["b"] is request.meta["b"]
        assert copy.meta["n"] is request.meta["n"]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ([1, 2, 3], [1, 2, 3]),
            ((1, 2, 3), (1, 2, 3)),
            ({1, 2, 3}, {1, 2, 3}),
            (frozenset({1, 2}), frozenset({1, 2})),
            (bytearray(b"abc"), bytearray(b"abc")),
        ],
    )
    def test_mutable_containers_are_copied(self, value, expected):
        """Mutable containers are deep-copied, not shared."""
        request = Request("http://example.com", meta={"key": value})
        copy = request.copy(strategy=CopyStrategy(allowed_meta_keys={"key"}))

        assert copy.meta["key"] == expected
        assert copy.meta["key"] is not request.meta["key"]

    def test_unauthorized_meta_keys_are_dropped(self):
        """Meta keys not in the whitelist are dropped from the copy."""
        request = Request(
            "http://example.com",
            meta={"allowed": 1, "debug": "secret", "other": 2},
        )
        strategy = CopyStrategy(allowed_meta_keys={"allowed"})
        copy = request.copy(strategy=strategy)

        assert copy.meta == {"allowed": 1}
        assert "debug" not in copy.meta
        assert "other" not in copy.meta

    def test_unauthorized_cb_kwargs_keys_are_dropped(self):
        """Cb_kwargs keys not in the whitelist are dropped from the copy."""
        request = Request(
            "http://example.com",
            cb_kwargs={"allowed": 1, "debug": "secret"},
        )
        strategy = CopyStrategy(allowed_cb_kwargs_keys={"allowed"})
        copy = request.copy(strategy=strategy)

        assert copy.cb_kwargs == {"allowed": 1}
        assert "debug" not in copy.cb_kwargs

    def test_callback_identity_preserved_but_not_executed(self):
        """The callback is preserved by identity and never executed."""
        calls = []

        def callback(response):
            calls.append(1)

        request = Request("http://example.com", callback=callback)
        copy = request.copy(strategy=CopyStrategy())

        assert copy.callback is callback
        assert calls == []

    def test_errback_identity_preserved(self):
        """The errback is preserved by identity."""

        def errback(failure):
            pass

        request = Request("http://example.com", errback=errback)
        copy = request.copy(strategy=CopyStrategy())

        assert copy.errback is errback

    def test_uncopyable_meta_value_reports_key_path(self):
        """An unsupported meta value raises with the full key path."""
        request = Request("http://example.com", meta={"bad": Uncopyable()})
        with pytest.raises(UncopyableValueError, match=r"meta\['bad'\]"):
            request.copy(strategy=CopyStrategy(allowed_meta_keys={"bad"}))

    def test_uncopyable_nested_value_reports_key_path(self):
        """An unsupported nested value raises with the full key path."""
        request = Request(
            "http://example.com",
            meta={"outer": {"inner": [1, Uncopyable()]}},
        )
        with pytest.raises(
            UncopyableValueError, match=r"meta\['outer'\]\['inner'\]\[1\]"
        ):
            request.copy(strategy=CopyStrategy(allowed_meta_keys={"outer"}))

    def test_uncopyable_cb_kwargs_value_reports_key_path(self):
        """An unsupported cb_kwargs value raises with the full key path."""
        request = Request("http://example.com", cb_kwargs={"bad": Uncopyable()})
        with pytest.raises(UncopyableValueError, match=r"cb_kwargs\['bad'\]"):
            request.copy(strategy=CopyStrategy(allowed_cb_kwargs_keys={"bad"}))

    def test_uncopyable_value_is_not_shallow_copied(self):
        """The error is raised instead of shallow-copying the value."""
        obj = Uncopyable()
        request = Request("http://example.com", meta={"bad": obj})
        with pytest.raises(UncopyableValueError):
            request.copy(strategy=CopyStrategy(allowed_meta_keys={"bad"}))
        # the original value is untouched
        assert request.meta["bad"] is obj

    def test_failure_leaves_original_untouched(self):
        """A failed copy leaves the original request untouched."""
        request = Request(
            "http://example.com",
            meta={"good": [1, 2], "bad": Uncopyable()},
            cb_kwargs={"item": {"k": "v"}},
        )
        original_bad = request.meta["bad"]
        with pytest.raises(UncopyableValueError):
            request.copy(
                strategy=CopyStrategy(
                    allowed_meta_keys={"good", "bad"},
                    allowed_cb_kwargs_keys={"item"},
                )
            )
        assert request.meta["good"] == [1, 2]
        assert request.meta["bad"] is original_bad
        assert request.cb_kwargs["item"] == {"k": "v"}

    def test_retry_protocol_keys_are_preserved(self):
        """Registered retry protocol meta keys are not casually dropped."""
        request = Request(
            "http://example.com",
            meta={
                "retry_times": 3,
                "max_retry_times": 5,
                "dont_retry": True,
                "priority_adjust": 2,
                "debug": "secret",
            },
        )
        copy = request.copy(strategy=CopyStrategy())

        assert copy.meta["retry_times"] == 3
        assert copy.meta["max_retry_times"] == 5
        assert copy.meta["dont_retry"] is True
        assert copy.meta["priority_adjust"] == 2
        assert "debug" not in copy.meta

    def test_protocol_key_reset_rule(self):
        """A RESET rule resets the key to its default value."""
        request = Request("http://example.com", meta={"retry_times": 3})
        strategy = CopyStrategy(
            protocol_meta_keys={
                "retry_times": ProtocolKeyRule(CopyRule.RESET, default=0),
            }
        )
        copy = request.copy(strategy=strategy)

        assert copy.meta["retry_times"] == 0

    def test_protocol_key_drop_rule(self):
        """A DROP rule drops the key from the copy."""
        request = Request("http://example.com", meta={"retry_times": 3})
        strategy = CopyStrategy(
            protocol_meta_keys={
                "retry_times": ProtocolKeyRule(CopyRule.DROP),
            }
        )
        copy = request.copy(strategy=strategy)

        assert "retry_times" not in copy.meta

    def test_protocol_key_preserve_rule_deep_copies(self):
        """A PRESERVE rule deep-copies mutable protocol key values."""
        request = Request("http://example.com", meta={"retry_times": [1, 2]})
        copy = request.copy(strategy=CopyStrategy())

        assert copy.meta["retry_times"] == [1, 2]
        assert copy.meta["retry_times"] is not request.meta["retry_times"]

    def test_allowed_key_overrides_protocol_rule(self):
        """An explicitly allowed key is copied even if its protocol rule
        would drop it.
        """
        request = Request("http://example.com", meta={"retry_times": 3})
        strategy = CopyStrategy(
            allowed_meta_keys={"retry_times"},
            protocol_meta_keys={
                "retry_times": ProtocolKeyRule(CopyRule.DROP),
            },
        )
        copy = request.copy(strategy=strategy)

        assert copy.meta["retry_times"] == 3

    def test_default_copy_is_unchanged(self):
        """The default copy() behavior (no strategy) is unchanged."""
        request = Request(
            "http://example.com",
            meta={"nested": {"a": 1}},
            cb_kwargs={"item": {"k": "v"}},
        )
        copy = request.copy()

        assert copy.meta is not request.meta
        assert copy.cb_kwargs is not request.cb_kwargs
        # the default copy shallow-copies, so nested objects are shared
        assert copy.meta["nested"] is request.meta["nested"]
        assert copy.cb_kwargs["item"] is request.cb_kwargs["item"]

    def test_replace_is_unchanged(self):
        """The replace() method is unchanged."""
        request = Request("http://example.com", meta={"nested": {"a": 1}})
        copy = request.replace()

        assert copy.meta is not request.meta
        assert copy.meta["nested"] is request.meta["nested"]

    def test_subclass_is_preserved(self):
        """Subclasses of Request are preserved by the copy."""

        class CustomRequest(Request):
            pass

        request = CustomRequest("http://example.com", meta={"a": 1})
        copy = request.copy(strategy=CopyStrategy(allowed_meta_keys={"a"}))

        assert isinstance(copy, CustomRequest)
        assert copy.meta["a"] == 1

    def test_empty_meta_and_cb_kwargs(self):
        """Copying a request with empty meta and cb_kwargs works."""
        request = Request("http://example.com")
        copy = request.copy(strategy=CopyStrategy())

        assert copy.meta == {}
        assert copy.cb_kwargs == {}

    def test_other_attributes_are_preserved(self):
        """Other request attributes are preserved by the copy."""
        request = Request(
            "http://example.com",
            method="POST",
            body=b"body",
            priority=5,
            dont_filter=True,
            flags=["f1"],
        )
        copy = request.copy(strategy=CopyStrategy())

        assert copy.url == request.url
        assert copy.method == request.method
        assert copy.body == request.body
        assert copy.priority == request.priority
        assert copy.dont_filter is True
        assert copy.flags == request.flags
