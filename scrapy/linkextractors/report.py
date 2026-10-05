"""
Read-only scope reports for link extractors.

A scope report lists every link found in a response and states whether the
link extractor accepts it or excludes it, along with the reason and the
location of the link in the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from w3lib.url import canonicalize_url

from scrapy.utils.response import get_base_url

if TYPE_CHECKING:
    from scrapy.http import TextResponse
    from scrapy.link import Link
    from scrapy.linkextractors.lxmlhtml import LxmlLinkExtractor


@dataclass(frozen=True)
class LinkReportEntry:
    """A single link found in a response and the decision made about it.

    :param link: the extracted :class:`~scrapy.link.Link` object
    :param accepted: ``True`` if the link would be extracted, ``False`` if it
        is excluded
    :param reason: the reason for the decision. One of ``"accepted"``,
        ``"invalid_protocol"``, ``"not_allowed"``, ``"denied"``,
        ``"domain_not_allowed"``, ``"domain_denied"``,
        ``"denied_extension"``, ``"text_not_matched"`` or ``"duplicate"``
    :param source: an XPath identifying the element that held the link, so
        that two links pointing to the same address from different locations
        can be told apart
    """

    link: Link
    accepted: bool
    reason: str
    source: str


def extract_links_report(
    response: TextResponse,
    extractor: LxmlLinkExtractor,
) -> list[LinkReportEntry]:
    """Return a read-only scope report for *response*.

    Unlike :meth:`~scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor.extract_links`,
    which only returns the accepted links, the report contains one
    :class:`LinkReportEntry` for every link found in the response, stating
    whether it is accepted or excluded and why.

    The report is read-only: it does not modify *response* or *extractor*,
    and it never issues any request. Links are resolved against the final
    response URL (``response.url``), and fragment and query normalization
    follows the current link rules.
    """
    base_url = get_base_url(response)
    if extractor.restrict_xpaths:
        docs = [
            subdoc for x in extractor.restrict_xpaths for subdoc in response.xpath(x)
        ]
    else:
        docs = [response.selector]

    entries: list[LinkReportEntry] = []
    seen: set[str] = set()
    for doc in docs:
        for el, _, attr_val in extractor.link_extractor._iter_links(doc.root):
            link = extractor.link_extractor._build_link(
                el, attr_val, response.url, response.encoding, base_url
            )
            if link is None:
                # Bogus values are dropped during extraction, so they are not
                # real links and are not reported.
                continue
            source = el.getroottree().getpath(el)
            accepted, reason = extractor._link_allowed_with_reason(link)
            if not accepted:
                entries.append(LinkReportEntry(link, False, reason, source))
                continue
            if extractor.canonicalize:
                link.url = canonicalize_url(link.url)
            key = extractor.link_extractor.link_key(link)
            if key in seen:
                entries.append(LinkReportEntry(link, False, "duplicate", source))
                continue
            seen.add(key)
            entries.append(LinkReportEntry(link, True, "accepted", source))
    return entries
