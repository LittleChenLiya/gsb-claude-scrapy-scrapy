from __future__ import annotations

import json
from hashlib import sha1
from typing import TYPE_CHECKING, Any, Protocol
from weakref import WeakKeyDictionary

import pytest

from scrapy.http import Request
from scrapy.utils.python import to_bytes
from scrapy.utils.request import (
    HeadersMetaRequestFingerprinter,
    _fingerprint_cache,
    _headers_meta_fingerprint_cache,
    fingerprint,
    request_httprepr,
    request_to_curl,
)
from scrapy.utils.test import get_crawler

if TYPE_CHECKING:
    from collections.abc import Iterable


@pytest.mark.parametrize(
    ("r", "expected"),
    [
        (
            Request("http://www.example.com"),
            b"GET / HTTP/1.1\r\nHost: www.example.com\r\n\r\n",
        ),
        (
            Request("http://www.example.com/some/page.html?arg=1"),
            b"GET /some/page.html?arg=1 HTTP/1.1\r\nHost: www.example.com\r\n\r\n",
        ),
        (
            Request(
                "http://www.example.com",
                method="POST",
                headers={"Content-type": b"text/html"},
                body=b"Some body",
            ),
            b"POST / HTTP/1.1\r\nHost: www.example.com\r\nContent-Type: text/html\r\n\r\nSome body",
        ),
    ],
)
def test_request_httprepr(r: Request, expected: bytes) -> None:
    assert request_httprepr(r) == expected


@pytest.mark.parametrize(
    "r",
    [
        Request("file:///tmp/foo.txt"),
        Request("ftp://localhost/tmp/foo.txt"),
    ],
)
def test_request_httprepr_for_non_http_request(r: Request) -> None:
    # the representation is not important but it must not fail.
    request_httprepr(r)


class _FingerprintFunction(Protocol):
    def __call__(
        self,
        request: Request,
        *,
        include_headers: Iterable[bytes | str] | None = None,
        keep_fragments: bool = False,
    ) -> bytes: ...


class TestFingerprint:
    function: _FingerprintFunction = staticmethod(fingerprint)
    cache: (
        WeakKeyDictionary[
            Request, dict[tuple[tuple[bytes, ...] | None, bool, bool], bytes]
        ]
        | WeakKeyDictionary[
            Request, dict[tuple[tuple[bytes, ...] | None, bool, bool], str]
        ]
    ) = _fingerprint_cache
    default_cache_key = (None, False, False)
    known_hashes: tuple[tuple[Request, bytes | str, dict[str, Any]], ...] = (
        (
            Request("http://example.org"),
            b"xs\xd7\x0c3uj\x15\xfe\xd7d\x9b\xa9\t\xe0d\xbf\x9cXD",
            {},
        ),
        (
            Request("https://example.org"),
            b"\xc04\x85P,\xaa\x91\x06\xf8t\xb4\xbd*\xd9\xe9\x8a:m\xc3l",
            {},
        ),
        (
            Request("https://example.org?a"),
            b"G\xad\xb8Ck\x19\x1c\xed\x838,\x01\xc4\xde;\xee\xa5\x94a\x0c",
            {},
        ),
        (
            Request("https://example.org?a=b"),
            b"\x024MYb\x8a\xc2\x1e\xbc>\xd6\xac*\xda\x9cF\xc1r\x7f\x17",
            {},
        ),
        (
            Request("https://example.org?a=b&a"),
            b"t+\xe8*\xfb\x84\xe3v\x1a}\x88p\xc0\xccB\xd7\x9d\xfez\x96",
            {},
        ),
        (
            Request("https://example.org?a=b&a=c"),
            b"\xda\x1ec\xd0\x9c\x08s`\xb4\x9b\xe2\xb6R\xf8k\xef\xeaQG\xef",
            {},
        ),
        (
            Request("https://example.org", method="POST"),
            b"\x9d\xcdA\x0fT\x02:\xca\xa0}\x90\xda\x05B\xded\x8aN7\x1d",
            {},
        ),
        (
            Request("https://example.org", body=b"a"),
            b"\xc34z>\xd8\x99\x8b\xda7\x05r\x99I\xa8\xa0x;\xa41_",
            {},
        ),
        (
            Request("https://example.org", method="POST", body=b"a"),
            b"5`\xe2y4\xd0\x9d\xee\xe0\xbatw\x87Q\xe8O\xd78\xfc\xe7",
            {},
        ),
        (
            Request("https://example.org#a", headers={"A": b"B"}),
            b"\xc04\x85P,\xaa\x91\x06\xf8t\xb4\xbd*\xd9\xe9\x8a:m\xc3l",
            {},
        ),
        (
            Request("https://example.org#a", headers={"A": b"B"}),
            b"]\xc7\x1f\xf2\xafG2\xbc\xa4\xfa\x99\n33\xda\x18\x94\x81U.",
            {"include_headers": ["A"]},
        ),
        (
            Request("https://example.org#a", headers={"A": b"B"}),
            b"<\x1a\xeb\x85y\xdeW\xfb\xdcq\x88\xee\xaf\x17\xdd\x0c\xbfH\x18\x1f",
            {"keep_fragments": True},
        ),
        (
            Request("https://example.org#a", headers={"A": b"B"}),
            b"\xc1\xef~\x94\x9bS\xc1\x83\t\xdcz8\x9f\xdc{\x11\x16I.\x11",
            {"include_headers": ["A"], "keep_fragments": True},
        ),
        (
            Request("https://example.org/ab"),
            b"N\xe5l\xb8\x12@iw\xe2\xf3\x1bp\xea\xffp!u\xe2\x8a\xc6",
            {},
        ),
        (
            Request("https://example.org/a", body=b"b"),
            b"_NOv\xbco$6\xfcW\x9f\xb24g\x9f\xbb\xdd\xa82\xc5",
            {},
        ),
    )

    def test_query_string_key_order(self):
        r1 = Request("http://www.example.com/query?id=111&cat=222")
        r2 = Request("http://www.example.com/query?cat=222&id=111")
        assert self.function(r1) == self.function(r1)
        assert self.function(r1) == self.function(r2)

    def test_query_string_key_without_value(self):
        r1 = Request("http://www.example.com/hnnoticiaj1.aspx?78132,199")
        r2 = Request("http://www.example.com/hnnoticiaj1.aspx?78160,199")
        assert self.function(r1) != self.function(r2)

    def test_caching(self):
        r1 = Request("http://www.example.com/hnnoticiaj1.aspx?78160,199")
        assert self.function(r1) == self.cache[r1][self.default_cache_key]

    def test_header(self):
        r1 = Request("http://www.example.com/members/offers.html")
        r2 = Request("http://www.example.com/members/offers.html")
        r2.headers["SESSIONID"] = b"somehash"
        assert self.function(r1) == self.function(r2)

    def test_headers(self):
        r1 = Request("http://www.example.com/")
        r2 = Request("http://www.example.com/")
        r2.headers["Accept-Language"] = b"en"
        r3 = Request("http://www.example.com/")
        r3.headers["Accept-Language"] = b"en"
        r3.headers["SESSIONID"] = b"somehash"

        assert self.function(r1) == self.function(r2) == self.function(r3)

        assert self.function(r1) == self.function(
            r1, include_headers=["Accept-Language"]
        )

        assert self.function(r1) != self.function(
            r2, include_headers=["Accept-Language"]
        )

        assert self.function(
            r3, include_headers=["accept-language", "sessionid"]
        ) == self.function(r3, include_headers=["SESSIONID", "Accept-Language"])

    def test_fragment(self):
        r1 = Request("http://www.example.com/test.html")
        r2 = Request("http://www.example.com/test.html#fragment")
        assert self.function(r1) == self.function(r2)
        assert self.function(r1) == self.function(r1, keep_fragments=True)
        assert self.function(r2) != self.function(r2, keep_fragments=True)
        assert self.function(r1) != self.function(r2, keep_fragments=True)

    def test_verbatim_url(self):
        # verbatim_url requests skip URL canonicalization
        r1 = Request(
            "http://www.example.com/query?a=1&b=2", meta={"verbatim_url": True}
        )
        r2 = Request(
            "http://www.example.com/query?b=2&a=1", meta={"verbatim_url": True}
        )
        assert self.function(r1) != self.function(r2)

        # without verbatim_url, canonicalization makes query-param order irrelevant
        r3 = Request("http://www.example.com/query?a=1&b=2")
        r4 = Request("http://www.example.com/query?b=2&a=1")
        assert self.function(r3) == self.function(r4)

        # with verbatim_url, the fragment is always kept in the fingerprint
        r5 = Request(
            "http://www.example.com/test#fragment", meta={"verbatim_url": True}
        )
        r6 = Request("http://www.example.com/test", meta={"verbatim_url": True})
        assert self.function(r5) != self.function(r6)

        # keep_fragments parameter is ignored for verbatim_url requests
        assert self.function(r5) == self.function(r5, keep_fragments=True)
        assert self.function(r5) == self.function(r5, keep_fragments=False)

    def test_method_and_body(self):
        r1 = Request("http://www.example.com")
        r2 = Request("http://www.example.com", method="POST")
        r3 = Request("http://www.example.com", method="POST", body=b"request body")

        assert self.function(r1) != self.function(r2)
        assert self.function(r2) != self.function(r3)

    def test_request_replace(self):
        # cached fingerprint must be cleared on request copy
        r1 = Request("http://www.example.com")
        fp1 = self.function(r1)
        r2 = r1.replace(url="http://www.example.com/other")
        fp2 = self.function(r2)
        assert fp1 != fp2

    def test_part_separation(self):
        # An old implementation used to serialize request data in a way that
        # would put the body right after the URL.
        r1 = Request("http://www.example.com/foo")
        fp1 = self.function(r1)
        r2 = Request("http://www.example.com/f", body=b"oo")
        fp2 = self.function(r2)
        assert fp1 != fp2

    def test_hashes(self):
        """Test hardcoded hashes, to make sure future changes to not introduce
        backward incompatibilities."""
        actual = [
            self.function(request, **kwargs) for request, _, kwargs in self.known_hashes
        ]
        expected = [_fingerprint for _, _fingerprint, _ in self.known_hashes]
        assert actual == expected


class TestRequestFingerprinter:
    def test_fingerprint(self):
        crawler = get_crawler()
        request = Request("https://example.com")
        assert crawler.request_fingerprinter.fingerprint(request) == fingerprint(
            request
        )


class _TenantFingerprinter(HeadersMetaRequestFingerprinter):
    include_headers = ("X-Tenant",)
    include_meta = ("tenant",)


class TestHeadersMetaRequestFingerprinter:
    def test_tenant_meta_distinguishes(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", meta={"tenant": "a"})
        r2 = Request("http://example.com/api", meta={"tenant": "b"})
        assert fingerprinter.fingerprint(r1) != fingerprinter.fingerprint(r2)

    def test_tenant_header_distinguishes(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", headers={"X-Tenant": "a"})
        r2 = Request("http://example.com/api", headers={"X-Tenant": "b"})
        assert fingerprinter.fingerprint(r1) != fingerprinter.fingerprint(r2)

    def test_unlisted_meta_ignored(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request(
            "http://example.com/api",
            meta={"tenant": "a", "debug": "x"},
        )
        r2 = Request(
            "http://example.com/api",
            meta={"tenant": "a", "debug": "y"},
        )
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_unlisted_headers_ignored(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", headers={"X-Debug": "x"})
        r2 = Request("http://example.com/api", headers={"X-Debug": "y"})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_header_name_case_insensitive(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", headers={"X-Tenant": "a"})
        r2 = Request("http://example.com/api", headers={"x-tenant": "a"})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_header_name_declaration_case_insensitive(self):
        r = Request("http://example.com/api", headers={"X-Tenant": "a"})
        upper = HeadersMetaRequestFingerprinter(include_headers=["X-TENANT"])
        lower = HeadersMetaRequestFingerprinter(include_headers=["x-tenant"])
        assert upper.fingerprint(r) == lower.fingerprint(r)

    def test_multiple_header_value_order_matters(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request(
            "http://example.com/api",
            headers=[("X-Tenant", b"a"), ("X-Tenant", b"b")],
        )
        r2 = Request(
            "http://example.com/api",
            headers=[("X-Tenant", b"b"), ("X-Tenant", b"a")],
        )
        assert fingerprinter.fingerprint(r1) != fingerprinter.fingerprint(r2)

    def test_same_meta_same_fingerprint(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", meta={"tenant": "a"})
        r2 = Request("http://example.com/api", meta={"tenant": "a"})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_url_canonicalized(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/query?a=1&b=2")
        r2 = Request("http://example.com/query?b=2&a=1")
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_method_and_body(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com")
        r2 = Request("http://example.com", method="POST")
        r3 = Request("http://example.com", method="POST", body=b"body")
        assert fingerprinter.fingerprint(r1) != fingerprinter.fingerprint(r2)
        assert fingerprinter.fingerprint(r2) != fingerprinter.fingerprint(r3)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            1,
            1.5,
            "a",
            b"a",
            [1, "a", None],
            (1, "a"),
            {"b": 2, "a": [1, 2]},
        ],
    )
    def test_stable_meta_values(self, value: Any) -> None:
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", meta={"tenant": value})
        r2 = Request("http://example.com/api", meta={"tenant": value})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_list_and_tuple_meta_equivalent(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", meta={"tenant": [1, 2]})
        r2 = Request("http://example.com/api", meta={"tenant": (1, 2)})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_dict_meta_key_order_insensitive(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/api", meta={"tenant": {"a": 1, "b": 2}})
        r2 = Request("http://example.com/api", meta={"tenant": {"b": 2, "a": 1}})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    @pytest.mark.parametrize("value", [object(), {"a", "b"}, frozenset("ab")])
    def test_unsupported_meta_value_raises(self, value: Any) -> None:
        fingerprinter = _TenantFingerprinter()
        request = Request("http://example.com/api", meta={"tenant": value})
        with pytest.raises(TypeError, match="Request meta key 'tenant'"):
            fingerprinter.fingerprint(request)

    def test_unsupported_nested_meta_value_raises(self):
        fingerprinter = _TenantFingerprinter()
        request = Request(
            "http://example.com/api",
            meta={"tenant": {"a": [1, object()]}},
        )
        with pytest.raises(TypeError, match=r"Request meta key \"tenant\['a'\]\[1\]\""):
            fingerprinter.fingerprint(request)

    def test_unsupported_meta_dict_key_raises(self):
        fingerprinter = _TenantFingerprinter()
        request = Request(
            "http://example.com/api",
            meta={"tenant": {1: "a"}},
        )
        with pytest.raises(TypeError, match="non-string dict key"):
            fingerprinter.fingerprint(request)

    def test_unsupported_value_repr_not_used(self):
        class Weird:
            def __repr__(self) -> str:
                raise AssertionError("repr() must not be called")

        fingerprinter = _TenantFingerprinter()
        request = Request("http://example.com/api", meta={"tenant": Weird()})
        # Raising AssertionError from __repr__ would surface instead of the
        # TypeError if the value's representation were read.
        with pytest.raises(TypeError, match="Request meta key 'tenant'"):
            fingerprinter.fingerprint(request)

    def test_request_not_mutated(self):
        nested = {"ids": [1, 2]}
        sentinel = object()
        request = Request(
            "http://example.com/api",
            meta={"tenant": nested, "debug": sentinel},
        )
        fingerprinter = _TenantFingerprinter()
        fingerprinter.fingerprint(request)
        assert request.meta["tenant"] is nested
        assert request.meta["tenant"]["ids"] == [1, 2]
        assert request.meta["debug"] is sentinel
        assert set(request.meta) >= {"tenant", "debug"}

    def test_copy_gets_new_cache_entry(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com", meta={"tenant": "a"})
        fp1 = fingerprinter.fingerprint(r1)
        r2 = r1.replace(meta={"tenant": "b"})
        fp2 = fingerprinter.fingerprint(r2)
        assert fp1 != fp2
        cache = _headers_meta_fingerprint_cache
        assert r1 in cache
        assert r2 in cache

    def test_caching_uses_policy_version_in_key(self):
        fingerprinter = _TenantFingerprinter()
        request = Request("http://example.com", meta={"tenant": "a"})
        fingerprinter.fingerprint(request)
        cache_key = (
            _TenantFingerprinter.VERSION,
            fingerprinter._include_headers,
            fingerprinter._include_meta,
            False,
            False,
        )
        assert cache_key in _headers_meta_fingerprint_cache[request]

    def test_caching_keeps_distinct_versions_apart(self):
        request = Request("http://example.com", meta={"tenant": "a"})

        class TenantFingerprinterV2(_TenantFingerprinter):
            VERSION = 2

        v1 = _TenantFingerprinter()
        v2 = TenantFingerprinterV2()
        fp1 = v1.fingerprint(request)
        fp2 = v2.fingerprint(request)
        # Recomputing after the other policy used the cache must return each
        # policy's own fingerprint, not the cached entry of the other one.
        assert v2.fingerprint(request) == fp2
        assert v1.fingerprint(request) == fp1
        cache = _headers_meta_fingerprint_cache[request]
        assert cache[(1, v1._include_headers, v1._include_meta, False, False)] == fp1
        assert cache[(2, v2._include_headers, v2._include_meta, False, False)] == fp2

    def test_version_changes_fingerprint(self):
        fingerprinter_v1 = _TenantFingerprinter()

        class TenantFingerprinterV2(_TenantFingerprinter):
            VERSION = 2

        fingerprinter_v2 = TenantFingerprinterV2()
        request = Request("http://example.com", meta={"tenant": "a"})
        assert fingerprinter_v1.fingerprint(request) != fingerprinter_v2.fingerprint(
            request
        )

    def test_policy_change_changes_fingerprint(self):
        tenant_only = HeadersMetaRequestFingerprinter(include_meta=("tenant",))
        tenant_and_org = HeadersMetaRequestFingerprinter(include_meta=("org", "tenant"))
        r1 = Request("http://example.com", meta={"tenant": "a", "org": "x"})
        r2 = Request("http://example.com", meta={"tenant": "a", "org": "y"})
        assert tenant_only.fingerprint(r1) == tenant_only.fingerprint(r2)
        assert tenant_and_org.fingerprint(r1) != tenant_and_org.fingerprint(r2)

    def test_missing_meta_key(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com")
        r2 = Request("http://example.com", meta={"tenant": None})
        assert fingerprinter.fingerprint(r1) == fingerprinter.fingerprint(r2)

    def test_verbatim_url(self):
        fingerprinter = _TenantFingerprinter()
        r1 = Request("http://example.com/query?a=1&b=2", meta={"verbatim_url": True})
        r2 = Request("http://example.com/query?b=2&a=1", meta={"verbatim_url": True})
        assert fingerprinter.fingerprint(r1) != fingerprinter.fingerprint(r2)

    def test_from_crawler(self):
        crawler = get_crawler(
            settings_dict={"REQUEST_FINGERPRINTER_CLASS": _TenantFingerprinter}
        )
        fingerprinter = crawler.request_fingerprinter
        assert isinstance(fingerprinter, _TenantFingerprinter)
        r1 = Request("http://example.com", meta={"tenant": "a"})
        r2 = Request("http://example.com", meta={"tenant": "b"})
        assert fingerprinter.fingerprint(r1) != fingerprinter.fingerprint(r2)


class TestCustomRequestFingerprinter:
    def test_include_headers(self):
        class RequestFingerprinter:
            def fingerprint(self, request):
                return fingerprint(request, include_headers=["X-ID"])

        settings = {
            "REQUEST_FINGERPRINTER_CLASS": RequestFingerprinter,
        }
        crawler = get_crawler(settings_dict=settings)

        r1 = Request("http://www.example.com", headers={"X-ID": "1"})
        fp1 = crawler.request_fingerprinter.fingerprint(r1)
        r2 = Request("http://www.example.com", headers={"X-ID": "2"})
        fp2 = crawler.request_fingerprinter.fingerprint(r2)
        assert fp1 != fp2

    def test_dont_canonicalize(self):
        class RequestFingerprinter:
            cache: WeakKeyDictionary[Request, bytes] = WeakKeyDictionary()

            def fingerprint(self, request: Request) -> bytes:
                if request not in self.cache:
                    fp = sha1()
                    fp.update(to_bytes(request.url))
                    self.cache[request] = fp.digest()
                return self.cache[request]

        settings = {
            "REQUEST_FINGERPRINTER_CLASS": RequestFingerprinter,
        }
        crawler = get_crawler(settings_dict=settings)

        r1 = Request("http://www.example.com?a=1&a=2")
        fp1 = crawler.request_fingerprinter.fingerprint(r1)
        r2 = Request("http://www.example.com?a=2&a=1")
        fp2 = crawler.request_fingerprinter.fingerprint(r2)
        assert fp1 != fp2

    def test_meta(self):
        class RequestFingerprinter:
            def fingerprint(self, request):
                if "fingerprint" in request.meta:
                    return request.meta["fingerprint"]
                return fingerprint(request)

        settings = {
            "REQUEST_FINGERPRINTER_CLASS": RequestFingerprinter,
        }
        crawler = get_crawler(settings_dict=settings)

        r1 = Request("http://www.example.com")
        fp1 = crawler.request_fingerprinter.fingerprint(r1)
        r2 = Request("http://www.example.com", meta={"fingerprint": "a"})
        fp2 = crawler.request_fingerprinter.fingerprint(r2)
        r3 = Request("http://www.example.com", meta={"fingerprint": "a"})
        fp3 = crawler.request_fingerprinter.fingerprint(r3)
        r4 = Request("http://www.example.com", meta={"fingerprint": "b"})
        fp4 = crawler.request_fingerprinter.fingerprint(r4)
        assert fp1 != fp2
        assert fp1 != fp4
        assert fp2 != fp4
        assert fp2 == fp3

    def test_from_crawler(self):
        class RequestFingerprinter:
            @classmethod
            def from_crawler(cls, crawler):
                return cls(crawler)

            def __init__(self, crawler):
                self._fingerprint = crawler.settings["FINGERPRINT"]

            def fingerprint(self, request):
                return self._fingerprint

        settings = {
            "REQUEST_FINGERPRINTER_CLASS": RequestFingerprinter,
            "FINGERPRINT": b"fingerprint",
        }
        crawler = get_crawler(settings_dict=settings)

        request = Request("http://www.example.com")
        fingerprint = crawler.request_fingerprinter.fingerprint(request)
        assert fingerprint == settings["FINGERPRINT"]


class TestRequestToCurl:
    def _test_request(
        self, request_object: Request, expected_curl_command: str
    ) -> None:
        curl_command = request_to_curl(request_object)
        assert curl_command == expected_curl_command

    def test_get(self) -> None:
        request_object = Request("https://www.example.com")
        expected_curl_command = "curl -X GET https://www.example.com"
        self._test_request(request_object, expected_curl_command)

    def test_post(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            'curl -X POST https://www.httpbin.org/post --data-raw \'{"foo": "bar"}\''
        )
        self._test_request(request_object, expected_curl_command)

    def test_headers(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            "curl -X POST https://www.httpbin.org/post"
            ' --data-raw \'{"foo": "bar"}\''
            " -H 'Content-Type: application/json' -H 'Accept: application/json'"
        )
        self._test_request(request_object, expected_curl_command)

    def test_cookies_dict(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            cookies={"foo": "bar"},
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            "curl -X POST https://www.httpbin.org/post"
            " --data-raw '{\"foo\": \"bar\"}' --cookie 'foo=bar'"
        )
        self._test_request(request_object, expected_curl_command)

    def test_cookies_dict_bytes(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            cookies={b"foo": b"bar"},
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            "curl -X POST https://www.httpbin.org/post"
            " --data-raw '{\"foo\": \"bar\"}' --cookie 'foo=bar'"
        )
        self._test_request(request_object, expected_curl_command)

    def test_cookies_list_verbose(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            cookies=[
                {
                    "name": b"foo",
                    "value": b"bar",
                    "domain": "example.com",
                    "path": "/",
                    "secure": True,
                }
            ],
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            "curl -X POST https://www.httpbin.org/post"
            " --data-raw '{\"foo\": \"bar\"}' --cookie 'foo=bar'"
        )
        self._test_request(request_object, expected_curl_command)

    def test_cookies_list_verbose_non_string_value(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            cookies=[
                {
                    "name": "foo",
                    "value": 1,
                    "domain": "example.com",
                    "path": "/",
                    "secure": True,
                }
            ],
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            "curl -X POST https://www.httpbin.org/post"
            " --data-raw '{\"foo\": \"bar\"}' --cookie 'foo=1'"
        )
        self._test_request(request_object, expected_curl_command)

    def test_request_to_curl_method(self) -> None:
        request_object = Request(
            "https://www.httpbin.org/post",
            method="POST",
            body=json.dumps({"foo": "bar"}),
        )
        expected_curl_command = (
            'curl -X POST https://www.httpbin.org/post --data-raw \'{"foo": "bar"}\''
        )
        assert request_object.to_curl() == expected_curl_command
