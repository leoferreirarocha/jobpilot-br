"""Offline browser integration. No LinkedIn requests or real applications."""
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from core import Job, Ledger
from jobpilot import StopRun, apply, collect, guard, read_job
import time
from unittest.mock import patch


FORM = '''<!doctype html><html><body>
<h1>Desenvolvedor C# Junior</h1>
<button onclick="document.querySelector('[role=dialog]').hidden=false">Candidatura simplificada</button>
<div role="dialog" hidden><form onsubmit="return false">
<label>Email address<input type="email" value="wrong@example.com"></label>
<label>Mobile phone number<input type="tel"></label>
<label>Currículo<input type="file" accept="application/pdf"></label>
<button onclick="window.sends=(window.sends||0)+1;document.querySelector('[role=dialog]').innerHTML='<h2>Candidatura enviada</h2>'">Enviar candidatura</button>
</form></div></body></html>'''


class BrowserTests(unittest.TestCase):
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
        self.ledger = Ledger(Path(self.temp.name) / "db.sqlite3")
        self.page = self.browser.new_page()
        self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=FORM))
        self.page.goto("https://www.linkedin.com/jobs/view/123/")
        self.resume = Path(self.temp.name) / "resume.pdf"
        self.resume.write_bytes(b"%PDF-1.4\n%fixture only")
        self.config = {"contact": {"email": "correct@example.com", "phone": "41999999999"},
                       "resumes": {"pt": str(self.resume), "en": str(self.resume)}}
        self.job = Job("123", "Dev", "Empresa", "Curitiba, Brasil", "Híbrido", "Descrição")

    def tearDown(self):
        self.page.close()
        self.ledger.close()
        self.temp.cleanup()

    def test_known_form_upload_and_single_submission(self):
        status, pending = apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual((status, pending), ("submitted", []))
        self.assertEqual(self.page.evaluate("window.sends"), 1)
        self.assertTrue(self.ledger.protected("123"))

    def test_unknown_question_even_if_prefilled_stops_send(self):
        self.page.locator("form").evaluate("el => el.insertAdjacentHTML('afterbegin','<label>Pretensão salarial<input value=5000></label>')")
        status, pending = apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual(status, "needs_review")
        self.assertIn("Pretensão salarial", pending)
        self.assertEqual(self.page.evaluate("window.sends || 0"), 0)

    def test_quota_stops_before_click(self):
        self.ledger.reserve(Job("456", "Other", "X", "", "", ""), 1)
        self.config["max_per_day"] = 1
        with self.assertRaises(StopRun):
            apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual(self.page.evaluate("window.sends || 0"), 0)

    def test_platform_limit_stops(self):
        self.page.set_content("<h1>Você atingiu o limite diário de candidaturas</h1>")
        with self.assertRaises(StopRun):
            guard(self.page)

    def test_unapproved_consent_is_not_checked(self):
        self.page.locator("form").evaluate("el => el.insertAdjacentHTML('afterbegin','<label>Aceito os termos<input type=checkbox></label>')")
        status, pending = apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual(status, "needs_review")
        self.assertIn("Aceito os termos", pending)
        self.assertFalse(self.page.locator("input[type=checkbox]").is_checked())

    def test_multi_step_form_upload_and_contacts(self):
        self.page.set_content('''<button onclick="document.querySelector('[role=dialog]').hidden=false">Candidatura simplificada</button>
        <div role=dialog hidden><form onsubmit="return false">
        <div id=step1><label>Email address<input type=email></label>
        <button onclick="document.getElementById('step1').hidden=true;document.getElementById('step2').hidden=false">Avançar</button></div>
        <div id=step2 hidden><div role=alert>Upload do currículo concluído</div><label>Currículo<input type=file accept=application/pdf></label>
        <button onclick="window.sends=1;document.querySelector('[role=dialog]').innerHTML='<h2>Candidatura enviada</h2>'">Enviar candidatura</button></div>
        </form></div>''')
        status, pending = apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual((status, pending), ("submitted", []))

    def test_nested_select_has_question_label_only(self):
        self.page.locator("form").evaluate("el => el.insertAdjacentHTML('afterbegin','<label>Código do país<select><option value=br>Brasil (+55)</option></select></label>')")
        self.config["answers"] = {"Código do país": "Brasil (+55)"}
        status, pending = apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual((status, pending), ("submitted", []))

    def test_current_job_layout_uses_scoped_top_card(self):
        html = '''<div><div><a href="https://www.linkedin.com/company/example/">Example</a>
        <p>Example</p><p>Desenvolvedor C# Junior</p><p>Brasil · há 1 dia</p></div>
        <a href="#">Remoto</a><button data-view-name="job-apply-button">Candidatura simplificada</button></div>
        <div><div><h2>Sobre a vaga</h2></div><span data-testid="expandable-text-box">Desenvolvimento em Unity e C# com Git.</span></div>'''
        self.page.unroute("**/*")
        self.page.route("**/*", lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=html))
        found = read_job(self.page, "123")
        self.assertEqual(found.title, "Desenvolvedor C# Junior")
        self.assertEqual(found.company, "Example")
        self.assertEqual(found.workplace, "Remoto")
        self.assertIn("Unity", found.description)

    def test_current_search_cards_have_ids_without_links(self):
        html = '''<a href="/mynetwork/">Networking</a><div componentkey="SearchResultsMainContent">
        <div role="button" componentkey="job-card-component-ref-123">Vaga A</div>
        <div role="button" componentkey="job-card-component-ref-456">Vaga B</div></div>'''
        self.page.unroute("**/*")
        self.page.route("**/*", lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=html))
        ids = collect(self.page, {"queries": ["C#"], "max_candidates": 2}, time.monotonic()+20)
        self.assertEqual(ids, ["123", "456"])

    def test_modern_file_chooser_confirms_selected_pdf(self):
        self.page.set_content('''<button onclick="document.querySelector('[role=dialog]').hidden=false">Candidatura simplificada</button>
        <div role=dialog hidden><button onclick="document.getElementById('file').click()">Carregar currículo</button>
        <input id=file type=file style="display:none" onchange="addResume(this)">
        <button onclick="window.sends=1;document.querySelector('[role=dialog]').innerHTML='<h2>Candidatura enviada</h2>'">Enviar candidatura</button></div>
        <script>function addResume(input) {
          const name=input.files[0].name;
          const card=document.createElement('button');card.type='button';card.textContent='PDF '+name;
          const marker=document.createElement('div');marker.setAttribute('aria-label',name);
          const radio=document.createElement('input');radio.type='radio';radio.style.display='none';marker.append(radio);
          card.onclick=()=>radio.checked=true;card.append(marker);document.querySelector('[role=dialog]').append(card);input.remove();
        }</script>''')
        with patch("jobpilot.ROOT", Path(self.temp.name)):
            status, pending = apply(self.page, self.job, self.config, self.ledger)
        self.assertEqual((status, pending), ("submitted", []))

    def test_invisible_background_captcha_is_not_a_challenge(self):
        self.page.set_content('<iframe title="reCAPTCHA" src="about:blank" style="display:none"></iframe>')
        guard(self.page)
        self.page.locator("iframe").evaluate("el=>el.style.display='block'")
        with self.assertRaises(StopRun):
            guard(self.page)


if __name__ == "__main__":
    unittest.main()
