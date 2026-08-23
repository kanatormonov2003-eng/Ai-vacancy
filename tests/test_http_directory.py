"""Regression tests for P0-4: provider query encoding and credential handling.

Before the fix a Cyrillic city raised UnicodeEncodeError on the socket write and
"Bishkek&admin=1" injected a query parameter into the upstream request.
"""
from __future__ import annotations
import os
from urllib.parse import parse_qs, urlsplit
from tests.base import AppTestCase
from app import config
from app.analysis import http_client as hc
from app.domain.query import SearchQuery
from app.errors import ProviderError
from app.sources.http_directory import HttpDirectoryProvider


class DirectoryUrlTest(AppTestCase):
    def _url(self, base, **kw):
        os.environ["DIRECTORY_BASE_URL"] = base
        config.load(force=True)
        try:
            return HttpDirectoryProvider()._url(SearchQuery(**kw), 2)
        finally:
            os.environ.pop("DIRECTORY_BASE_URL", None)
            config.load(force=True)

    def test_cyrillic_city_is_percent_encoded(self):
        url = self._url("http://api.example/companies", cities=["Бишкек"])
        url.encode("ascii")  # this is what used to raise UnicodeEncodeError
        self.assertEqual(parse_qs(urlsplit(url).query)["city"], ["Бишкек"])

    def test_injected_parameter_cannot_escape_the_value(self):
        url = self._url("http://api.example/companies", cities=["Bishkek&admin=1"])
        params = parse_qs(urlsplit(url).query)
        self.assertNotIn("admin", params)
        self.assertEqual(params["city"], ["Bishkek&admin=1"])
        self.assertIn("city=Bishkek%26admin%3D1", url)

    def test_base_url_params_are_preserved_but_cannot_override_ours(self):
        url = self._url("http://api.example/companies?key=abc&page=99", cities=["Osh"])
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["key"], ["abc"])
        self.assertEqual(params["page"], ["2"])

    def test_relative_base_url_is_rejected(self):
        with self.assertRaises(ProviderError):
            self._url("api.example/companies")


class CredentialHandlingTest(AppTestCase):
    def test_sensitive_headers_are_dropped(self):
        headers = {"Authorization": "Bearer secret", "Accept": "application/json"}
        self.assertEqual(hc._drop_sensitive(headers), {"Accept": "application/json"})

    def test_cross_origin_is_detected_by_host_and_port(self):
        self.assertNotEqual(hc._origin("http://127.0.0.1:8080/a"), hc._origin("http://localhost:8080/a"))
        self.assertNotEqual(hc._origin("http://a.example/x"), hc._origin("http://a.example:81/x"))
        self.assertEqual(hc._origin("https://A.Example/x"), hc._origin("https://a.example:443/y"))

    def test_cache_is_partitioned_by_credentials(self):
        anon = hc._credential_marker(None)
        one = hc._credential_marker({"Authorization": "Bearer one"})
        two = hc._credential_marker({"Authorization": "Bearer two"})
        self.assertEqual(anon, "")
        self.assertNotEqual(one, two)
        self.assertNotIn("one", one)  # the marker must not embed the secret
