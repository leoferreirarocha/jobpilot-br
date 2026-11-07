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

    def test_mixed_junior_seniority_range_keeps_junior_priority(self):
        mixed=rank(job(title="Suporte técnico Junior/Pleno (Bilingue)"))
        general=rank(job(title="Assistente administrativo"))
        self.assertGreater(mixed.score,general.score)

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

    def test_salary_targets_require_clear_regime_and_monthly_expectation(self):
        config={"salary_expectations":{"clt":2500,"pj":3500}}
        self.assertEqual(answer_for("Qual sua pretensão salarial CLT?",config),"2500")
        self.assertEqual(answer_for("Qual sua expectativa salarial mensal PJ?",config),"3500")
        self.assertEqual(answer_for("Expected salary for CLT",config),"2500")
        for question in ["Qual sua pretensão salarial?","Qual seu último salário CLT?","Pretensão salarial anual PJ","Pretensão salarial por hora PJ","Pretensão salarial CLT ou PJ"]:
            self.assertIsNone(answer_for(question,config))
        config["answers"]={"Qual sua pretensão salarial CLT?":"2800"}
        self.assertEqual(answer_for("Qual sua pretensão salarial CLT?",config),"2800")

    def test_explicit_unspecified_salary_target(self):
        config={"salary_expectations":{"clt":2500,"pj":3500,"unspecified":3000}}
        for question in ['Qual sua pretensão salarial?', 'Qual é sua expectativa de remuneração mensal?', 'Expected salary', 'What is your expected compensation?', 'Pretensão salarial CLT ou PJ']:
            self.assertEqual(answer_for(question,config),'3000',question)
        self.assertEqual(answer_for('Qual sua pretensão salarial CLT?',config),'2500')
        self.assertEqual(answer_for('Qual sua pretensão salarial PJ?',config),'3500')
        for question in ['Qual seu último salário?', 'Pretensão salarial anual', 'Expected hourly salary', 'Pretensão salarial semanal', 'Pretensão salarial líquida', 'Pretensão salarial CLT e PJ separadamente']:
            self.assertIsNone(answer_for(question,config),question)

    def test_current_unemployment_overrides_no_past_employer_assumptions(self):
        config={'employment':{'currently_employed':False}}
        bilingual='Confirm the name of the company where you work/ Confirme o nome da empresa em que você trabalha atualmente'
        self.assertEqual(answer_for(bilingual,config),'Não trabalho atualmente')
        self.assertEqual(answer_for('Current company',config),'Not currently employed')
        self.assertEqual(answer_for('Are you currently employed?',config),'No')
        self.assertEqual(answer_for('Você trabalha atualmente?',config),'Não')
        for label in ['Empresa anterior', 'Previous company', 'Where did you last work?', 'Qual seu último salário?', 'Empresa']:
            self.assertIsNone(answer_for(label,config),label)
        self.assertIsNone(answer_for('Current company',{}))

    def test_numeric_salary_format_does_not_mean_benefits_included(self):
        config={'salary_expectations':{'clt':2500,'pj':3500,'unspecified':3000}}
        question='What is your salary expectation? Only the numeric value (no currency, text, or benefits)/ Qual é a sua pretensão salarial? Apenas o valor numérico (sem moeda, texto ou benefícios).'
        self.assertEqual(answer_for(question,config),'3000')
        self.assertIsNone(answer_for(question+' Annual salary',config))
        self.assertIsNone(answer_for('Expected salary including benefits',config))
        self.assertIsNone(answer_for('Pretensão salarial incluindo benefícios',config))

    def test_no_relatives_requires_explicit_general_declaration(self):
        config={'employment':{'relatives_or_close_contacts_at_employer':False}}
        question='Do you have any relatives (such as parents, siblings), close friends, or acquaintances who currently work at Example?*'
        self.assertEqual(answer_for(question,config),'No')
        self.assertEqual(answer_for('Você possui algum parente que trabalha nesta empresa?',config),'Não')
        self.assertIsNone(answer_for(question,{}))
        for label in ['Name any relatives who work at Example', 'Do you have any relatives who do not work at Example?', 'Você possui algum parente que não trabalha nesta empresa?', 'Do you require accessibility?', 'Are you eligible to work at Example?']:
            self.assertIsNone(answer_for(label,config),label)

    def test_generic_salary_respects_known_pj_floor(self):
        config={'salary_expectations':{'clt':2500,'pj':3500,'unspecified':3000},'minimum_monthly_salary':{'clt':2500,'pj':3500}}
        self.assertEqual(answer_for('Qual sua pretensão salarial?',config),'3000')
        self.assertEqual(answer_for('Qual sua pretensão salarial?',config,contract_regime='clt'),'3000')
        self.assertEqual(answer_for('Qual sua pretensão salarial?',config,contract_regime='pj'),'3500')

    def test_confirmed_weekday_availability(self):
        config={'availability':{'hours_per_day':8,'weekdays':['monday','tuesday','wednesday','thursday','friday'],'work_modes':['onsite','hybrid','remote'],'onsite_city':'Curitiba'}}
        self.assertEqual(answer_for('How many hours per day are you available to work?',config),'8')
        self.assertEqual(answer_for('How many hours per week are you available to work?',config),'40')
        self.assertEqual(answer_for('Qual sua disponibilidade de horário?',config),'8 horas por dia, de segunda a sexta-feira')
        self.assertEqual(answer_for('Are you available to work full-time?',config),'Yes')
        self.assertEqual(answer_for('Você possui disponibilidade para atuar no modelo presencial em Curitiba/PR?',config),'Sim')
        self.assertEqual(answer_for('Você possui disponibilidade para trabalhar remotamente?',config),'Sim')
        self.assertEqual(answer_for('Pode trabalhar aos sábados?',config),'Não')
        self.assertEqual(answer_for('Aceita escala 12x36?',config),'Não')
        self.assertIsNone(answer_for('Pode trabalhar aos sábados?',{}))
        for label in ['Tem disponibilidade para início imediato?', 'Você pode trabalhar das 23h às 8h?', 'Você possui disponibilidade para atuar em São Paulo?']:
            self.assertIsNone(answer_for(label,config),label)


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
