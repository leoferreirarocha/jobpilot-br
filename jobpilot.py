"""LinkedIn Easy Apply assistant. No stealth, CAPTCHA solving, or invented answers."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core import Job, Ledger, answer_for, eligible, norm, rank, resume_language, save_json, search_urls

ROOT = Path(__file__).resolve().parent
APPLY_SELECTOR = '[data-view-name="job-apply-button"], button[aria-label*="simplificada"], button[aria-label*="Easy Apply"]'


class StopRun(Exception):
    pass


class Review(Exception):
    pass


@contextmanager
def single_run(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StopRun("Outra execucao ja esta usando o perfil do navegador.")
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def load_config():
    path = ROOT / "config.local.json"
    if not path.exists():
        raise StopRun("Crie config.local.json a partir de config.example.json.")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    cap = config.get("max_per_day")
    if cap is not None and (not isinstance(cap, int) or cap < 1):
        raise StopRun("max_per_day deve ser null ou inteiro positivo.")
    if not config.get("queries") or config.get("max_candidates", 150) < 1 or config.get("max_minutes", 60) <= 0:
        raise StopRun("Revise os limites e as consultas da configuracao.")
    return config


def first_text(parent, selector):
    loc = parent.locator(selector)
    return loc.first.inner_text(timeout=4000).strip() if loc.count() else ""


def visible_button(parent, pattern):
    for button in parent.get_by_role("button", name=re.compile(pattern, re.I)).all():
        if button.is_visible():
            return button
    return None


def guard(page):
    url = page.url.lower()
    text = norm(page.locator("body").inner_text(timeout=10000))
    limits = ["reached today's easy apply limit", "reached your daily", "daily application limit",
              "daily easy apply limit", "atingiu o limite", "limite diario", "limite de candidaturas",
              "applying too quickly", "too many applications", "temporarily paused", "try again tomorrow",
              "tente novamente amanha", "muitas candidaturas", "muito rapido", "temporary pause on easy apply"]
    if any(value in text for value in limits):
        raise StopRun("Limite ou pausa do LinkedIn detectado; nenhuma nova tentativa hoje (UTC).")
    if any(value in url for value in ["/checkpoint/", "/challenge/", "/authwall"]):
        raise StopRun("Verificacao de seguranca do LinkedIn. Resolva pessoalmente no modo login.")
    if page.locator('iframe[src*="captcha"], iframe[title*="reCAPTCHA"], #captcha-internal').count():
        raise StopRun("CAPTCHA detectado. A rotina nao tenta resolver ou contornar a verificacao.")
    if re.search(r"/login|/uas/", url):
        raise StopRun("Sessao ausente ou expirada. Abra Entrar-LinkedIn.cmd.")


def authenticated(page):
    return bool(page.locator('nav.global-nav, a[href*="/mynetwork/"], a[href*="/messaging/"]').count())


def login(context):
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    print("Entre pessoalmente na janela do navegador. Prazo: 10 minutos. Nenhuma candidatura sera enviada.", flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if page.is_closed():
            raise StopRun("Janela fechada antes de confirmar o login.")
        if authenticated(page) and "/checkpoint/" not in page.url:
            save_json(ROOT / "data/login-status.json", {"ready": True, "at": Ledger.now()})
            print("LOGIN_OK: sessao local pronta.", flush=True)
            return
        page.wait_for_timeout(1000)
    raise StopRun("Tempo de login esgotado. Execute Entrar-LinkedIn.cmd para tentar novamente.")


def collect(page, config, deadline):
    ids = []
    for url in search_urls(config):
        if time.monotonic() > deadline:
            break
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        guard(page)
        if not authenticated(page):
            raise StopRun("Nao foi possivel confirmar uma sessao autenticada. Abra Entrar-LinkedIn.cmd.")
        # Wait for actual search cards or the no-results UI, rather than a guessed fixed delay.
        page.locator('[data-occludable-job-id], .jobs-search-results-list, .jobs-search-no-results-banner, [componentkey="SearchResultsMainContent"], [componentkey="searchResultsHeaderComponent"]').first.wait_for(timeout=15000)
        container = page.locator('.jobs-search-results-list, .scaffold-layout__list, [componentkey="SearchResultsMainContent"]').first
        for _ in range(4):
            for card in page.locator('[componentkey^="job-card-component-ref-"][role="button"]').all():
                match = re.search(r"job-card-component-ref-(\d+)", card.get_attribute("componentkey") or "")
                if match and match[1] not in ids:
                    ids.append(match[1])
            for link in page.locator('a[href*="/jobs/view/"]').all():
                match = re.search(r"/jobs/view/(\d+)", link.get_attribute("href") or "")
                if match and match[1] not in ids:
                    ids.append(match[1])
            if len(ids) >= config.get("max_candidates", 150):
                return ids[:config.get("max_candidates", 150)]
            if container.count():
                container.evaluate("""el => {
                    let parent=el;
                    while(parent && !(parent.scrollHeight > parent.clientHeight && /auto|scroll/.test(getComputedStyle(parent).overflowY))) parent=parent.parentElement;
                    if(parent) parent.scrollBy(0, parent.clientHeight);
                }""")
                page.wait_for_timeout(500)
    return ids


def read_job(page, job_id):
    page.goto(f"https://www.linkedin.com/jobs/view/{job_id}/", wait_until="domcontentloaded", timeout=45000)
    guard(page)
    page.locator('h1, ' + APPLY_SELECTOR).first.wait_for(timeout=15000)
    title = first_text(page, "h1")
    company = first_text(page, ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name")
    location = first_text(page, ".job-details-jobs-unified-top-card__tertiary-description-container, .jobs-unified-top-card__bullet")
    workplace = first_text(page, ".job-details-jobs-unified-top-card__job-insight, .jobs-unified-top-card__workplace-type")
    # LinkedIn sometimes renders workplace/salary as multiple insight buttons.
    insights = page.locator(".job-details-jobs-unified-top-card__job-insight, .job-details-fit-level-preferences button, .jobs-unified-top-card__workplace-type")
    if insights.count():
        workplace = " ".join(insights.all_inner_texts())
    more = visible_button(page, r"^(Show more|Exibir mais|Ver mais)$")
    if more:
        more.click()
    description = first_text(page, "#job-details, .jobs-description__content, .jobs-description-content__text")
    salary_source = workplace
    if not title and page.locator(APPLY_SELECTOR).count():
        # 2026 interface: titles are paragraphs; scope to the smallest top card,
        # avoiding job recommendations and text in the description.
        top = page.locator(APPLY_SELECTOR).first.evaluate("""button => {
            let el=button.parentElement;
            while(el && !el.querySelector('a[href*="/company/"]')) el=el.parentElement;
            if(!el) return null;
            const company=el.querySelector('a[href*="/company/"]').innerText.trim();
            const paragraphs=[...el.querySelectorAll('p')].map(p=>p.innerText.trim()).filter(Boolean);
            const title=paragraphs.find(text=>text!==company);
            const location=paragraphs.find(text=>text.includes('·')) || '';
            const modes=[...el.querySelectorAll('a,button')].map(n=>n.innerText.trim()).filter(text=>/^(Remoto|Remote|Híbrido|Hybrid|Presencial|On.?site)$/i.test(text));
            return {company,title,location,workplace:modes.join(' '),pay:el.innerText};
        }""")
        if top:
            title, company, location, workplace = (top.get(key, "") for key in ("title", "company", "location", "workplace"))
            salary_source = top.get("pay", workplace)
        about = page.get_by_role("heading", name=re.compile(r"^(Sobre a vaga|About the job)$", re.I))
        if about.count():
            description = first_text(about.first.locator("xpath=../.."), '[data-testid="expandable-text-box"]')
    if not title or not description:
        raise Review("Layout da vaga nao reconhecido; descricao ou titulo ausente.")
    salary = " ".join(line for line in (salary_source + "\n" + description).splitlines()
                      if "R$" in line and re.search(r"mes\b|mensal|mensais|month|/mo\b", norm(line)))
    return rank(Job(job_id, title, company, location, workplace, description, salary))


def label_of(field):
    return field.evaluate(r"""el => {
      const ids=(el.getAttribute('aria-labelledby')||'').split(/\s+/);
      const labels=el.labels ? [...el.labels].map(x=>{
        const copy=x.cloneNode(true);
        copy.querySelectorAll('input,select,textarea,button').forEach(child=>child.remove());
        return copy.textContent;
      }).join(' ').trim() : '';
      return labels || el.getAttribute('aria-label') || ids.map(id=>document.getElementById(id)?.innerText||'').join(' ').trim() || '';
    }""")


def fill_step(dialog, config, resume: Path):
    pending = []
    for field in dialog.locator("input, textarea, select").all():
        kind = (field.get_attribute("type") or "").lower()
        if kind in {"hidden", "submit", "button"} or field.is_disabled():
            continue
        if kind == "file":
            # Attach only a resume, never other documents requested by the employer.
            label = norm(label_of(field))
            accept = (field.get_attribute("accept") or "").lower()
            nearby = norm(field.evaluate("el => el.closest('.jobs-document-upload, .js-jobs-document-upload__container')?.innerText || ''"))
            if not re.search(r"resume|curriculo|cv\b", label + " " + nearby):
                pending.append(label or "Upload de documento sem identificacao")
                continue
            if accept and not any(ext in accept for ext in ["pdf", "*"]):
                pending.append("Upload nao aceita PDF")
                continue
            field.set_input_files(str(resume))
            continue
        if not field.is_visible():
            continue
        label = label_of(field).strip()
        if kind in {"radio", "checkbox"}:
            if norm(label).startswith(("follow ", "seguir ")):
                field.uncheck()
                continue
            # No consent, demographic, or other checkbox is silently accepted.
            pending.append(label or "Checkbox/radio sem identificacao")
            continue
        if not label:
            pending.append("Campo sem rotulo reconhecivel")
            continue
        answer = answer_for(label, config)
        if answer is None:
            pending.append(label)
            continue
        if field.evaluate("el => el.tagName") == "SELECT":
            options = field.locator("option").all()
            match = next((o for o in options if norm(o.inner_text()) == norm(answer) or o.get_attribute("value") == answer), None)
            if match is None:
                pending.append(label + " (opcao nao encontrada)")
                continue
            field.select_option(value=match.get_attribute("value"))
        else:
            field.fill(answer)
    return sorted(set(pending))


def apply(page, job, config, ledger):
    if re.search(r"\b(application submitted|candidatura enviada|ja se candidatou|already applied)\b", norm(page.locator("body").inner_text())):
        ledger.record(job, "already_applied")
        return "already_applied", []
    button = visible_button(page, r"Candidatura simplificada|Easy Apply")
    if button is None:
        return "not_easy_apply", []
    button.click()
    dialog = page.get_by_role("dialog").filter(has=page.locator("form")).first
    if not dialog.count():
        dialog = page.get_by_role("dialog").first
    dialog.wait_for(timeout=10000)
    resume = Path(config["resumes"][resume_language(job)])
    if not resume.is_absolute():
        resume = ROOT / resume
    if not resume.is_file() or resume.suffix.lower() != ".pdf":
        raise StopRun("Curriculo PDF nao encontrado. Revise config.local.json.")
    upload_seen = False
    for _ in range(12):
        guard(page)
        upload_seen |= dialog.locator('input[type="file"]').count() > 0
        pending = fill_step(dialog, config, resume)
        if pending:
            return "needs_review", pending
        submit = visible_button(dialog, r"^(Enviar candidatura|Submit application)$")
        if submit:
            if not upload_seen:
                return "needs_review", ["Nao foi possivel confirmar o anexo do curriculo nesta candidatura"]
            if not ledger.reserve(job, config.get("max_per_day")):
                raise StopRun("Cota local atingida, pausa ativa ou candidatura ja registrada.")
            try:
                # Reserve durably BEFORE the irreversible click; ambiguous results never retry.
                submit.click()
                page.get_by_role("heading", name=re.compile(
                    r"^(Your )?application (was )?(sent|submitted)|^(Sua )?candidatura (foi )?enviada", re.I
                )).first.wait_for(state="visible", timeout=15000)
                ledger.record(job, "submitted", "Confirmacao visivel de envio")
                return "submitted", []
            except Exception:
                ledger.record(job, "uncertain", "Clique de envio iniciado; confirmar no LinkedIn antes de qualquer nova tentativa")
                guard(page)
                raise StopRun("Resultado de envio incerto; registrado sem repetir a candidatura.")
        next_button = visible_button(dialog, r"^(Next|Avançar|Continuar|Continue|Review|Revisar|Revisar candidatura)$")
        if not next_button:
            return "needs_review", ["Etapa ou botao nao reconhecido"]
        next_button.click()
        errors = dialog.locator('.artdeco-inline-feedback--error, [role="alert"]').all_inner_texts()
        if errors:
            return "needs_review", ["Validacao do formulario: " + " ".join(errors)]
    return "needs_review", ["Formulario excedeu 12 etapas"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "login", "plan", "run"])
    parser.add_argument("--max-candidates", type=int, help="Limite de vagas examinadas nesta execucao")
    parser.add_argument("--minutes", type=float, help="Tempo maximo desta execucao")
    args = parser.parse_args()
    summary = {"at": Ledger.now(), "mode": args.command, "submitted": 0, "candidates": 0, "pending": [], "results": [], "status": "ok"}
    ledger = None
    try:
        config = load_config()
        if args.max_candidates is not None:
            if args.max_candidates < 1:
                raise StopRun("--max-candidates deve ser positivo")
            config["max_candidates"] = args.max_candidates
        if args.minutes is not None:
            if args.minutes <= 0:
                raise StopRun("--minutes deve ser positivo")
            config["max_minutes"] = args.minutes
        for resume in config["resumes"].values():
            if not (ROOT / resume).is_file():
                raise StopRun("Curriculo configurado nao encontrado: " + str(resume))
        if args.command == "check":
            print("CONFIG_OK: curriculos encontrados; nenhuma conexao ou candidatura executada.")
            return 0
        from playwright.sync_api import sync_playwright
        with single_run(ROOT / "data/run.lock"):
            ledger = Ledger(ROOT / "data/applications.sqlite3")
            if args.command == "run" and ledger.paused():
                raise StopRun(ledger.paused())
            # Without a confirmed local login, unattended runs don't repeatedly access login.
            if args.command != "login" and not (ROOT / "data/login-status.json").exists():
                raise StopRun("LOGIN_NECESSARIO: abra Entrar-LinkedIn.cmd e entre pessoalmente uma vez.")
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    str(ROOT / "data/browser-profile"), channel=config.get("browser_channel", "msedge"),
                    headless=False if args.command == "login" else config.get("headless", True),
                    accept_downloads=False, locale="pt-BR", viewport={"width": 1440, "height": 1000})
                context.set_default_timeout(10000)
                try:
                    if args.command == "login":
                        login(context)
                        return 0
                    deadline = time.monotonic() + 60 * config.get("max_minutes", 60)
                    page = context.pages[0] if context.pages else context.new_page()
                    ids = collect(page, config, deadline)
                    jobs = []
                    for job_id in ids:
                        if time.monotonic() > deadline:
                            break
                        if ledger.protected(job_id):
                            continue
                        try:
                            job = read_job(page, job_id)
                        except Review as e:
                            summary["pending"].append({"job_id": job_id, "questions": [str(e)]})
                            continue
                        if eligible(job):
                            jobs.append(job)
                    jobs.sort(key=lambda j: (-j.score, j.id))
                    summary["candidates"] = len(jobs)
                    save_json(ROOT / "data/ranked-jobs.json", [asdict(job) | {"url": job.url} for job in jobs])
                    if args.command == "plan":
                        return 0
                    for job in jobs:
                        if time.monotonic() > deadline:
                            break
                        if config.get("max_per_day") is not None and ledger.count_today() >= config["max_per_day"]:
                            summary["stop_reason"] = "Cota local atingida"
                            break
                        # A fresh tab prevents carrying a partially filled modal into the next application.
                        application_page = context.new_page()
                        try:
                            fresh_job = read_job(application_page, job.id)
                            if not eligible(fresh_job):
                                continue
                            status, pending = apply(application_page, fresh_job, config, ledger)
                            if status not in {"submitted", "already_applied"}:
                                ledger.record(job, status, "; ".join(pending))
                            summary["results"].append({"id": job.id, "title": job.title, "company": job.company, "status": status, "url": job.url})
                            if pending:
                                summary["pending"].append({"job_id": job.id, "url": job.url, "questions": pending})
                            summary["submitted"] += status == "submitted"
                            # Checkpoint even if the process later crashes.
                            save_json(ROOT / "data/last-run.json", summary)
                            if status == "submitted":
                                application_page.wait_for_timeout(max(0, config.get("seconds_between_applications", 30)) * 1000)
                        finally:
                            application_page.close()
                finally:
                    context.close()
    except StopRun as e:
        summary.update(status="stopped", stop_reason=str(e))
        if ledger and ("Limite ou pausa" in str(e) or "Verificacao de seguranca" in str(e) or "CAPTCHA" in str(e)):
            ledger.pause(str(e))
        return 2
    except Exception as e:
        # Log exception class only: browser exceptions can embed personal form values.
        summary.update(status="error", stop_reason=f"{type(e).__name__}: layout, sessao ou rede precisa de revisao local")
        return 1
    finally:
        if args.command != "check":
            save_json(ROOT / "data/last-run.json", summary)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            save_json(ROOT / f"data/reports/{stamp}-{args.command}.json", summary)
            if ledger:
                ledger.close()
            print(json.dumps({k: summary[k] for k in ["status", "submitted", "candidates"]} | {"reason": summary.get("stop_reason", "")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
