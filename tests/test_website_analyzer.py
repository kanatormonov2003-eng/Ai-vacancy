import unittest
from tests.base import AppTestCase

class TestAnalyzerAgainstRealHttp(AppTestCase):
    with_fixture_server = True

    def setUp(self):
        from app.analysis import circuit, http_client
        circuit.reset()
        http_client.BUDGET.reset()
        http_client.clear_robots_cache()
        from app.db import sqlite as db
        db.execute("DELETE FROM http_cache")

    def analyze(self, path, **kw):
        from app.analysis import website
        return website.analyze(self.web.base + path, cache_ttl_s=0, **kw)

    def test_modern_site_scores_well_and_detects_business_signals(self):
        a = self.analyze('/good/')
        self.assertTrue(a.reachable)
        self.assertEqual(a.http_status, 200)
        self.assertGreater(a.total_score, 55, a.scores)
        self.assertGreater(a.scores['mobile'], 55)
        d = a.detected
        self.assertTrue(d['viewport'])
        self.assertTrue(d['catalog'])
        self.assertTrue(d['prices'])
        self.assertTrue(d['whatsapp'])
        self.assertTrue(d['contact_form'])
        self.assertTrue(d['multi_language'])
        self.assertEqual(d['contacts']['phone'], '+996555112233')
        self.assertEqual(d['socials']['instagram'], 'cafe.alfa.kg')
        self.assertEqual(d['socials']['telegram'], 'cafealfa')
        self.assertEqual(d['contacts']['email'], 'hi@alfa.example')

    def test_outdated_site_scores_badly_with_named_weaknesses(self):
        a = self.analyze('/bad/')
        self.assertTrue(a.reachable)
        self.assertLess(a.scores['mobile'], 40, a.scores)
        self.assertLess(a.total_score, 45, a.scores)
        joined = ' '.join(a.detected['weaknesses'])
        self.assertIn('viewport', joined)

    def test_windows1251_encoding_is_decoded(self):
        a = self.analyze('/bad/')
        self.assertIn('СТО', a.detected['title'])

    def test_no_https_is_reported_as_fact(self):
        a = self.analyze('/good/')
        facts = {f.key: f.value for f in a.facts}
        self.assertEqual(facts['website_https'], 'false')
        self.assertEqual(facts['website_reachable'], 'true')

    def test_redirect_followed(self):
        a = self.analyze('/redirect/')
        self.assertTrue(a.reachable)
        self.assertEqual(a.redirects, 1)
        self.assertIn('/good', a.final_url)

    def test_500_is_unreachable_not_crash(self):
        a = self.analyze('/error500')
        self.assertFalse(a.reachable)
        self.assertEqual(a.total_score, 0)
        self.assertEqual(a.http_status, 500)

    def test_429_handled(self):
        a = self.analyze('/error429')
        self.assertFalse(a.reachable)
        self.assertEqual(a.http_status, 429)

    def test_timeout_is_structured_error(self):
        from app.analysis import website
        import os
        os.environ['HTTP_TIMEOUT_SECONDS'] = '1'
        from app import config
        config.reset_cache(); config.load(force=True)
        try:
            a = website.analyze(self.web.base + '/slow/', cache_ttl_s=0)
            self.assertFalse(a.reachable)
            self.assertEqual(a.error_code, 'timeout')
        finally:
            os.environ['HTTP_TIMEOUT_SECONDS'] = '3'
            config.reset_cache(); config.load(force=True)

    def test_malformed_html_does_not_raise(self):
        a = self.analyze('/malformed')
        self.assertTrue(a.reachable)
        self.assertIsInstance(a.total_score, int)

    def test_empty_body(self):
        a = self.analyze('/emptybody')
        self.assertTrue(a.reachable)
        self.assertEqual(a.detected['word_count'], 0)
        self.assertLess(a.total_score, 40)

    def test_huge_page_is_truncated_and_bounded(self):
        a = self.analyze('/huge')
        self.assertTrue(a.reachable)
        self.assertLessEqual(a.html_bytes, 2_100_000)

    def test_robots_disallow_is_respected(self):
        a = self.analyze('/blocked/')
        self.assertFalse(a.reachable)
        self.assertEqual(a.error_code, 'robots_disallowed')

    def test_dns_failure_is_structured(self):
        from app.analysis import website
        a = website.analyze('https://this-domain-does-not-exist-38271.kg', cache_ttl_s=0)
        self.assertFalse(a.reachable)
        self.assertIn(a.error_code, ('dns_not_found', 'dns_timeout', 'dns_error'))

    def test_invalid_url(self):
        from app.analysis import website
        for bad in ['', 'not a url', 'javascript:alert(1)', None]:
            a = website.analyze(bad, cache_ttl_s=0)
            self.assertEqual(a.error_code, 'invalid_url', bad)

    def test_ssrf_guard_blocks_localhost_when_enabled(self):
        import os
        from app import config
        from app.analysis import website
        os.environ['ALLOW_PRIVATE_HOSTS'] = '0'
        config.reset_cache(); config.load(force=True)
        try:
            a = website.analyze(self.web.base + '/good/', cache_ttl_s=0)
            self.assertEqual(a.error_code, 'blocked_private_address')
        finally:
            os.environ['ALLOW_PRIVATE_HOSTS'] = '1'
            config.reset_cache(); config.load(force=True)

    def test_retry_recovers_from_flaky_503(self):
        import os
        from app import config
        from app.analysis import http_client
        os.environ['HTTP_RETRIES'] = '3'
        config.reset_cache(); config.load(force=True)
        try:
            res = http_client.fetch(self.web.base + '/flaky', provider='flaky-test')
            self.assertEqual(res.status, 200)
        finally:
            os.environ['HTTP_RETRIES'] = '1'
            config.reset_cache(); config.load(force=True)

    def test_cache_prevents_second_network_call(self):
        from app.analysis import http_client
        first = http_client.fetch(self.web.base + '/good/', cache_ttl_s=60)
        second = http_client.fetch(self.web.base + '/good/', cache_ttl_s=60)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)

    def test_circuit_breaker_opens_after_repeated_failures(self):
        from app.analysis import circuit, http_client
        from app.errors import CircuitOpenError
        circuit.reset('cb-test')
        for _ in range(5):
            http_client.fetch(self.web.base + '/error500', provider='cb-test', retries=0)
        with self.assertRaises(CircuitOpenError):
            http_client.fetch(self.web.base + '/good/', provider='cb-test')

if __name__ == '__main__':
    unittest.main()
