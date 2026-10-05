.. _topics-link-extractors:

===============
Link Extractors
===============

A link extractor is an object that extracts links from responses.

The ``__init__`` method of
:class:`~scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor` takes settings that
determine which links may be extracted. :class:`LxmlLinkExtractor.extract_links
<scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor.extract_links>` returns a
list of matching :class:`~scrapy.link.Link` objects from a
:class:`~scrapy.http.Response` object.

Link extractors are used in :class:`~scrapy.spiders.CrawlSpider` spiders
through a set of :class:`~scrapy.spiders.Rule` objects.

You can also use link extractors in regular spiders. For example, you can
instantiate :class:`LinkExtractor
<scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor>` as a class variable in your
spider and use it from your spider callbacks:

.. code-block:: python

    def parse(self, response):
        for link in self.link_extractor.extract_links(response):
            yield Request(link.url, callback=self.parse)

.. _topics-link-extractors-scope-report:

Link scope reports
==================

Sometimes you need to know not only which links a link extractor accepts, but
also why the rest are excluded. The
:func:`~scrapy.linkextractors.extract_links_report` function returns a
read-only *scope report*: one
:class:`~scrapy.linkextractors.LinkReportEntry` for every link found in the
response, stating whether it is accepted or excluded and the reason.

The report never issues any request and does not modify the response or the
extractor. Links are resolved against the final response URL
(``response.url``), so relative links on a page reached after a redirect are
resolved against the URL the response actually landed on, not the original
request URL. Fragment and query normalization follows the current link rules.

Each entry carries a ``source`` field with an XPath identifying the element
that held the link, so two links pointing to the same address from different
locations can still be told apart.

For example, given a response whose final URL is
``http://example.com/redirected/page`` (the original request was for
``http://example.com/old``):

.. code-block:: python

    from scrapy.linkextractors import LinkExtractor, extract_links_report

    extractor = LinkExtractor(allow_domains=["example.com"])
    report = extract_links_report(response, extractor)
    for entry in report:
        print(entry.accepted, entry.reason, entry.link.url, entry.source)

would print something like:

.. code-block:: none

    True accepted http://example.com/keep.html /html/body/a[1]
    False domain_not_allowed http://other.com/page /html/body/a[2]
    True accepted http://example.com/redirected/rel/link.html /html/body/a[3]
    False invalid_protocol javascript:alert(1) /html/body/a[4]
    False duplicate http://example.com/keep.html /html/body/a[5]
    False denied_extension http://example.com/photo.jpg /html/body/a[6]

The possible ``reason`` values are:

- ``accepted``: the link is extracted.
- ``invalid_protocol``: the link's protocol is not one of ``http``, ``https``,
  ``file`` or ``ftp``. This check runs before any other, so a ``javascript:``
  link is reported as ``invalid_protocol`` even if it would also fail a
  domain or path filter.
- ``not_allowed``: the link's URL does not match any ``allow`` regex.
- ``denied``: the link's URL matches a ``deny`` regex.
- ``domain_not_allowed``: the link's domain is not in ``allow_domains``.
- ``domain_denied``: the link's domain is in ``deny_domains``.
- ``denied_extension``: the link's URL has a denied extension (e.g. ``.jpg``).
- ``text_not_matched``: the link's text does not match ``restrict_text``.
- ``duplicate``: a link with the same address was already accepted from a
  different source location.

The accepted entries of the report are exactly what
:meth:`~scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor.extract_links`
returns, so the report is a drop-in debugging companion:

.. code-block:: python

    assert [e.link for e in report if e.accepted] == extractor.extract_links(response)

The report behavior is covered by tests using an :class:`~scrapy.http.HtmlResponse`
fixture that simulates a redirect (the response URL differs from the original
request URL). Run them with:

.. code-block:: console

    pytest tests/test_linkextractors.py::TestLinkReport -v

.. _topics-link-extractors-ref:

Link extractor reference
========================

The link extractor class is
:class:`scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor`. For convenience it
can also be imported as ``scrapy.linkextractors.LinkExtractor``:

.. code-block:: python

    from scrapy.linkextractors import LinkExtractor

LxmlLinkExtractor
-----------------

.. autoclass:: scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor

    .. automethod:: extract_links

Link scope report
-----------------

.. autofunction:: scrapy.linkextractors.extract_links_report

.. autoclass:: scrapy.linkextractors.LinkReportEntry

Link
----

.. autoclass:: scrapy.link.Link
