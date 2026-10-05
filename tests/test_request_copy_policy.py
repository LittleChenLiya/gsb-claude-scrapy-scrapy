"""Tests for the explicit request copy policy.

Run with::

    python -m pytest tests/test_request_copy_policy.py -v
"""

import pytest

from scrapy.http import Request
from scrapy.http.request.copy import (
    DEFAULT_INTERNAL_KEY_RULES,
    RequestCopyPolicy,
    UnsafeRequestCopyError,
    copy_request,
)


def _make_request(**kwargs):
    defaults = {
        "url": "https://example.com/",
        "meta": {
            "item": {"name": "foo", "tags": ["a", "b"]},
            "retry_times": 2,
            "debug_token": "secret",
            "_debug_trace": ["x"],
        },
        "cb_kwargs": {"page": 1, "context": {"steps": ["s1"]}},
    }
    defaults.update(kwargs)
    return Request(**defaults)


class TestCopyPolicyKeys:
    def test_nested_meta_is_deep_copied(self):
        request = _make_request()
        new_request = request.safe_copy()
        new_request.meta["item"]["tags"].append("c")
        new_request.meta["item"]["name"] = "bar"
        assert request.meta["item"] == {"name": "foo", "tags": ["a", "b"]}
        assert new_request.meta["item"] == {"name": "bar", "tags": ["a", "b", "c"]}

    def test_nested_cb_kwargs_is_deep_copied(self):
        request = _make_request()
        new_request = request.safe_copy()
        new_request.cb_kwargs["context"]["steps"].append("s2")
        assert request.cb_kwargs["context"] == {"steps": ["s1"]}
        assert new_request.cb_kwargs["context"] == {"steps": ["s1", "s2"]}

    def test_unauthorized_debug_keys_disappear(self):
        request = _make_request()
        new_request = request.safe_copy()
        assert "debug_token" not in new_request.meta
        assert "_debug_trace" not in new_request.meta
        assert "item" in new_request.meta

    def test_internal_retry_keys_are_kept(self):
        request = _make_request(
            meta={"retry_times": 3, "max_retry_times": 5, "dont_retry": False}
        )
        new_request = request.safe_copy()
        assert new_request.meta["retry_times"] == 3
        assert new_request.meta["max_retry_times"] == 5
        assert new_request.meta["dont_retry"] is False

    def test_internal_keys_survive_allow_list(self):
        # Registered internal keys are not dropped even when an allow list
        # that does not mention them is used.
        policy = RequestCopyPolicy(allowed_meta_keys=frozenset({"item"}))
        request = _make_request(meta={"item": 1, "other": 2, "retry_times": 4})
        new_request = copy_request(request, policy)
        assert new_request.meta == {"item": 1, "retry_times": 4}

    def test_internal_key_drop_rule(self):
        policy = RequestCopyPolicy(internal_key_rules={"retry_times": "drop"})
        request = _make_request(meta={"retry_times": 4, "item": 1})
        new_request = copy_request(request, policy)
        assert "retry_times" not in new_request.meta
        assert new_request.meta["item"] == 1

    def test_allow_lists(self):
        policy = RequestCopyPolicy(
            allowed_meta_keys=frozenset({"item"}),
            allowed_cb_kwargs_keys=frozenset({"page"}),
        )
        request = _make_request()
        new_request = copy_request(request, policy)
        assert set(new_request.meta) == {"item", "retry_times"}
        assert set(new_request.cb_kwargs) == {"page"}

    def test_denied_keys(self):
        policy = RequestCopyPolicy(denied_keys=frozenset({"item"}))
        request = _make_request(meta={"item": 1, "other": 2})
        new_request = copy_request(request, policy)
        assert new_request.meta == {"other": 2}

    def test_default_internal_key_rules(self):
        assert set(DEFAULT_INTERNAL_KEY_RULES) == {
            "retry_times",
            "max_retry_times",
            "dont_retry",
            "retry_reason",
        }
        assert set(DEFAULT_INTERNAL_KEY_RULES.values()) == {"copy"}


class TestCopyPolicyValues:
    def test_callback_identity_preserved_and_not_called(self):
        calls = []

        def callback(response):
            calls.append(response)

        request = _make_request(callback=callback)
        new_request = request.safe_copy()
        assert new_request.callback is callback
        assert len(calls) == 0

    def test_callable_meta_value_shared_not_called(self):
        calls = []

        def lazy():
            calls.append(1)
            return "value"

        request = _make_request(meta={"lazy": lazy})
        new_request = request.safe_copy()
        assert new_request.meta["lazy"] is lazy
        assert len(calls) == 0

    def test_immutable_scalars_shared(self):
        request = _make_request(meta={"n": 1, "s": "x", "b": b"y", "z": None})
        new_request = request.safe_copy()
        assert new_request.meta == {"n": 1, "s": "x", "b": b"y", "z": None}

    def test_containers_copied(self):
        request = _make_request(
            meta={
                "l": [1, [2]],
                "t": (1, [2]),
                "s": {1, 2},
                "f": frozenset({3}),
                "d": {"x": {"y": 1}},
            }
        )
        new_request = request.safe_copy()
        assert new_request.meta == request.meta
        for key in ("l", "t", "s", "f", "d"):
            assert new_request.meta[key] is not request.meta[key]
        assert new_request.meta["l"][1] is not request.meta["l"][1]
        assert new_request.meta["d"]["x"] is not request.meta["d"]["x"]

    def test_unsafe_object_reports_key_path(self):
        class Session:
            pass

        request = _make_request(meta={"session": Session()})
        with pytest.raises(UnsafeRequestCopyError, match=r"meta\['session'\]"):
            request.safe_copy()

    def test_unsafe_nested_object_reports_full_key_path(self):
        request = _make_request(meta={"a": {"b": [object()]}})
        with pytest.raises(UnsafeRequestCopyError, match=r"meta\['a'\]\['b'\]\[0\]"):
            request.safe_copy()

    def test_unsafe_cb_kwargs_object_reports_key_path(self):
        request = _make_request(cb_kwargs={"client": object()})
        with pytest.raises(UnsafeRequestCopyError, match=r"cb_kwargs\['client'\]"):
            request.safe_copy()

    def test_failed_copy_leaves_original_untouched(self):
        meta = {"good": {"x": [1]}, "bad": object()}
        request = _make_request(meta=meta, cb_kwargs={"page": 1})
        with pytest.raises(UnsafeRequestCopyError):
            request.safe_copy()
        assert request.meta == meta
        assert request.meta["good"] is meta["good"]
        assert request.cb_kwargs == {"page": 1}


class TestReplaceUnchanged:
    def test_replace_still_shallow_copies_meta(self):
        request = _make_request()
        new_request = request.replace()
        assert new_request.meta is not request.meta
        # nested objects are still shared by default (unchanged behavior)
        assert new_request.meta["item"] is request.meta["item"]

    def test_copy_delegates_to_replace(self):
        request = _make_request()
        new_request = request.copy()
        assert new_request.meta["item"] is request.meta["item"]

    def test_safe_copy_preserves_other_attributes(self):
        request = _make_request(priority=5, dont_filter=True)
        new_request = request.safe_copy()
        assert new_request.url == request.url
        assert new_request.priority == 5
        assert new_request.dont_filter is True
        assert new_request.meta is not request.meta
