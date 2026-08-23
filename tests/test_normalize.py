import unittest
from app.domain import normalize as nz
from app.domain import dedupe

class TestNames(unittest.TestCase):
    def test_legal_forms_and_scripts_collapse(self):
        keys = {nz.normalize_company_name(x) for x in ['ОсОО Альфа', 'ООО "Альфа"', 'Альфа', 'Альфа KG', 'alfa']}
        self.assertEqual(len(keys), 1, keys)

    def test_distinct_companies_stay_distinct(self):
        self.assertLess(nz.name_similarity('ОсОО Альфа', 'ОсОО Бета'), 0.7)

    def test_token_order_is_significant(self):
        # REGRESSION (audit P1-7). This test previously asserted the opposite.
        # Sorting name tokens made every anagram collide, so Vostok Stroy and
        # Stroy Vostok shared one identity key and merged two real companies.
        # The test encoded the bug, so the test was wrong, not the product.
        a, b = 'Восток Строй', 'Строй Восток'
        self.assertNotEqual(nz.normalize_company_name(a), nz.normalize_company_name(b))
        self.assertTrue(nz.is_token_permutation(a, b))
        self.assertLess(nz.name_similarity(a, b), dedupe.MERGE_THRESHOLD)
        self.assertEqual(nz.sorted_name_key(a), nz.sorted_name_key(b))

    def test_only_legal_form_does_not_wipe_name(self):
        self.assertNotEqual(nz.normalize_company_name('ОсОО'), '')

    def test_edge_inputs(self):
        for bad in ['', '   ', None, '\u200b\u200b', '!!!', 'a' * 500]:
            nz.normalize_company_name(bad or '')

class TestPhones(unittest.TestCase):
    def test_kg_formats(self):
        for raw in ['+996555112233', '996 555 11 22 33', '0555 11-22-33', '0555112233', '555112233', '(0555) 112233', '00996555112233']:
            self.assertEqual(nz.normalize_phone(raw)[0], '+996555112233', raw)

    def test_landline(self):
        self.assertEqual(nz.normalize_phone('0312 900 900')[0], '+996312900900')

    def test_invalid(self):
        for raw in ['', None, 'abc', '12', '0000', '05551122', '+996'] :
            self.assertIsNone(nz.normalize_phone(raw)[0], raw)

    def test_multi_phone_takes_first(self):
        self.assertEqual(nz.normalize_phone('0555112233, 0700445566')[0], '+996555112233')

    def test_mobile_detection(self):
        self.assertTrue(nz.is_mobile_kg('+996555112233'))
        self.assertFalse(nz.is_mobile_kg('+996312900900'))

class TestUrls(unittest.TestCase):
    def test_domain_normalisation(self):
        for raw in ['example.kg', 'http://www.example.kg', 'https://EXAMPLE.kg/', 'www.example.kg/page?utm_source=x']:
            self.assertEqual(nz.normalize_domain(raw), 'example.kg', raw)

    def test_registrable_domain(self):
        self.assertEqual(nz.registrable_domain('https://shop.alfa.com.kg/x'), 'alfa.com.kg')
        self.assertEqual(nz.registrable_domain('https://blog.alfa.kg'), 'alfa.kg')

    def test_tracking_params_stripped(self):
        self.assertEqual(nz.normalize_url('http://a.kg/p?utm_source=fb&id=3'), 'http://a.kg/p?id=3')

    def test_bad_urls(self):
        for raw in ['', None, 'javascript:alert(1)', 'ftp://a.kg', 'not a url', 'http://']:
            self.assertIsNone(nz.normalize_url(raw), raw)

    def test_idn(self):
        self.assertEqual(nz.normalize_domain('http://пример.рф'), 'xn--e1afmkfd.xn--p1ai')

class TestSocial(unittest.TestCase):
    def test_extract(self):
        html = '<a href="https://instagram.com/alfa.kg/">ig</a> <a href="https://wa.me/996555112233">wa</a> <a href="https://t.me/alfakg">tg</a>'
        got = nz.extract_social(html)
        self.assertEqual(got['instagram'], 'alfa.kg')
        self.assertEqual(got['whatsapp'], '+996555112233')
        self.assertEqual(got['telegram'], 'alfakg')

    def test_ignores_share_links(self):
        self.assertNotIn('facebook', nz.extract_social('<a href="https://facebook.com/sharer?u=x">share</a>'))

class TestCities(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(nz.normalize_city('bishkek')[0], 'Бишкек')
        self.assertEqual(nz.normalize_city('ОШ')[0], 'Ош')
        self.assertEqual(nz.normalize_city('г. Бишкек')[0], 'Бишкек')
        self.assertEqual(nz.normalize_city('Бишкек')[1], 'Чуйская')

class TestDedupe(unittest.TestCase):
    def test_key_priority(self):
        self.assertEqual(dedupe.dedupe_key({'website': 'https://www.alfa.kg', 'phone': '0555112233'}), 'd:alfa.kg')
        self.assertEqual(dedupe.dedupe_key({'phone': '0555112233'}), 'p:+996555112233')
        self.assertTrue(dedupe.dedupe_key({'company_name': 'Альфа', 'city': 'Ош'}).startswith('n:'))

    def test_same_domain_merges(self):
        r = dedupe.score_pair({'company_name': 'Альфа', 'website': 'http://alfa.kg'}, {'id': 'l1', 'company_name': 'ALFA KG', 'website': 'https://www.alfa.kg/'})
        self.assertTrue(r.should_merge, r)

    def test_different_companies_do_not_merge(self):
        r = dedupe.score_pair({'company_name': 'Кафе Альфа', 'city': 'Бишкек'}, {'id': 'l1', 'company_name': 'Кафе Бета', 'city': 'Бишкек'})
        self.assertFalse(r.should_merge, r)

    def test_shared_phone_different_name_is_review_not_merge(self):
        r = dedupe.score_pair({'company_name': 'СТО Турбо', 'phone': '0312900900'}, {'id': 'l1', 'company_name': 'Клиника Здоровье', 'phone': '0312900900'})
        self.assertFalse(r.should_merge)
        self.assertTrue(r.needs_review, r)

    def test_same_name_different_city_held_back(self):
        r = dedupe.score_pair({'company_name': 'ОсОО Альфа', 'city': 'Ош'}, {'id': 'l1', 'company_name': 'ОсОО Альфа', 'city': 'Бишкек'})
        self.assertFalse(r.should_merge, r)

    def test_merge_fields_fills_gaps_only(self):
        patch, changes = dedupe.merge_fields({'phone': '+996555112233', 'city': None, 'branches_estimate': 1}, {'phone': '+996700000000', 'city': 'Ош', 'branches_estimate': 3})
        self.assertEqual(patch['city'], 'Ош')
        self.assertEqual(patch['branches_estimate'], 3)
        self.assertNotIn('phone', patch)

if __name__ == '__main__':
    unittest.main()
