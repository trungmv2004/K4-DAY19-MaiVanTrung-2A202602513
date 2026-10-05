"""Additional regression tests; the classroom tests and benchmark are unchanged."""

import json
import unittest

from src.graph import extract_report, find_substances, load_markdown_docs, parse_law_article
from src.legal_evidence import article_leads, mass_in_grams, named_mass_thresholds, penalty_band, without_related_teasers, explicit_responsibility_totals
from src.models import Document


class TestLegalEvidence(unittest.TestCase):
    def test_mass_conversion_preserves_lower_bound(self):
        self.assertEqual(mass_in_grams('hơn 9,6kg'), (9600, 'gt'))
        self.assertEqual(mass_in_grams('0,686g'), (0.686, 'eq'))
        self.assertEqual(mass_in_grams('khoảng 406g'), (406, 'approx'))

    def test_tablet_count_and_ranges_do_not_become_grams(self):
        self.assertEqual(mass_in_grams('5 viên'), (None, 'unknown'))
        self.assertEqual(mass_in_grams('từ 5g đến 30g'), (None, 'unknown'))
        self.assertEqual(mass_in_grams('5g và 30g'), (None, 'unknown'))

    def test_mdma_threshold_boundaries_from_actual_law(self):
        doc = next(d for d in load_markdown_docs('data/drug_law') if d.id == 'blhs-dieu-250')
        clauses = parse_law_article(doc)['clauses']
        rules = [(cl['number'], t) for cl in clauses
                 for t in named_mass_thresholds(cl, doc.id, find_substances) if 'MDMA' in t['substances']]
        def bands(grams):
            return [num for num, rule in rules if grams >= rule['min_g'] and
                    (rule['max_g'] is None or grams < rule['max_g'])]
        self.assertEqual(bands(29.999), [2])
        self.assertEqual(bands(30), [3])
        self.assertEqual(bands(99.999), [3])
        self.assertEqual(bands(100), [4])
        self.assertEqual(bands(9600), [4])

    def test_maximum_penalty_retains_life_imprisonment(self):
        doc = next(d for d in load_markdown_docs('data/drug_law') if d.id == 'blhs-dieu-255')
        penalties = [(cl['number'], penalty_band(cl, doc.id)) for cl in parse_law_article(doc)['clauses']]
        num, highest = max(((num, p) for num, p in penalties if p), key=lambda row: row[1]['severity'])
        self.assertEqual(num, 4)
        self.assertTrue(highest['life'])
        self.assertFalse(highest['death'])
        self.assertEqual(highest['max_years'], 20)

    def test_real_related_article_teaser_is_removed(self):
        docs = load_markdown_docs('data/drug_news')
        doc = next(d for d in docs if d.id == 'news-100260918080821054')
        text, removed = without_related_teasers(doc, article_leads(docs))
        self.assertEqual(removed, 1)
        self.assertNotIn('Cái Quang Huy', text)
        self.assertIn('Lê Minh Thành', text)

    def test_unrelated_final_paragraph_is_retained(self):
        doc = Document('one', '# Header\n\n' + 'Lead ' * 20 + '\n\nConclusion')
        text, removed = without_related_teasers(doc, article_leads([doc]))
        self.assertEqual(removed, 0)
        self.assertTrue(text.endswith('Conclusion'))

    def test_numeric_finding_needs_grounded_quote(self):
        doc = Document('one', 'Nguyễn Văn A vận chuyển 5 viên MDMA.')
        response = {'case': {'name': 'Vụ A', 'people': [], 'findings': [
            {'name': 'MDMA', 'amount': '9,6kg', 'evidence': 'Nguyễn Văn A vận chuyển 9,6kg MDMA.'}]}}
        case = extract_report(doc, doc.content, [], lambda *a, **kw: json.dumps(response))
        self.assertFalse(case['findings'][0]['quantity_verified'])
        self.assertIsNone(case['findings'][0]['mass_g'])

    def test_person_not_in_source_is_not_inserted(self):
        doc = Document('one', 'Nguyễn Văn A bị bắt.')
        response = {'case': {'name': 'Vụ A', 'people': [{'name':'Nguyễn Văn B'}], 'findings': []}}
        case = extract_report(doc, doc.content, [], lambda *a, **kw: json.dumps(response))
        self.assertEqual(case['people'], [])

    def test_explicit_responsibility_total_is_attributed_from_actual_source(self):
        doc = next(d for d in load_markdown_docs('data/drug_news') if d.id=='news-100260917203001265')
        totals = explicit_responsibility_totals(doc.content, [{'name':'Cái Quang Huy'}, {'name':'Nguyễn Tiến Đạt'}], ['MDMA','Ketamine'])
        mdma = next(row for row in totals if row['name']=='MDMA')
        self.assertEqual(mdma['subject'], 'Cái Quang Huy')
        self.assertEqual(mdma['mass_g'], 9600)
        self.assertEqual(mdma['qualifier'], 'gt')
        self.assertTrue(mdma['quantity_verified'])
        self.assertEqual(len(totals), 2)  # Next sentence's 4.3 kg belongs to another person.

    def test_ambiguous_short_name_does_not_assign_mass(self):
        text='Tổng khối lượng ma túy Huy phải chịu trách nhiệm hình sự là hơn 9,6kg MDMA.'
        totals=explicit_responsibility_totals(text,[{'name':'Nguyễn Văn Huy'},{'name':'Trần Văn Huy'}],['MDMA'])
        self.assertEqual(totals, [])
