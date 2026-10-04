"""
This module provides some useful functions for working with
scrapy.Request objects
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol
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

# Cache for ContextRequestFingerprinter fingerprints:
# request -> {policy id: fingerprint}
# The policy id (see ContextRequestFingerprinter.policy_id) is part of the
# cache key so that fingerprints computed under different policies (e.g. a
# different version) are never mixed up.
_context_fingerprint_cache: WeakKeyDictionary[Request, dict[bytes, bytes]] = (
    WeakKeyDictionary()
)

#: Default value for the REQUEST_FINGERPRINTER_VERSION setting.
DEFAULT_REQUEST_FINGERPRINTER_VERSION = "1"


def _normalize_include_headers(
    headers: Iterable[bytes | str] | None,
) -> tuple[bytes, ...]:
    """Normalize a list of header names: lowercase, deduplicate and sort.

    Header names are case-insensitive, and sorting them makes the order in
    which the policy lists them irrelevant to the fingerprint.
    """
    if not headers:
        return ()
    return tuple(sorted({to_bytes(h.lower()) for h in headers}))


def _normalize_include_meta(keys: Iterable[str] | None) -> tuple[str, ...]:
    """Normalize a list of meta keys: deduplicate and sort."""
    if not keys:
        return ()
    return tuple(sorted({str(k) for k in keys}))


def _encode_meta_value(value: Any) -> Any:
    """Encode a request meta value into a JSON-stable representation.

    Only values that can be encoded in a fully deterministic, stable way are
    accepted: ``None``, :class:`bool`, :class:`int`, :class:`float`,
    :class:`str`, :class:`bytes`, :class:`list`, :class:`tuple` and
    :class:`dict` (with string keys).

    Any other value raises :class:`TypeError`. In particular, :func:`repr` is
    never used as a fallback, since its output is not guaranteed to be stable
    across Python versions or processes.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"__type__": "bytes", "data": value.hex()}
    if isinstance(value, (list, tuple)):
        return [_encode_meta_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode_meta_value(item) for key, item in value.items()}
    raise TypeError(
        f"Cannot include meta value {value!r} of type "
        f"{type(value).__name__} in the request fingerprint: only None, bool, "
        f"int, float, str, bytes, list, tuple and dict values are supported."
    )


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


class ContextRequestFingerprinter(RequestFingerprinter):
    """Optional request fingerprinter that also takes into account selected
    request headers and selected :attr:`~scrapy.Request.meta` keys.

    The URL, method and body keep following the original fingerprint spec (see
    :class:`RequestFingerprinter`), and the selected headers and meta keys are
    added on top of them. This allows requests that share the same URL, method
    and body but have a different business context (e.g. a different tenant) to
    get different fingerprints.

    The headers to include are configured through the
    ``REQUEST_FINGERPRINTER_INCLUDE_HEADERS`` setting, and the meta keys
    through the ``REQUEST_FINGERPRINTER_INCLUDE_META`` setting. Header names
    are case-insensitive and their order in the policy does not matter (they
    are sorted), while the order of multiple values of a header is the order
    in which they appear in the request.

    Meta values must be stable encodable (``None``, ``bool``, ``int``,
    ``float``, ``str``, ``bytes``, ``list``, ``tuple`` and ``dict``); any
    other value raises :class:`TypeError`.

    The ``REQUEST_FINGERPRINTER_VERSION`` setting (default ``"1"``) is a
    marker of the fingerprinting policy. It is part of both the fingerprint
    itself and the internal cache key, so changing it (or any of the policy
    settings) prevents disk deduplication data from a previous policy from
    being silently reused.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        settings = crawler.settings
        return cls(
            crawler,
            include_headers=settings.getlist("REQUEST_FINGERPRINTER_INCLUDE_HEADERS"),
            include_meta=settings.getlist("REQUEST_FINGERPRINTER_INCLUDE_META"),
            version=settings.get("REQUEST_FINGERPRINTER_VERSION"),
        )

    def __init__(
        self,
        crawler: Crawler | None = None,
        *,
        include_headers: Iterable[bytes | str] | None = None,
        include_meta: Iterable[str] | None = None,
        version: str | int | None = None,
    ) -> None:
        super().__init__(crawler)
        self._include_headers = _normalize_include_headers(include_headers)
        self._include_meta = _normalize_include_meta(include_meta)
        self._version = (
            str(version)
            if version is not None
            else DEFAULT_REQUEST_FINGERPRINTER_VERSION
        )
        self._policy_id = hashlib.sha1(  # noqa: S324
            json.dumps(
                {
                    "version": self._version,
                    "include_headers": [h.decode() for h in self._include_headers],
                    "include_meta": list(self._include_meta),
                },
                sort_keys=True,
            ).encode()
        ).digest()

    @property
    def version(self) -> str:
        """The version marker of this fingerprinting policy."""
        return self._version

    @property
    def policy_id(self) -> bytes:
        """An SHA1 hash identifying this fingerprinting policy.

        It includes the version marker and the normalized lists of headers and
        meta keys, so it changes whenever any policy setting changes. It is
        used as the cache key for the fingerprints computed by this
        fingerprinter.
        """
        return self._policy_id

    def fingerprint(self, request: Request) -> bytes:
        cache = _context_fingerprint_cache.setdefault(request, {})
        if self._policy_id not in cache:
            # The base fingerprint follows the original fingerprint spec:
            # canonical URL, method, body and the selected header values.
            base = fingerprint(
                request,
                include_headers=self._include_headers or None,
            )
            meta: dict[str, Any] = {}
            for key in self._include_meta:
                if key in request.meta:
                    try:
                        meta[key] = _encode_meta_value(request.meta[key])
                    except TypeError as exc:
                        raise TypeError(
                            f"Cannot include meta key {key!r} in the request "
                            f"fingerprint: {exc}"
                        ) from exc
            data = {
                "base": base.hex(),
                "meta": meta,
                "policy": {
                    "version": self._version,
                    "include_headers": [h.decode() for h in self._include_headers],
                    "include_meta": list(self._include_meta),
                },
            }
            cache[self._policy_id] = hashlib.sha1(  # noqa: S324
                json.dumps(data, sort_keys=True).encode()
            ).digest()
        return cache[self._policy_id]


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
