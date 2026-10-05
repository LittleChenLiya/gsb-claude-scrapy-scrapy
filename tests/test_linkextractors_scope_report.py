"""Tests for LxmlLinkExtractor.explain_links (read-only link scope report).

Run with:
    python -m pytest tests/test_linkextractors_scope_report.py -q
"""

from __future__ import annotations

import pytest

from scrapy.http import HtmlResponse, Request
from scrapy.linkextractors import LinkExtractor, LinkScopeReport
from scrapy.linkextractors.lxmlhtml import (
    REASON_ACCEPTED,
    REASON_DEDUP,
    REASON_DOMAIN,
    REASON_PATH,
    REASON_PROTOCOL,
)


@pytest.fixture
def redirect_response() -> HtmlResponse:
    """A page obtained after a redirect: the request went to
    ``http://origin.example/start`` but the final response URL is
    ``http://cdn.example/final/index.html``. Relative links must resolve
    against the final URL, without re-requesting anything.
    """
    body = b"""
    <html><body>
        <a href="next.html">next page</a>
        <a href="http://other.example.org/out">external</a>
    </body></html>
    """
    request = Request("http://origin.example/start")
    return HtmlResponse(
        url="http://cdn.example/final/index.html", body=body, request=request
    )


@pytest.fixture
def mixed_response() -> HtmlResponse:
    """A page mixing protocol, domain, path and duplicate cases."""
    body = b"""
    <html><body>
        <a href="http://example.com/dup?a=1&amp;b=2">first ref</a>
        <a href="http://example.com/dup?a=1&amp;b=2">second ref, same address</a>
        <a href="http://example.com/a?b=2&amp;a=1">query order A</a>
        <a href="http://example.com/a?a=1&amp;b=2">query order B</a>
        <a href="http://example.com/page.html#frag">with fragment</a>
        <a href="http://example.com/page.html">without fragment</a>
        <a href="javascript:goToPage(1)">javascript link</a>
        <a href="mailto:someone@example.com">mail link</a>
        <a href="http://blocked.example.net/blocked">blocked domain</a>
        <a href="http://example.com/files/report.pdf">pdf file</a>
        <a href="http://example.com/secret/internal">denied path</a>
    </body></html>
    """
    return HtmlResponse(url="http://example.com/index.html", body=body)


def make_extractor(**kwargs) -> LinkExtractor:
    kwargs.setdefault("allow_domains", ["example.com"])
    kwargs.setdefault("deny", [r"/secret/"])
    return LinkExtractor(**kwargs)


class TestFinalUrlResolution:
    def test_relative_links_resolve_against_final_url(self, redirect_response):
        lx = LinkExtractor()
        report = lx.explain_links(redirect_response)
        assert isinstance(report, LinkScopeReport)
        # the report is based on the final URL, not the original request URL
        assert report.response_url == "http://cdn.example/final/index.html"
        assert redirect_response.request.url == "http://origin.example/start"
        next_page = report.entries[0]
        assert next_page.url == "http://cdn.example/final/next.html"
        assert next_page.accepted
        # same as extract_links, which already resolves against response.url
        assert report.links == lx.extract_links(redirect_response)

    def test_base_url_from_response(self):
        body = b"""
        <html><head><base href="http://base.example/sub/"></head>
        <body><a href="rel.html">rel</a></body></html>
        """
        response = HtmlResponse(url="http://final.example/page", body=body)
        report = LinkExtractor().explain_links(response)
        assert report.base_url == "http://base.example/sub/"
        assert report.entries[0].url == "http://base.example/sub/rel.html"


class TestReasons:
    def test_allow_domains_exclusion_has_reason(self, redirect_response):
        lx = LinkExtractor(allow_domains=["cdn.example"])
        report = lx.explain_links(redirect_response)
        external = report.entries[1]
        assert external.url == "http://other.example.org/out"
        assert not external.accepted
        assert external.reason == REASON_DOMAIN
        assert "other.example.org" in external.detail
        assert "allow_domains" in external.detail

    def test_protocol_checked_before_other_rules(self, mixed_response):
        # allow_domains would also reject these links, but the protocol
        # check runs first and must be the reported reason.
        lx = make_extractor()
        report = lx.explain_links(mixed_response)
        by_url = {e.url: e for e in report.entries}
        js = by_url["javascript:goToPage(1)"]
        assert js.reason == REASON_PROTOCOL
        assert "javascript" in js.detail
        mail = by_url["mailto:someone@example.com"]
        assert mail.reason == REASON_PROTOCOL
        assert "mailto" in mail.detail

    def test_path_reasons(self, mixed_response):
        lx = make_extractor()
        report = lx.explain_links(mixed_response)
        by_url = {e.url: e for e in report.entries}
        denied = by_url["http://example.com/secret/internal"]
        assert denied.reason == REASON_PATH
        assert "deny" in denied.detail
        pdf = by_url["http://example.com/files/report.pdf"]
        assert pdf.reason == REASON_PATH
        assert "extension" in pdf.detail

    def test_allow_pattern_exclusion(self, mixed_response):
        lx = make_extractor(allow=(r"/a\?",))
        report = lx.explain_links(mixed_response)
        page = next(
            e for e in report.entries if e.url == "http://example.com/page.html"
        )
        assert page.reason == REASON_PATH
        assert "allow" in page.detail


class TestDeduplication:
    def test_duplicate_references_keep_their_sources(self, mixed_response):
        lx = make_extractor()
        report = lx.explain_links(mixed_response)
        refs = [e for e in report.entries if e.url == "http://example.com/dup?a=1&b=2"]
        # both occurrences are reported, each with its own source position
        assert len(refs) == 2
        assert refs[0].source.occurrence != refs[1].source.occurrence
        assert refs[0].source.tag == refs[1].source.tag == "a"
        # the current dedup key is the extracted URL itself (fragments
        # included), so the second reference is a duplicate of the first
        assert refs[0].accepted
        assert not refs[1].accepted
        assert refs[1].reason == REASON_DEDUP
        assert str(refs[0].source.occurrence) in refs[1].detail
        # and extract_links only returns the address once
        urls = [link.url for link in lx.extract_links(mixed_response)]
        assert urls.count("http://example.com/dup?a=1&b=2") == 1

    def test_query_arg_order_is_not_normalized(self, mixed_response):
        # the current dedup rule compares the extracted URLs as-is:
        # reordering query arguments does not make two links duplicates
        lx = make_extractor()
        report = lx.explain_links(mixed_response)
        reordered = [
            e
            for e in report.entries
            if e.url in {"http://example.com/a?b=2&a=1", "http://example.com/a?a=1&b=2"}
        ]
        assert len(reordered) == 2
        assert all(e.accepted for e in reordered)

    def test_fragments_are_kept_by_dedup_key(self, mixed_response):
        lx = make_extractor()
        report = lx.explain_links(mixed_response)
        with_frag = next(
            e for e in report.entries if e.url == "http://example.com/page.html#frag"
        )
        without_frag = next(
            e for e in report.entries if e.url == "http://example.com/page.html"
        )
        # fragments are part of the dedup key, so these are not duplicates
        assert with_frag.accepted
        assert without_frag.accepted

    def test_unique_false_keeps_duplicates(self, mixed_response):
        lx = make_extractor(unique=False)
        report = lx.explain_links(mixed_response)
        assert not any(e.reason == REASON_DEDUP for e in report.entries)
        assert report.links == lx.extract_links(mixed_response)


class TestReadOnly:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"allow": ("sample",)},
            {"deny": ("sample1",)},
            {"allow_domains": ["example.com"]},
            {"deny_domains": ["google.com"]},
            {"deny_extensions": ["html"]},
            {"restrict_text": ("sample",)},
            {"canonicalize": True},
            {"unique": False},
            {"restrict_xpaths": ("//body",)},
        ],
    )
    def test_report_matches_extract_links(self, kwargs):
        body = b"""
        <html><body>
            <a href="http://example.com/sample1.html">sample 1</a>
            <a href="http://example.com/sample2.html">sample 2</a>
            <a href="http://example.com/sample2.html">sample 2 again</a>
            <a href="http://www.google.com/something">google</a>
            <a href="ftp://example.com/ftp">ftp</a>
            <a href="javascript:void(0)">js</a>
        </body></html>
        """
        response = HtmlResponse(url="http://example.com/index", body=body)
        lx = LinkExtractor(**kwargs)
        expected = lx.extract_links(response)
        report = lx.explain_links(response)
        # the report is read-only: extract_links output is unchanged and
        # the accepted entries match it exactly
        assert report.links == expected
        assert lx.extract_links(response) == expected
        # every extracted occurrence is accounted for
        assert len(report.entries) == len(report.accepted) + len(report.excluded)

    def test_summary_and_text_report(self, mixed_response):
        lx = make_extractor()
        report = lx.explain_links(mixed_response)
        summary = report.summary()
        assert summary[REASON_ACCEPTED] == len(report.accepted)
        assert summary[REASON_PROTOCOL] == 2
        assert summary[REASON_DOMAIN] == 1
        assert summary[REASON_DEDUP] == 1
        text = report.explain()
        assert "Link scope report for http://example.com/index.html" in text
        assert "excluded:protocol" in text
        assert "excluded:domain" in text
        assert "excluded:dedup" in text
