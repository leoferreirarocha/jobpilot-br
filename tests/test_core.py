import tempfile
import unittest
from pathlib import Path

from core import Job, Ledger, answer_for, eligible, eligibility_review, monthly_salary, rank, resume_language, search_urls


def job(**kwargs):
    fields = dict(id="123", title="Desenvolvedor C# Junior", company="Exemplo", location="Curitiba, Paraná, Brasil", workplace="Híbrido", description="Unity, C#, Git")
    return Job(**(fields | kwargs))


class MatchingTests(unittest.TestCase):
    def test_geography(self):
        self.assertTrue(eligible(job()))
        self.assertTrue(eligible(job(location="São Paulo, Brasil", workplace="Remoto")))
        self.assertFalse(eligible(job(location="São Paulo, Brasil", workplace="Presencial")))
        self.assertFalse(eligible(job(location="Portugal", workplace="Remote")))
        self.assertFalse(eligible(job(location="Curitiba, PR", workplace="")))
        self.assertFalse(eligible(job(location="Curitibanos, Brasil", workplace="Presencial")))

    def test_salary_only_when_known_and_comparable(self):
        self.assertEqual(monthly_salary("R$ 3.500,00 - R$ 5.000,00 /mês"), 3500)
        self.assertEqual(monthly_salary("R$ 5 mil mensais"), 5000)
        self.assertIsNone(monthly_salary("R$ 50 /hora"))
        self.assertIsNone(monthly_salary("R$ 60.000 por ano"))
        self.assertIsNone(monthly_salary("A combinar"))

    def test_fit_precedes_salary_and_seniority(self):
        junior = rank(job())
        senior = rank(job(title="Senior C# Developer", salary_text="R$ 15.000 /mês"))
        self.assertGreater(junior.score, senior.score)

    def test_resume_language(self):
        self.assertEqual(resume_language(job(description="You will work with our team. The requirements include experience with C#. Your responsibilities include testing.")), "en")
        self.assertEqual(resume_language(job(description="Você trabalhará com nossa equipe para desenvolver jogos.")), "pt")

    def test_answers_never_guess(self):
        config = {"contact": {"email": "example@example.com", "phone": "41999999999"}, "answers": {"País": "Brasil"}}
        self.assertEqual(answer_for("Email address *", config), "example@example.com")
        self.assertEqual(answer_for("País", config), "Brasil")
        self.assertEqual(answer_for("Número de celular*", config), "41999999999")
        self.assertEqual(answer_for("Phone*", config), "41999999999")
        for question in ["Pretensão salarial", "Years of C# experience", "Are you authorized to work in Brazil?", "E-mail do responsável", "Senha"]:
            self.assertIsNone(answer_for(question, config))

    def test_urls_have_easy_apply_and_area_filters(self):
        urls = list(search_urls({"queries": ["C# júnior"]}))
        self.assertEqual(len(urls), 2)
        self.assertTrue(all("f_AL=true" in x for x in urls))
        self.assertIn("f_WT=2", urls[0])
        self.assertIn("Curitiba", urls[1])

    def test_explicit_eligibility_needs_review_without_inference(self):
        self.assertIsNone(eligibility_review(job(),{}))
        restricted=job(title="Desenvolvedor Junior - PCD")
        self.assertIsNotNone(eligibility_review(restricted,{}))
        self.assertIsNone(eligibility_review(restricted,{"reviewed_eligibility_job_ids":["123"]}))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite3")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_reserve_before_send_and_no_duplicate(self):
        self.assertTrue(self.ledger.reserve(job(), None))
        self.assertFalse(self.ledger.reserve(job(), None))
        self.assertEqual(self.ledger.count_today(), 1)
        self.ledger.record(job(), "submitted")
        self.assertEqual(self.ledger.count_today(), 1)
        self.assertTrue(self.ledger.protected("123"))

    def test_uncertain_is_not_retried(self):
        self.ledger.reserve(job(), None)
        self.ledger.record(job(), "uncertain")
        self.assertFalse(self.ledger.reserve(job(), None))

    def test_cap_persists_between_connections(self):
        self.ledger.reserve(job(), 1)
        other = Ledger(Path(self.temp.name) / "ledger.sqlite3")
        try:
            self.assertFalse(other.reserve(job(id="456"), 1))
        finally:
            other.close()

    def test_pause_stops_submission(self):
        self.ledger.pause("Daily limit")
        self.assertFalse(self.ledger.reserve(job(), None))


if __name__ == "__main__":
    unittest.main()
