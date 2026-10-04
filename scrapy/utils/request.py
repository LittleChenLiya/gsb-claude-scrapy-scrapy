"""
This module provides some useful functions for working with
scrapy.Request objects
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Protocol
from urllib.parse import urlunparse
from weakref import WeakKeyDictionary

from w3lib.url import canonicalize_url

from scrapy import Request, Spider
from scrapy.utils.httpobj import urlparse_cached
from scrapy.utils.misc import load_object
from scrapy.utils.python import to_bytes, to_unicode

if TYPE_CHECKING:
    from collections.abc import Iterable

    # typing.Self requires Python 3.11
    from typing_extensions import Self

    from scrapy.crawler import Crawler
    from scrapy.http.request import CookiesT, VerboseCookie

logger = logging.getLogger(__name__)


_fingerprint_cache: WeakKeyDictionary[
    Request, dict[tuple[tuple[bytes, ...] | None, bool, bool], bytes]
] = WeakKeyDictionary()

# Separate from _fingerprint_cache because the cache key includes the
# HeadersMetaRequestFingerprinter policy (selected headers, meta keys, version).
_headers_meta_fingerprint_cache: WeakKeyDictionary[
    Request,
    dict[
        tuple[
            int,
            tuple[bytes, ...] | None,
            tuple[str, ...] | None,
            bool,
            bool,
        ],
        bytes,
    ],
] = WeakKeyDictionary()


def fingerprint(
    request: Request,
    *,
    include_headers: Iterable[bytes | str] | None = None,
    keep_fragments: bool = False,
) -> bytes:
    """
    Return the request fingerprint.

    The request fingerprint is a hash that uniquely identifies the resource the
    request points to. For example, take the following two urls:
    ``http://www.example.com/query?id=111&cat=222``,
    ``http://www.example.com/query?cat=222&id=111``.

    Even though those are two different URLs both point to the same resource
    and are equivalent (i.e. they should return the same response).

    Another example are cookies used to store session ids. Suppose the
    following page is only accessible to authenticated users:
    ``http://www.example.com/members/offers.html``.

    Lots of sites use a cookie to store the session id, which adds a random
    component to the HTTP Request and thus should be ignored when calculating
    the fingerprint.

    For this reason, request headers are ignored by default when calculating
    the fingerprint. If you want to include specific headers use the
    include_headers argument, which is a list of Request headers to include.

    Also, servers usually ignore fragments in urls when handling requests,
    so they are also ignored by default when calculating the fingerprint.
    If you want to include them, set the keep_fragments argument to True
    (for instance when handling requests with a headless browser).

    keep_fragments is not a substitute for rendering JavaScript to reach
    content loaded based on the URL fragment; see
    :ref:`topics-dynamic-content` for that instead.
    """
    processed_include_headers: tuple[bytes, ...] | None = None
    if include_headers:
        processed_include_headers = tuple(
            to_bytes(h.lower()) for h in sorted(include_headers)
        )
    verbatim_url = bool(request.meta.get("verbatim_url"))
    effective_keep_fragments = keep_fragments and not verbatim_url
    cache = _fingerprint_cache.setdefault(request, {})
    cache_key = (processed_include_headers, effective_keep_fragments, verbatim_url)
    if cache_key not in cache:
        # To decode bytes reliably (JSON does not support bytes), regardless of
        # character encoding, we use bytes.hex()
        headers: dict[str, list[str]] = {}
        if processed_include_headers:
            for header in processed_include_headers:
                if header in request.headers:
                    headers[header.hex()] = [
                        header_value.hex()
                        for header_value in request.headers.getlist(header)
                    ]
        if verbatim_url:
            url = request.url
        else:
            url = canonicalize_url(request.url, keep_fragments=keep_fragments)
        fingerprint_data = {
            "method": to_unicode(request.method),
            "url": url,
            "body": (request.body or b"").hex(),
            "headers": headers,
        }
        fingerprint_json = json.dumps(fingerprint_data, sort_keys=True)
        cache[cache_key] = hashlib.sha1(  # noqa: S324
            fingerprint_json.encode()
        ).digest()
    return cache[cache_key]


class RequestFingerprinterProtocol(Protocol):
    def fingerprint(self, request: Request) -> bytes: ...


class RequestFingerprinter:
    """Default fingerprinter.

    It takes into account a canonical version
    (:func:`w3lib.url.canonicalize_url`) of :attr:`request.url
    <scrapy.Request.url>` and the values of :attr:`request.method
    <scrapy.Request.method>` and :attr:`request.body
    <scrapy.Request.body>`, unless :reqmeta:`verbatim_url` is true for that
    request. It then generates an `SHA1 <https://en.wikipedia.org/wiki/SHA-1>`_
    hash.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        return cls(crawler)

    def __init__(self, crawler: Crawler | None = None):
        self._fingerprint = fingerprint

    def fingerprint(self, request: Request) -> bytes:
        return self._fingerprint(request)


def _stable_meta_value(value: Any, path: str) -> Any:
    """Return a JSON-friendly version of a selected ``request.meta`` value.

    Only values that serialize deterministically are accepted. ``bytes`` are
    encoded as hex because JSON has no bytes type, like header values are.
    Unknown object types raise :exc:`TypeError`; their representation is never
    used, since it is not guaranteed to stay stable across processes or runs.
    *path* names the meta entry (with container indices) for the error message.
    """
    # bool is a subclass of int, so it must be checked first.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if isinstance(value, tuple):
        return [
            _stable_meta_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, list):
        return [
            _stable_meta_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        encoded: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError(f"Request meta key {path!r} has a non-string dict key")
            encoded[key] = _stable_meta_value(value[key], f"{path}[{key!r}]")
        return encoded
    raise TypeError(
        f"Request meta key {path!r} has a value of an unsupported type"
        f" {type(value).__name__}"
    )


class HeadersMetaRequestFingerprinter:
    """Optional fingerprinter that also takes selected request headers and
    :attr:`~scrapy.Request.meta` entries into account.

    Requests that share a URL, method and body but belong to different business
    contexts (for example different tenants identified by a header or a meta
    key) get different fingerprints, while requests that only differ in meta
    entries that are not selected keep sharing a fingerprint.

    The URL (canonicalized, as with :class:`RequestFingerprinter`), method and
    body are fingerprinted exactly like with :func:`fingerprint`. Header names
    are matched case-insensitively, and the values of a header with multiple
    values contribute in the order in which the request stores them, so
    different orders produce different fingerprints. Selected meta values must
    be stably serializable: ``None``, booleans, integers, floats, strings,
    bytes (hex-encoded), lists, tuples and dictionaries with string keys. Any
    other type makes :meth:`fingerprint` raise :exc:`TypeError`. The request,
    including its meta, is never modified.

    .. versionadded:: VERSION

    Configure it with the :setting:`REQUEST_FINGERPRINTER_CLASS` setting,
    typically through a subclass that sets the headers and meta keys:

    .. code-block:: python

        from scrapy.utils.request import HeadersMetaRequestFingerprinter


        class RequestFingerprinter(HeadersMetaRequestFingerprinter):
            include_headers = ("X-Tenant",)
            include_meta = ("tenant",)

    Fingerprints carry a policy version marker, so fingerprints stored in a
    :ref:`job directory <job-dir>` by one policy can never silently match
    fingerprints produced by another policy (see :attr:`VERSION`).
    """

    #: Bump (or change) this whenever the configured headers or meta keys, or
    #: the meaning of their values, change. It is part of the fingerprint and
    #: of the cache key, so data deduplicated on disk with the old policy is not
    #: reused after the change.
    VERSION: ClassVar[int] = 1

    #: Default selected headers, used when no *include_headers* argument is
    #: given. Override in a subclass instead of repeating it in ``__init__``.
    include_headers: ClassVar[Iterable[bytes | str] | None] = None

    #: Default selected meta keys, used when no *include_meta* argument is
    #: given. Override in a subclass instead of repeating it in ``__init__``.
    include_meta: ClassVar[Iterable[str] | None] = None

    def __init__(
        self,
        crawler: Crawler | None = None,
        *,
        include_headers: Iterable[bytes | str] | None = None,
        include_meta: Iterable[str] | None = None,
        keep_fragments: bool = False,
    ) -> None:
        if include_headers is None:
            include_headers = type(self).include_headers
        if include_meta is None:
            include_meta = type(self).include_meta
        self._include_headers: tuple[bytes, ...] | None = (
            tuple(to_bytes(h.lower()) for h in sorted(include_headers))
            if include_headers
            else None
        )
        self._include_meta: tuple[str, ...] | None = (
            tuple(sorted(include_meta)) if include_meta else None
        )
        self._keep_fragments = keep_fragments

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        return cls(crawler)

    def fingerprint(self, request: Request) -> bytes:
        verbatim_url = bool(request.meta.get("verbatim_url"))
        cache = _headers_meta_fingerprint_cache.setdefault(request, {})
        cache_key = (
            self.VERSION,
            self._include_headers,
            self._include_meta,
            self._keep_fragments and not verbatim_url,
            verbatim_url,
        )
        if cache_key not in cache:
            # Header names and values are hex-encoded, like in fingerprint(),
            # so that arbitrary bytes survive the JSON encoding.
            headers: dict[str, list[str]] = {}
            if self._include_headers:
                for header in self._include_headers:
                    if header in request.headers:
                        headers[header.hex()] = [
                            value.hex() for value in request.headers.getlist(header)
                        ]
            # Selected meta values are encoded without mutating request.meta.
            # A missing key is encoded like an explicit None so that the two
            # cannot produce different identities.
            meta: dict[str, Any] = {}
            if self._include_meta:
                for key in self._include_meta:
                    meta[key] = _stable_meta_value(request.meta.get(key), key)
            if verbatim_url:
                url = request.url
            else:
                url = canonicalize_url(request.url, keep_fragments=self._keep_fragments)
            fingerprint_data = {
                "method": to_unicode(request.method),
                "url": url,
                "body": (request.body or b"").hex(),
                "headers": headers,
                "meta": meta,
                # Policy markers: changing the version or the selected headers
                # or meta keys changes every fingerprint, which keeps old
                # on-disk dedup data from silently matching new fingerprints.
                "policy": {
                    "version": self.VERSION,
                    "headers": (
                        [header.hex() for header in self._include_headers]
                        if self._include_headers
                        else []
                    ),
                    "meta": list(self._include_meta or []),
                },
            }
            fingerprint_json = json.dumps(fingerprint_data, sort_keys=True)
            cache[cache_key] = hashlib.sha1(  # noqa: S324
                fingerprint_json.encode()
            ).digest()
        return cache[cache_key]


def request_httprepr(request: Request) -> bytes:
    """Return the raw HTTP representation (as bytes) of the given request.
    This is provided only for reference since it's not the actual stream of
    bytes that will be send when performing the request (that's controlled
    by Twisted).
    """
    parsed = urlparse_cached(request)
    path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    s = to_bytes(request.method) + b" " + to_bytes(path) + b" HTTP/1.1\r\n"
    s += b"Host: " + to_bytes(parsed.hostname or b"") + b"\r\n"
    if request.headers:
        s += request.headers.to_string() + b"\r\n"
    s += b"\r\n"
    s += request.body
    return s


def referer_str(request: Request) -> str | None:
    """Return Referer HTTP header suitable for logging."""
    referrer = request.headers.get("Referer")
    if referrer is None:
        return referrer
    return to_unicode(referrer, errors="replace")


def request_from_dict(d: dict[str, Any], *, spider: Spider | None = None) -> Request:
    """Create a :class:`~scrapy.Request` object from a dict.

    If a spider is given, it will try to resolve the callbacks looking at the
    spider for methods with the same name.
    """
    request_cls: type[Request] = load_object(d["_class"]) if "_class" in d else Request
    kwargs = {key: value for key, value in d.items() if key in request_cls.attributes}
    if d.get("callback") and spider:
        kwargs["callback"] = _get_method(spider, d["callback"])
    if d.get("errback") and spider:
        kwargs["errback"] = _get_method(spider, d["errback"])
    return request_cls(**kwargs)


def _get_method(obj: Any, name: object) -> Any:
    """Helper function for request_from_dict"""
    name = str(name)
    try:
        return getattr(obj, name)
    except AttributeError:
        raise ValueError(f"Method {name!r} not found in: {obj}") from None


def _to_verbose_cookies(cookies: CookiesT) -> list[VerboseCookie]:
    """Return a list of verbose cookies from ``request.cookies``.

    The list of dicts form is returned as is, the dict one is converted first.
    """
    if isinstance(cookies, dict):
        return [{"name": k, "value": v} for k, v in cookies.items()]
    return cookies


def _decode_cookie(cookie: VerboseCookie, request: Request) -> dict[str, str] | None:
    """Return a dict with non-flag verbose cookie values converted to strings.

    ``name``, ``value``, ``path``, ``domain`` are included, ``secure`` isn't.
    """

    decoded = {}
    for key in ("name", "value", "path", "domain"):
        value = cookie.get(key)
        if value is None:
            if key in {"name", "value"}:
                logger.warning(
                    f"Invalid cookie found in request {request}:"
                    f" {cookie} ('{key}' is missing)"
                )
                return None
            continue
        if isinstance(value, (bool, float, int, str)):
            decoded[key] = str(value)
        else:
            assert isinstance(value, bytes)
            try:
                decoded[key] = value.decode("utf8")
            except UnicodeDecodeError:
                logger.warning(
                    f"Non UTF-8 encoded cookie found in request {request}: {cookie}",
                )
                decoded[key] = value.decode("latin1", errors="replace")
    return decoded


def request_to_curl(request: Request) -> str:
    """
    Converts a :class:`~scrapy.Request` object to a curl command.

    :param request: Request object to be converted
    :return: string containing the curl command
    """
    method = request.method

    data = f"--data-raw '{request.body.decode('utf-8')}'" if request.body else ""

    headers = " ".join(
        f"-H '{k.decode()}: {v[0].decode()}'" for k, v in request.headers.items()
    )

    url = request.url

    cookie_list: list[VerboseCookie] = _to_verbose_cookies(request.cookies)
    pairs = [
        f"{decoded['name']}={decoded['value']}"
        for c in cookie_list
        if (decoded := _decode_cookie(c, request)) is not None
    ]
    cookies = f"--cookie '{'; '.join(pairs)}'" if pairs else ""

    curl_cmd = f"curl -X {method} {url} {data} {headers} {cookies}".strip()
    return " ".join(curl_cmd.split())
