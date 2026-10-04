from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from scrapy.core.scheduler import Scheduler
from scrapy.dupefilters import BaseDupeFilter, RFPDupeFilter
from scrapy.exceptions import ScrapyDeprecationWarning
from scrapy.http import Request
from scrapy.utils.misc import build_from_crawler
from scrapy.utils.python import to_bytes
from scrapy.utils.request import HeadersMetaRequestFingerprinter
from scrapy.utils.test import get_crawler
from tests.spiders import SimpleSpider

if TYPE_CHECKING:
    from scrapy.crawler import Crawler


def _get_dupefilter(
    *,
    crawler: Crawler | None = None,
    settings: dict[str, Any] | None = None,
    open_: bool = True,
) -> BaseDupeFilter:
    if crawler is None:
        crawler = get_crawler(settings_dict=settings)
    scheduler = build_from_crawler(Scheduler, crawler)
    dupefilter = scheduler.df
    if open_:
        dupefilter.open()
    return dupefilter


class FromCrawlerRFPDupeFilter(RFPDupeFilter):
    method: str

    @classmethod
    def from_crawler(cls, crawler):
        df = super().from_crawler(crawler)
        df.method = "from_crawler"
        return df


class DirectDupeFilter:
    method = "n/a"


class TestRFPDupeFilter:
    def test_df_from_crawler_scheduler(self):
        settings = {
            "DUPEFILTER_DEBUG": True,
            "DUPEFILTER_CLASS": FromCrawlerRFPDupeFilter,
        }
        crawler = get_crawler(settings_dict=settings)
        scheduler = build_from_crawler(Scheduler, crawler)
        dupefilter = cast("FromCrawlerRFPDupeFilter", scheduler.df)
        assert dupefilter.debug
        assert dupefilter.method == "from_crawler"

    def test_df_direct_scheduler(self):
        settings = {
            "DUPEFILTER_CLASS": DirectDupeFilter,
        }
        crawler = get_crawler(settings_dict=settings)
        scheduler = build_from_crawler(Scheduler, crawler)
        assert cast("DirectDupeFilter", scheduler.df).method == "n/a"

    def test_filter(self):
        dupefilter = _get_dupefilter()
        r1 = Request("http://scrapytest.org/1")
        r2 = Request("http://scrapytest.org/2")
        r3 = Request("http://scrapytest.org/2")

        assert not dupefilter.request_seen(r1)
        assert dupefilter.request_seen(r1)

        assert not dupefilter.request_seen(r2)
        assert dupefilter.request_seen(r3)

        dupefilter.close("finished")

    def test_dupefilter_path(self):
        r1 = Request("http://scrapytest.org/1")
        r2 = Request("http://scrapytest.org/2")

        path = tempfile.mkdtemp()
        try:
            df = _get_dupefilter(settings={"JOBDIR": path}, open_=False)
            try:
                df.open()
                assert not df.request_seen(r1)
                assert df.request_seen(r1)
            finally:
                df.close("finished")

            df2 = _get_dupefilter(settings={"JOBDIR": path}, open_=False)
            assert df is not df2
            try:
                df2.open()
                assert df2.request_seen(r1)
                assert not df2.request_seen(r2)
                assert df2.request_seen(r2)
            finally:
                df2.close("finished")
        finally:
            shutil.rmtree(path)

    def test_request_fingerprint(self):
        """Test if customization of request_fingerprint method will change
        output of request_seen.

        """
        dupefilter = _get_dupefilter()
        r1 = Request("http://scrapytest.org/index.html")
        r2 = Request("http://scrapytest.org/INDEX.html")

        assert not dupefilter.request_seen(r1)
        assert not dupefilter.request_seen(r2)

        dupefilter.close("finished")

        class RequestFingerprinter:
            def fingerprint(self, request):
                fp = hashlib.sha1()
                fp.update(to_bytes(request.url.lower()))
                return fp.digest()

        settings = {"REQUEST_FINGERPRINTER_CLASS": RequestFingerprinter}
        case_insensitive_dupefilter = _get_dupefilter(settings=settings)

        assert not case_insensitive_dupefilter.request_seen(r1)
        assert case_insensitive_dupefilter.request_seen(r2)

        case_insensitive_dupefilter.close("finished")

    def test_seenreq_truncated(self):
        r1 = Request("http://scrapytest.org/1")
        r2 = Request("http://scrapytest.org/2")

        path = tempfile.mkdtemp()
        try:
            df = _get_dupefilter(settings={"JOBDIR": path}, open_=False)
            try:
                df.open()
                df.request_seen(r1)
                df.request_seen(r2)
            finally:
                df.close("finished")

            seen_file = Path(path, "requests.seen")
            seen_file.write_bytes(seen_file.read_bytes()[:-1])

            df2 = _get_dupefilter(settings={"JOBDIR": path}, open_=False)
            try:
                df2.open()
                assert df2.request_seen(r1)
                assert not df2.request_seen(r2)
            finally:
                df2.close("finished")
        finally:
            shutil.rmtree(path)

    def test_headers_meta_fingerprinter_persists(self):
        class TenantFingerprinter(HeadersMetaRequestFingerprinter):
            include_meta = ("tenant",)

        settings = {"REQUEST_FINGERPRINTER_CLASS": TenantFingerprinter}
        r1 = Request("http://scrapytest.org/1", meta={"tenant": "a"})
        r2 = Request("http://scrapytest.org/2", meta={"tenant": "a"})
        r1_other_tenant = Request("http://scrapytest.org/1", meta={"tenant": "b"})

        path = tempfile.mkdtemp()
        try:
            df = _get_dupefilter(settings={**settings, "JOBDIR": path}, open_=False)
            try:
                df.open()
                assert not df.request_seen(r1)
                assert df.request_seen(r1)
            finally:
                df.close("finished")

            # The same request, including its business context, is a duplicate
            # after the filter is reopened from the job directory.
            df2 = _get_dupefilter(settings={**settings, "JOBDIR": path}, open_=False)
            try:
                df2.open()
                assert df2.request_seen(r1)
                assert not df2.request_seen(r1_other_tenant)
                assert not df2.request_seen(r2)
            finally:
                df2.close("finished")
        finally:
            shutil.rmtree(path)

    def test_headers_meta_fingerprinter_version_invalidates_on_disk(self):
        class TenantFingerprinterV1(HeadersMetaRequestFingerprinter):
            include_meta = ("tenant",)

        class TenantFingerprinterV2(TenantFingerprinterV1):
            VERSION = 2

        request = Request("http://scrapytest.org/1", meta={"tenant": "a"})

        path = tempfile.mkdtemp()
        try:
            df = _get_dupefilter(
                settings={
                    "REQUEST_FINGERPRINTER_CLASS": TenantFingerprinterV1,
                    "JOBDIR": path,
                },
                open_=False,
            )
            try:
                df.open()
                assert not df.request_seen(request)
            finally:
                df.close("finished")

            # With a bumped policy version, fingerprints stored on disk must not
            # silently match: the request is seen as new again.
            df2 = _get_dupefilter(
                settings={
                    "REQUEST_FINGERPRINTER_CLASS": TenantFingerprinterV2,
                    "JOBDIR": path,
                },
                open_=False,
            )
            try:
                df2.open()
                assert not df2.request_seen(request)
                assert df2.request_seen(request)
            finally:
                df2.close("finished")
        finally:
            shutil.rmtree(path)

    def test_headers_meta_fingerprinter_policy_change_invalidates_on_disk(self):
        class TenantFingerprinter(HeadersMetaRequestFingerprinter):
            include_meta = ("tenant",)

        class TenantOrgFingerprinter(HeadersMetaRequestFingerprinter):
            include_meta = ("org", "tenant")

        request = Request("http://scrapytest.org/1", meta={"tenant": "a", "org": "x"})

        path = tempfile.mkdtemp()
        try:
            df = _get_dupefilter(
                settings={
                    "REQUEST_FINGERPRINTER_CLASS": TenantFingerprinter,
                    "JOBDIR": path,
                },
                open_=False,
            )
            try:
                df.open()
                assert not df.request_seen(request)
            finally:
                df.close("finished")

            # Selecting a different set of meta keys also prevents on-disk
            # fingerprints from being silently reused.
            df2 = _get_dupefilter(
                settings={
                    "REQUEST_FINGERPRINTER_CLASS": TenantOrgFingerprinter,
                    "JOBDIR": path,
                },
                open_=False,
            )
            try:
                df2.open()
                assert not df2.request_seen(request)
            finally:
                df2.close("finished")
        finally:
            shutil.rmtree(path)

    def test_log(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = {
            "DUPEFILTER_DEBUG": False,
            "DUPEFILTER_CLASS": FromCrawlerRFPDupeFilter,
        }
        crawler = get_crawler(SimpleSpider, settings_dict=settings)
        spider = SimpleSpider.from_crawler(crawler)
        dupefilter = _get_dupefilter(crawler=crawler)

        r1 = Request("http://scrapytest.org/index.html")
        r2 = Request("http://scrapytest.org/index.html")

        with caplog.at_level(logging.DEBUG):
            dupefilter.log(r1, spider)
            dupefilter.log(r2, spider)

        assert crawler.stats.get_value("dupefilter/filtered") == 2
        assert (
            "scrapy.dupefilters",
            logging.DEBUG,
            (
                "Filtered duplicate request: <GET http://scrapytest.org/index.html> - no more"
                " duplicates will be shown (see DUPEFILTER_DEBUG to show all duplicates)"
            ),
        ) in caplog.record_tuples

        dupefilter.close("finished")

    @pytest.mark.parametrize("df", [None, FromCrawlerRFPDupeFilter])
    def test_log_debug(
        self, caplog: pytest.LogCaptureFixture, df: type[BaseDupeFilter] | None
    ) -> None:
        settings: dict[str, Any] = {
            "DUPEFILTER_DEBUG": True,
        }
        if df:
            settings["DUPEFILTER_CLASS"] = df
        crawler = get_crawler(SimpleSpider, settings_dict=settings)
        spider = SimpleSpider.from_crawler(crawler)
        dupefilter = _get_dupefilter(crawler=crawler)

        r1 = Request("http://scrapytest.org/index.html")
        r2 = Request(
            "http://scrapytest.org/index.html",
            headers={"Referer": "http://scrapytest.org/INDEX.html"},
        )

        with caplog.at_level(logging.DEBUG):
            dupefilter.log(r1, spider)
            dupefilter.log(r2, spider)

        assert crawler.stats.get_value("dupefilter/filtered") == 2
        assert (
            "scrapy.dupefilters",
            logging.DEBUG,
            "Filtered duplicate request: <GET http://scrapytest.org/index.html> (referer: None)",
        ) in caplog.record_tuples
        assert (
            "scrapy.dupefilters",
            logging.DEBUG,
            (
                "Filtered duplicate request: <GET http://scrapytest.org/index.html>"
                " (referer: http://scrapytest.org/INDEX.html)"
            ),
        ) in caplog.record_tuples

        dupefilter.close("finished")

    def test_fingerprints_deprecation(self):
        dupefilter = cast("RFPDupeFilter", _get_dupefilter())
        request = Request("http://scrapytest.org/index.html")
        dupefilter.request_seen(request)
        with pytest.warns(
            ScrapyDeprecationWarning,
            match=r"RFPDupeFilter\.fingerprints is deprecated\.",
        ):
            fingerprints = dupefilter.fingerprints
        assert fingerprints == {dupefilter.request_fingerprint(request)}
        dupefilter.close("finished")

    def test_request_fingerprint_override_deprecation(self):
        class LegacyDupeFilter(RFPDupeFilter):
            def request_fingerprint(self, request):
                return hashlib.sha1(to_bytes(request.url.lower())).hexdigest()

        with pytest.warns(
            ScrapyDeprecationWarning,
            match=r"Overriding RFPDupeFilter\.request_fingerprint\(\) is deprecated",
        ):
            dupefilter = _get_dupefilter(
                settings={"DUPEFILTER_CLASS": LegacyDupeFilter}
            )

        assert not dupefilter.request_seen(Request("http://scrapytest.org/index.html"))
        assert dupefilter.request_seen(Request("http://scrapytest.org/INDEX.html"))
        dupefilter.close("finished")


class TestBaseDupeFilter:
    def test_log_deprecation(self):
        dupefilter = _get_dupefilter(
            settings={"DUPEFILTER_CLASS": BaseDupeFilter},
        )
        with pytest.warns(
            ScrapyDeprecationWarning,
            match=r"Calling BaseDupeFilter\.log\(\) is deprecated.",
        ):
            dupefilter.log(None, None)  # type: ignore[arg-type]
