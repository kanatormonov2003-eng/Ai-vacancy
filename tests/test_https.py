import unittest
from tests.base import AppTestCase


class HTTPSFixtureTest(AppTestCase):
    def test_trusted_tls_and_invalid_certificate_are_distinguishable(self):
        from app.analysis import http_client
        from tools.https_fixture import HTTPSFixture
        fixture = HTTPSFixture().start()
        try:
            trusted = http_client.fetch(fixture.url, provider="tls-trusted", check_robots=False,
                                        cache_ttl_s=0, ca_file=fixture.cert)
            self.assertEqual(trusted.status, 200)
            self.assertTrue(trusted.tls_ok)
            untrusted = http_client.fetch(fixture.url, provider="tls-untrusted", check_robots=False,
                                          cache_ttl_s=0)
            self.assertEqual(untrusted.status, 200)
            self.assertFalse(untrusted.tls_ok)
        finally:
            fixture.stop()


if __name__ == "__main__": unittest.main()
