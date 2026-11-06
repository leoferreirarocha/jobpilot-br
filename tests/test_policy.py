"""Regression cases from the real mismatched applications; no network or sends."""
import tempfile
import unittest
from pathlib import Path

from core import Job, Ledger
from policy import screen_job, salary_gate
from jobpilot import apply


def job(**kw):
    return Job(**(dict(id='123', title='Desenvolvedor C# Junior', company='Exemplo',
                      location='Curitiba, Brasil', workplace='Híbrido',
                      description='Desenvolvimento de software em C#.\nSalário CLT: R$ 2.500,00') | kw))


CONFIG = {'minimum_monthly_salary': {'clt': 2500, 'pj': 3500}, 'allow_undisclosed_salary': True}


class PolicyTests(unittest.TestCase):
    def test_retail_is_blocked_even_with_high_score(self):
        j = job(title='Auxiliar de loja', company='Varejista exemplo', score=1000,
                description='Atendimento, abastecimento e limpeza.\nSalário de R$ 2.050,00 + VT + VR (24,60)')
        self.assertEqual(screen_job(j, CONFIG).status, 'excluded')
        self.assertEqual(salary_gate(j, CONFIG).status, 'excluded')

    def test_missing_floors_cannot_be_bypassed_by_direct_apply(self):
        self.assertEqual(screen_job(job(), {'allow_undisclosed_salary': True}).status, 'review')

    def test_tech_company_does_not_make_billing_role_technology(self):
        self.assertEqual(screen_job(job(title='Assistente de Faturamento', company='Empresa de Software'), CONFIG).status, 'excluded')

    def test_retail_sales_and_industrial_quality_are_blocked(self):
        for title in ['Operador de caixa', 'Assistente comercial', 'Vendedor', 'TÉCNICO DE QUALIDADE (ELETROTÉCNICA) - PJ', 'Intake QA Coordinator', 'Auxiliar Financeiro II']:
            with self.subTest(title=title):
                self.assertEqual(screen_job(job(title=title), CONFIG).status, 'excluded')

    def test_salary_without_month_marker_still_enforces_floor(self):
        for salary in ['Salário R$ 2.050,00', 'Salário: R$ 2.499,99 + VT', 'Remuneração CLT: R$ 2.000,00 a R$ 4.000,00']:
            self.assertEqual(salary_gate(job(description=salary), CONFIG).status, 'excluded')

    def test_benefits_and_commission_cannot_lift_fixed_salary(self):
        self.assertEqual(salary_gate(job(description='Salário CLT R$ 2.050,00 + comissão R$ 5.000,00 + VR R$ 700,00'), CONFIG).status, 'excluded')

    def test_correct_clt_and_pj_floors(self):
        for regime, amount, expected in [('CLT', 2500, 'accepted'), ('PJ', 3499, 'excluded'), ('PJ', 3500, 'accepted')]:
            self.assertEqual(salary_gate(job(description=f'Salário {regime}: R$ {amount}'), CONFIG).status, expected)

    def test_unknown_regime_between_floors_requires_review(self):
        self.assertEqual(salary_gate(job(description='Salário R$ 3.000,00'), CONFIG).status, 'review')
        self.assertEqual(salary_gate(job(description='Salário R$ 4.000,00'), CONFIG).status, 'accepted')

    def test_undisclosed_salary_is_explicit_user_choice(self):
        j = job(description='Desenvolvimento de software em C#. Salário a combinar.\nVR: R$ 700,00')
        self.assertEqual(screen_job(j, CONFIG).status, 'accepted')
        self.assertEqual(screen_job(j, CONFIG | {'allow_undisclosed_salary': False}).status, 'review')

    def test_hourly_annual_foreign_and_variable_pay_need_review(self):
        for salary in ['Salário: R$ 50 por hora', 'Salário: R$ 60.000 por ano', 'Salário até R$ 5.000,00', 'Salary USD 60000 per year', 'Salário: 2050']:
            self.assertEqual(salary_gate(job(description=salary), CONFIG).status, 'review', salary)

    def test_junior_software_qa_and_it_support_can_match(self):
        for title, desc in [('Analista de QA Junior', 'Testes de software e registro de bugs.'), ('Suporte técnico TI Junior', 'Suporte a computadores e Windows.'), ('Game Developer', 'Unity C# e Git.')]:
            self.assertEqual(screen_job(job(title=title, description=desc), CONFIG).status, 'accepted')

    def test_unsupported_stack_requires_review_regardless_of_seniority(self):
        for title in ['Junior Node Developer', 'Analista Desenvolvedor .NET Pleno', 'Linux System Administrator – N2']:
            self.assertEqual(screen_job(job(title=title), CONFIG).status, 'review', title)

    def test_higher_seniority_and_experience_demands_do_not_block(self):
        for title in ['Desenvolvedor C# Senior', 'Game Developer Pleno', 'Lead Unity Developer']:
            self.assertEqual(screen_job(job(title=title, description='Desenvolvimento em C# e Unity.\nRequirements\n5+ years of experience in software development.'), CONFIG).status, 'accepted', title)

    def test_internship_and_apprenticeship_are_below_minimum(self):
        for title in ['Estágio Desenvolvedor C#', 'Software Developer Intern', 'Aprendiz de programação de software']:
            self.assertEqual(screen_job(job(title=title), CONFIG).status, 'excluded', title)

    def test_mandatory_skill_needs_review_but_desirable_does_not(self):
        base = 'Desenvolvimento em C#.\nRequisitos:\n'
        self.assertEqual(screen_job(job(description=base+'Experiência com SQL.'), CONFIG).status, 'review')
        self.assertEqual(screen_job(job(description=base+'C# e Git.\nDiferenciais:\nConhecimento em SQL.'), CONFIG).status, 'accepted')

    def test_direct_apply_cannot_bypass_role_or_salary_gate(self):
        # page=None proves that neither form opening nor upload nor reserve is reached.
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'test.sqlite3')
            try:
                for j in [job(title='Auxiliar de loja'), job(description='Desenvolvimento C#.\nSalário CLT R$ 2050')]:
                    status, reasons = apply(None, j, CONFIG, ledger)
                    self.assertEqual(status, 'outside_scope')
                    self.assertTrue(reasons)
                    self.assertFalse(ledger.protected(j.id))
                    self.assertEqual(ledger.count_today(), 0)
            finally:
                ledger.close()


if __name__ == '__main__':
    unittest.main()
