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
    .. automethod:: explain_links

.. _topics-link-extractors-scope-report:

Auditing extraction with a link scope report
--------------------------------------------

:meth:`LxmlLinkExtractor.explain_links
<scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor.explain_links>` returns a
read-only :class:`~scrapy.linkextractors.lxmlhtml.LinkScopeReport` that
explains, for every link occurrence found in a response, whether it was
accepted or excluded and why (protocol, path, domain, text or dedup rules).

The report is computed entirely from the already-downloaded response: relative
links are resolved against the *final* response URL (``response.url``, i.e. the
URL after redirects, honoring a ``<base href>`` tag when present), extracted
links are never re-requested, and building a report never changes the result
of :meth:`extract_links
<scrapy.linkextractors.lxmlhtml.LxmlLinkExtractor.extract_links>` —
``report.links`` is identical to ``extract_links(response)``.

Each entry keeps its own source position, so two references to the same
address are both listed even when deduplication keeps only the first one.
The protocol (URL scheme) check is reported first, before any domain, path or
scheduling-related rule. Fragment and query-string handling follows the same
rules as regular extraction: the dedup key is the extracted URL as-is,
fragment included (or its canonicalized form with ``canonicalize=True``).

Example:

.. code-block:: python

    from scrapy.http import HtmlResponse
    from scrapy.linkextractors import LinkExtractor

    # A page reached after a redirect to http://cdn.example/final/index.html
    response = HtmlResponse(
        url="http://cdn.example/final/index.html",
        body=b"""
            <a href="next.html">next</a>
            <a href="http://other.example.org/out">external</a>
            <a href="javascript:goToPage(1)">js</a>
            <a href="next.html">next again</a>
        """,
    )

    lx = LinkExtractor(allow_domains=["cdn.example"])
    report = lx.explain_links(response)
    print(report.explain())

.. code-block:: none

    Link scope report for http://cdn.example/final/index.html (base URL: http://cdn.example/final/index.html)
    total: 4, accepted: 1, excluded: 3, summary: {'accepted': 1, 'domain': 1, 'protocol': 1, 'dedup': 1}
    [accepted] http://cdn.example/final/next.html from entry #0 (document 0, <a> @href) - kept: passes all extraction rules
    [excluded:domain] http://other.example.org/out from entry #1 (document 0, <a> @href) - domain 'other.example.org' is not in allow_domains ['cdn.example']
    [excluded:protocol] javascript:goToPage(1) from entry #2 (document 0, <a> @href) - scheme 'javascript' is not one of 'http', 'https', 'file', 'ftp'
    [excluded:dedup] http://cdn.example/final/next.html from entry #3 (document 0, <a> @href) - duplicate of entry #0: same dedup key 'http://cdn.example/final/next.html' ...

Note how ``next.html`` is resolved against the final URL
(``http://cdn.example/final/``), not the originally requested one, and how the
second reference to it (entry #3) is still reported with its own source.

The report classes:

.. autoclass:: scrapy.linkextractors.lxmlhtml.LinkScopeReport
    :members:

.. autoclass:: scrapy.linkextractors.lxmlhtml.LinkExplanation
    :members:

.. autoclass:: scrapy.linkextractors.lxmlhtml.LinkSource
    :members:

The tests in ``tests/test_linkextractors_scope_report.py`` contain runnable
examples based on :class:`~scrapy.http.HtmlResponse` fixtures. Run them with:

.. code-block:: console

    python -m pytest tests/test_linkextractors_scope_report.py -q

Link
----

.. autoclass:: scrapy.link.Link
