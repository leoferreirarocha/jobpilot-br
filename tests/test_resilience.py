"""Regression coverage for the scheduled run that failed on November 5."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import TimeoutError as BrowserTimeout, sync_playwright

import jobpilot
from core import Job, Ledger


MODERN = '''<!doctype html><meta charset="utf-8">
<title>Assistente de manutenção | Oficina Exemplo | LinkedIn</title>
<section><div><p>Oficina Exemplo</p><p>Assistente de manutenção</p>
<p>Curitiba, Paraná, Brasil · há 1 dia · 2 candidaturas</p></div>
<a href="#">Presencial</a><button aria-label="Usar a candidatura simplificada para esta vaga">Candidatura simplificada</button></section>
<section><div><h2>Sobre a vaga</h2></div><div><span data-testid="expandable-text-box"><p>Auxiliar na manutenção de equipamentos.</p></span></div></section>
<section><a href="https://www.linkedin.com/company/unrelated/">Empresa recomendada</a></section>'''

LEGACY_FORM = '''<!doctype html><meta charset="utf-8"><h1>Desenvolvedor Junior</h1>
<div class="jobs-unified-top-card__company-name">Empresa</div>
<div class="jobs-unified-top-card__bullet">Curitiba, Paraná, Brasil</div>
<div class="jobs-unified-top-card__workplace-type">Híbrido</div>
<div id="job-details">Desenvolvimento em C#.</div>
<button onclick="document.querySelector('[role=dialog]').hidden=false">Candidatura simplificada</button>
<div role="dialog" hidden><form onsubmit="return false">
<label>Email address<input type="email"></label><label>Currículo<input type="file" accept="application/pdf"></label>
<button onclick="window.sends=(window.sends||0)+1;this.parentElement.innerHTML='<h2>Candidatura enviada</h2>'">Enviar candidatura</button>
</form></div>'''


def summary():
    return {"at": Ledger.now(), "mode": "run", "status": "running", "candidates": 0, "submitted": 0,
            "scanned": 0, "excluded": 0, "skipped_registered": 0, "skipped_unavailable": 0,
            "read_issues": [], "search_issues": [], "pending": [], "results": []}


class ResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(jobpilot, "ROOT", self.root)
        self.root_patch.start()
        self.context = self.browser.new_context()
        self.context.route("**/*", lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=MODERN))
        self.page = self.context.new_page()
        self.ledger = Ledger(self.root / "applications.sqlite3")
        self.resume = self.root / "resume.pdf"
        self.resume.write_bytes(b"%PDF-1.4\n%fixture")
        self.config = {"contact": {"email": "candidate@example.com"}, "resumes": {"en": str(self.resume), "pt": str(self.resume)}, "seconds_between_applications": 0}

    def tearDown(self):
        self.context.close()
        self.ledger.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_company_without_link_and_nested_description(self):
        job = jobpilot.read_job(self.page, "123")
        self.assertEqual(job.title, "Assistente de manutenção")
        self.assertEqual(job.company, "Oficina Exemplo")
        self.assertEqual(job.location, "Curitiba, Paraná, Brasil")
        self.assertEqual(job.workplace, "Presencial")
        self.assertEqual(job.description, "Auxiliar na manutenção de equipamentos.")

    def test_legacy_title_still_uses_modern_description_fallback(self):
        html = MODERN.replace('<p>Assistente de manutenção</p>', '<h1>Assistente de manutenção</h1>')
        self.page.route("**/*", lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=html))
        self.assertIn("equipamentos", jobpilot.read_job(self.page, "123").description)

    def test_waits_for_description_without_requiring_apply_button(self):
        html = MODERN.replace('<button aria-label="Usar a candidatura simplificada para esta vaga">Candidatura simplificada</button>', '<p>Candidatura enviada</p>')
        self.page.route("**/*", lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=html))
        job = jobpilot.read_job(self.page, "123")
        self.assertEqual(job.title, "Assistente de manutenção")

    def test_removed_job_is_skipped_without_retry(self):
        requests = []
        def removed(route):
            requests.append(route.request.url)
            route.fulfill(status=404, content_type="text/html", body="<h1>Page not found</h1>")
        self.page.route("**/*", removed)
        with self.assertRaises(jobpilot.Unavailable):
            jobpilot.read_job(self.page, "123")
        self.assertEqual(len(requests), 1)

    def test_transient_server_error_is_retried_once(self):
        requests = []
        def transient(route):
            requests.append(route.request.url)
            route.fulfill(status=503 if len(requests) == 1 else 200, content_type="text/html; charset=utf-8", body=MODERN)
        self.page.route("**/*", transient)
        self.assertEqual(jobpilot.read_job(self.page, "123").company, "Oficina Exemplo")
        self.assertEqual(len(requests), 2)

    def test_429_is_global_stop_without_retry(self):
        requests = []
        def limited(route):
            requests.append(route.request.url)
            route.fulfill(status=429, content_type="text/html", body="Rate limited")
        self.page.route("**/*", limited)
        with self.assertRaises(jobpilot.StopRun):
            jobpilot.read_job(self.page, "123")
        self.assertEqual(len(requests), 1)

    def test_fast_learning_in_job_description_is_not_a_platform_limit(self):
        self.page.set_content('<p>Buscamos alguém que aprenda muito rápido para esta oportunidade.</p>')
        jobpilot.guard(self.page)

    def test_rate_notice_inside_dialog_is_a_global_stop(self):
        self.page.set_content('<div role=dialog>Você está enviando candidaturas muito rápido. Tente novamente amanhã.</div>')
        with self.assertRaises(jobpilot.StopRun):
            jobpilot.guard(self.page)

    def test_bad_job_does_not_discard_next_valid_job(self):
        real_reader = jobpilot.read_job
        def reader(page, job_id, deadline):
            if job_id == "999":
                raise BrowserTimeout("Injected read timeout")
            return real_reader(page, job_id, deadline)
        report = summary()
        with patch.object(jobpilot, "read_job", side_effect=reader):
            jobs = jobpilot.scan_jobs(self.page, ["999", "123"], self.ledger, report, time.monotonic()+20)
        self.assertEqual([job.id for job in jobs], ["123"])
        self.assertEqual(report["candidates"], 1)
        self.assertEqual(len(report["read_issues"]), 1)
        self.assertEqual(json.loads((self.root/"data/last-run.json").read_text())["candidates"], 1)

    def test_progress_survives_later_security_stop(self):
        valid = Job("123", "Dev", "Empresa", "Curitiba, Brasil", "Híbrido", "C#")
        report = summary()
        with patch.object(jobpilot, "read_job", side_effect=[valid, jobpilot.StopRun("Security checkpoint")]):
            with self.assertRaises(jobpilot.StopRun):
                jobpilot.scan_jobs(self.page, ["123", "456"], self.ledger, report, time.monotonic()+20)
        self.assertEqual(json.loads((self.root/"data/last-run.json").read_text())["candidates"], 1)

    def test_form_timeout_does_not_prevent_next_application(self):
        self.context.route("**/*", lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=LEGACY_FORM))
        real_apply = jobpilot.apply
        def applicant(page, job, config, ledger):
            if job.id == "999":
                raise BrowserTimeout("Injected form timeout")
            return real_apply(page, job, config, ledger)
        jobs = [Job(i, "Dev", "Empresa", "Curitiba, Brasil", "Híbrido", "C#") for i in ["999", "123"]]
        report = summary()
        with patch.object(jobpilot, "apply", side_effect=applicant):
            jobpilot.send_applications(self.context, jobs, self.config, self.ledger, report, time.monotonic()+20)
        self.assertEqual(report["submitted"], 1)
        self.assertEqual(self.ledger.status("999"), "needs_review")
        self.assertEqual(self.ledger.status("123"), "submitted")

    def test_error_after_reservation_stops_without_retrying_next(self):
        valid = Job("123", "Dev", "Empresa", "Curitiba, Brasil", "Híbrido", "C#")
        def uncertain(page, job, config, ledger):
            ledger.reserve(job, None)
            raise BrowserTimeout("Ambiguous result after reservation")
        with patch.object(jobpilot, "read_job", return_value=valid), patch.object(jobpilot, "apply", side_effect=uncertain):
            with self.assertRaises(jobpilot.StopRun):
                jobpilot.send_applications(self.context, [valid], self.config, self.ledger, summary(), time.monotonic()+20)
        self.assertEqual(self.ledger.status("123"), "uncertain")
        self.assertTrue(self.ledger.protected("123"))

    def test_styled_radio_question_is_not_silently_skipped(self):
        self.page.set_content('<div role=dialog><input type=radio id=answer style="display:none"><label for=answer>Possui experiência fiscal?</label></div>')
        pending = jobpilot.fill_step(self.page.get_by_role("dialog"), self.config, self.resume)
        self.assertEqual(pending, ["Possui experiência fiscal?"])

    def test_failed_search_does_not_discard_other_searches(self):
        html='<a href="/mynetwork/">Networking</a><div componentkey="SearchResultsMainContent"><div role="button" componentkey="job-card-component-ref-123">Vaga</div></div>'
        self.page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html))
        real_goto=self.page.goto
        calls=[]
        def goto(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 1:
                raise BrowserTimeout("Injected search timeout")
            return real_goto(*args, **kwargs)
        issues=[]
        with patch.object(self.page, "goto", side_effect=goto):
            ids=jobpilot.collect(self.page, {"queries":["C#"], "max_candidates":1}, time.monotonic()+20, issues)
        self.assertEqual(ids, ["123"])
        self.assertEqual(len(issues), 1)

    def test_rate_limited_search_does_not_try_other_searches(self):
        requests=[]
        def limited(route):
            requests.append(route.request.url)
            route.fulfill(status=429, content_type="text/html", body="Rate limited")
        self.page.route("**/*", limited)
        with self.assertRaisesRegex(jobpilot.StopRun, "Limite ou pausa"):
            jobpilot.collect(self.page, {"queries":["C#", "suporte"]}, time.monotonic()+20)
        self.assertEqual(len(requests), 1)

    def test_async_next_step_is_audited_before_another_click(self):
        html = LEGACY_FORM.replace('<label>Email address<input type="email"></label><label>Currículo<input type="file" accept="application/pdf"></label>', '''<div id=step1><label>Email address<input type=email></label>
        <button type=button aria-label="Avançar para próxima etapa" onclick="window.nextClicks=(window.nextClicks||0)+1;setTimeout(()=>{document.getElementById('step1').remove();document.getElementById('step2').hidden=false},350)">Avançar</button></div>
        <div id=step2 hidden><label>Currículo<input type=file accept=application/pdf></label></div>''')
        html = html.replace('<button onclick="window.sends=', '<button id=send hidden onclick="window.sends=')
        html = html.replace("document.getElementById('step2').hidden=false", "document.getElementById('step2').hidden=false;document.getElementById('send').hidden=false")
        self.page.route("**/*", lambda r:r.fulfill(content_type="text/html; charset=utf-8",body=html))
        job=jobpilot.read_job(self.page,"123")
        status,pending=jobpilot.apply(self.page,job,self.config,self.ledger)
        self.assertEqual((status,pending),("submitted",[]))
        self.assertEqual(self.page.evaluate("window.nextClicks"),1)

    def test_initial_dialog_waits_for_async_form(self):
        html=LEGACY_FORM.replace("document.querySelector('[role=dialog]').hidden=false", "document.querySelector('[role=dialog]').hidden=false;setTimeout(()=>document.querySelector('form').hidden=false,350)")
        html=html.replace('<form onsubmit=', '<form hidden onsubmit=')
        self.page.route("**/*",lambda r:r.fulfill(content_type="text/html; charset=utf-8",body=html))
        job=jobpilot.read_job(self.page,"123")
        status,pending=jobpilot.apply(self.page,job,self.config,self.ledger)
        self.assertEqual((status,pending),("submitted",[]))

    def test_empty_optional_question_stays_blank_without_blocking(self):
        html=LEGACY_FORM.replace('<label>Email address', '<label>Referrer name (optional)<input type=text></label><label>Email address')
        self.page.route("**/*",lambda r:r.fulfill(content_type="text/html; charset=utf-8",body=html))
        job=jobpilot.read_job(self.page,"123")
        self.assertEqual(jobpilot.apply(self.page,job,self.config,self.ledger),("submitted",[]))

    def test_unknown_required_question_stops_before_submission(self):
        html=LEGACY_FORM.replace('<label>Email address', '<label>Years of experience<input type=number required></label><label>Email address')
        self.page.route("**/*",lambda r:r.fulfill(content_type="text/html; charset=utf-8",body=html))
        job=jobpilot.read_job(self.page,"123")
        status,pending=jobpilot.apply(self.page,job,self.config,self.ledger)
        self.assertEqual(status,"needs_review")
        self.assertIn("Years of experience",pending)
        self.assertFalse(self.ledger.protected("123"))

    def test_affirmative_job_requires_review_before_opening_application(self):
        self.page.set_content(LEGACY_FORM)
        job=Job("123","Analista Junior - Vaga Afirmativa PCD","Empresa","Brasil","Remoto","Suporte")
        status,pending=jobpilot.apply(self.page,job,self.config,self.ledger)
        self.assertEqual(status,"needs_review")
        self.assertIn("elegibilidade",pending[0])
        self.assertFalse(self.page.get_by_role("dialog").is_visible())
        self.assertFalse(self.ledger.protected("123"))

    def test_lost_success_modal_is_confirmed_without_second_submission(self):
        requests=[]
        applied='''<title>Desenvolvedor Junior | Empresa | LinkedIn</title><section><div><h2>Status da candidatura</h2></div><p>Candidatura enviada</p></section><section><h2>Sobre a vaga</h2><p>Desenvolvimento</p></section>'''
        initial=LEGACY_FORM.replace("'<h2>Candidatura enviada</h2>'", "'<p>Carregando confirmação</p>'")
        def respond(route):
            requests.append(route.request.url)
            route.fulfill(content_type="text/html; charset=utf-8",body=initial if len(requests)==1 else applied)
        self.page.route("**/*",respond)
        job=jobpilot.read_job(self.page,"123")
        with patch.object(jobpilot,"CONFIRMATION_TIMEOUT_MS",200):
            status,pending=jobpilot.apply(self.page,job,self.config,self.ledger)
        self.assertEqual((status,pending),("submitted",[]))
        self.assertEqual(self.ledger.count_today(),1)
        self.assertEqual(len(requests),2)

    def test_status_words_in_description_are_not_confirmation(self):
        self.page.set_content('<section><h2>Sobre a vaga</h2><h2>Status da candidatura</h2><p>Candidatura enviada</p></section>')
        self.assertFalse(self.page.evaluate(jobpilot.STATUS_CONFIRMATION_JS))

    def test_field_label_is_resolved_inside_shadow_dom(self):
        self.page.set_content('<div id=host></div>')
        self.page.evaluate('''() => document.getElementById('host').attachShadow({mode:'open'}).innerHTML='<div role=dialog><span id=email-label>Email address</span><input aria-labelledby=email-label type=email></div>' ''')
        dialog=self.page.get_by_role("dialog")
        self.assertEqual(jobpilot.fill_step(dialog,self.config,self.resume),[])
        self.assertEqual(dialog.locator('input').input_value(),"candidate@example.com")


if __name__ == "__main__":
    unittest.main()
