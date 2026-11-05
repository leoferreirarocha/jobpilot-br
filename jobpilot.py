"""LinkedIn Easy Apply assistant. No stealth, CAPTCHA solving, or invented answers."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout

from core import Job, Ledger, answer_for, eligible, eligibility_review, norm, rank, resume_language, save_json, search_urls
from page_reader import JOB_READY_JS, JOB_SNAPSHOT_JS

ROOT = Path(__file__).resolve().parent
APPLY_SELECTOR = '[data-view-name="job-apply-button"], button[aria-label*="simplificada"], button[aria-label*="Easy Apply"]'
STEP_SIGNATURE_JS = r"""el => JSON.stringify({
    page:(el.innerText.match(/\b\d+\s+(?:de|of)\s+\d+/i)||[])[0]||'',
    fields:[...el.querySelectorAll('input,textarea,select,[role="checkbox"],[role="radio"]')].map(field=>
        [field.tagName,field.id,field.type,field.name,field.getAttribute('aria-label'),field.getClientRects().length>0].join('|')),
    headings:[...el.querySelectorAll('h2,h3,legend')].map(node=>node.innerText.trim()),
    buttons:[...el.querySelectorAll('button')].filter(node=>node.getClientRects().length>0).map(node=>node.innerText.trim()).filter(Boolean)
})"""
FORM_READY_JS = r"""el => [...el.querySelectorAll('button,[role="button"]')].some(button =>
    button.getClientRects().length>0 && /^(Next|Avançar|Continuar|Continue|Review|Revisar|Avaliar|Enviar candidatura|Submit application)\b/i.test(
        button.getAttribute('aria-label') || button.innerText.trim()))"""
CONFIRMATION_TIMEOUT_MS = 15000
STATUS_CONFIRMATION_JS = r"""() => {
    const headings=[...document.querySelectorAll('h2,h3,[role="heading"]')];
    const about=headings.find(el=>/^(Sobre a vaga|About the job)$/i.test(el.innerText.trim()));
    if(!about) return false;
    for(const heading of headings.filter(el=>/^(Status da candidatura|Application status)$/i.test(el.innerText.trim()))) {
        if(!(heading.compareDocumentPosition(about)&Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        for(let el=heading.parentElement,n=0; el && n<3; el=el.parentElement,n++) {
            if(el.contains(about)) break;
            if(el.innerText.split(/\n/).some(text=>/^(Candidatura (foi )?enviada|Application (was )?(sent|submitted)|Applied)$/i.test(text.trim()))) return true;
        }
    }
    return false;
}"""


class StopRun(Exception):
    pass


class Review(Exception):
    pass


class Unavailable(Review):
    pass


class BudgetReached(Exception):
    pass


def timeout_for(deadline, maximum=30000):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BudgetReached()
    return maximum if remaining >= maximum / 1000 else max(1, int(remaining * 1000))


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
    if any(value in url for value in ["/checkpoint/", "/challenge/", "/authwall"]):
        raise StopRun("Verificacao de seguranca do LinkedIn. Resolva pessoalmente no modo login.")
    if re.search(r"/login|/uas/", url):
        raise StopRun("Sessao ausente ou expirada. Abra Entrar-LinkedIn.cmd.")
    text = norm(page.locator("body").inner_text(timeout=10000))
    limits = ["reached today's easy apply limit", "reached your daily", "daily application limit",
              "daily easy apply limit", "atingiu o limite de candidaturas", "limite diario de candidaturas", "limite de candidaturas simplificadas",
              "applying too quickly", "too many applications", "temporary pause on easy apply"]
    notices = norm(" ".join(page.locator('[role="alert"], [role="dialog"], .artdeco-toast-item').all_inner_texts()))
    if any(value in text for value in limits) or (re.search(r"candidat|appl", notices) and any(value in notices for value in ["atingiu o limite", "limite diario", "temporarily paused", "try again tomorrow", "tente novamente amanha", "muitas candidaturas", "muito rapido"])):
        raise StopRun("Limite ou pausa do LinkedIn detectado; nenhuma nova tentativa hoje (UTC).")
    for challenge in page.locator('iframe[src*="captcha"], iframe[title*="reCAPTCHA"], #captcha-internal').all():
        if challenge.is_visible():
            raise StopRun("CAPTCHA visivel detectado. Resolva pessoalmente; a rotina nao tenta contornar a verificacao.")


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


def collect(page, config, deadline, issues=None):
    issues = issues if issues is not None else []
    ids = []
    for url in search_urls(config):
        if time.monotonic() > deadline:
            break
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_for(deadline))
            guard(page)
            if response and response.status == 429:
                raise StopRun("Limite ou pausa HTTP 429 do LinkedIn; nenhuma nova tentativa hoje (UTC).")
            if response and response.status == 403:
                raise StopRun("Acesso recusado pelo LinkedIn (HTTP 403); verifique pessoalmente a sessao.")
            if not authenticated(page):
                raise StopRun("Nao foi possivel confirmar uma sessao autenticada. Abra Entrar-LinkedIn.cmd.")
            # Wait for actual cards, not the results container before hydration.
            page.wait_for_function(r"""() => {
                if(document.querySelector('[componentkey^="job-card-component-ref-"], [data-occludable-job-id], .job-card-container')) return true;
                return /nenhuma vaga|nenhum resultado|nao encontramos|não encontramos|no jobs found|no matching jobs|\b0 resultados\b/i.test(document.body.innerText);
            }""", timeout=timeout_for(deadline, 25000))
            guard(page)
            container = page.locator('.jobs-search-results-list, .scaffold-layout__list, [componentkey="SearchResultsMainContent"]').first
            for _ in range(4):
                timeout_for(deadline)
                # Only links within search results; recommendation links are unrelated.
                cards = page.locator('[componentkey^="job-card-component-ref-"][role="button"], [data-occludable-job-id]')
                for card in cards.all():
                    raw = card.get_attribute("componentkey") or card.get_attribute("data-occludable-job-id") or ""
                    match = re.search(r"(\d+)$", raw)
                    if match and match[1] not in ids:
                        ids.append(match[1])
                if container.count():
                    for link in container.locator('a[href*="/jobs/view/"]').all():
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
        except BudgetReached:
            break
        except BrowserError as e:
            try:
                guard(page)
            except BrowserError:
                pass
            issues.append({"url": url, "reason": f"{type(e).__name__}: pesquisa nao concluida; demais pesquisas continuam"})
    return ids


def read_job(page, job_id, deadline=float("inf")):
    """Retry read-only navigation once; isolate a bad ad without retrying a submission."""
    for attempt in range(2):
        try:
            response = page.goto(f"https://www.linkedin.com/jobs/view/{job_id}/", wait_until="domcontentloaded", timeout=timeout_for(deadline))
            guard(page)
            status = response.status if response else 200
            if status == 429:
                raise StopRun("Limite ou pausa HTTP 429 do LinkedIn; nenhuma nova tentativa hoje (UTC).")
            if status in {401, 403}:
                raise StopRun("Acesso recusado pelo LinkedIn. Verifique pessoalmente a sessao.")
            if status in {404, 410}:
                raise Unavailable("Anuncio removido ou indisponivel")
            if status >= 500:
                if attempt == 0:
                    continue
                raise Review(f"Servidor indisponivel (HTTP {status}); vaga ignorada nesta execucao")
            page.wait_for_function(JOB_READY_JS, timeout=timeout_for(deadline, 15000))
            guard(page)
            return parse_job(page, job_id)
        except (BrowserTimeout, BrowserError) as e:
            # Stop for authentication/security barriers even if hydration timed out.
            try:
                guard(page)
            except BrowserError:
                pass
            if attempt:
                raise Review(f"{type(e).__name__}: leitura nao concluida apos duas tentativas") from None
        except Unavailable:
            raise
        except Review:
            if attempt:
                raise
    raise Review("Leitura da vaga nao concluida")


def parse_job(page, job_id):
    snapshot = page.evaluate(JOB_SNAPSHOT_JS, APPLY_SELECTOR)
    if snapshot["unavailable"]:
        raise Unavailable("Anuncio encerrado ou indisponivel")
    title = first_text(page, "h1")
    company = first_text(page, ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name")
    location = first_text(page, ".job-details-jobs-unified-top-card__tertiary-description-container, .jobs-unified-top-card__bullet")
    workplace = first_text(page, ".job-details-jobs-unified-top-card__job-insight, .jobs-unified-top-card__workplace-type")
    # LinkedIn sometimes renders workplace/salary as multiple insight buttons.
    insights = page.locator(".job-details-jobs-unified-top-card__job-insight, .job-details-fit-level-preferences button, .jobs-unified-top-card__workplace-type")
    if insights.count():
        workplace = " ".join(insights.all_inner_texts())
    description = first_text(page, "#job-details, .jobs-description__content, .jobs-description-content__text")
    title = title or snapshot["title"]
    company = company or snapshot["company"]
    location = location or snapshot["location"]
    workplace = workplace or snapshot["workplace"]
    description = description or snapshot["description"]
    salary_source = snapshot["pay"] or workplace
    if not title or not description:
        raise Review("Layout da vaga nao reconhecido; descricao ou titulo ausente.")
    salary = " ".join(line for line in (salary_source + "\n" + description).splitlines()
                      if "R$" in line and re.search(r"mes\b|mensal|mensais|month|/mo\b", norm(line)))
    return rank(Job(job_id, title, company, location, workplace, description, salary))


def label_of(field):
    return field.evaluate(r"""el => {
      const ids=(el.getAttribute('aria-labelledby')||'').split(/\s+/);
      const root=el.getRootNode();
      const labels=el.labels ? [...el.labels].map(x=>{
        const copy=x.cloneNode(true);
        copy.querySelectorAll('input,select,textarea,button').forEach(child=>child.remove());
        return copy.textContent;
      }).join(' ').trim() : '';
      return labels || el.getAttribute('aria-label') || ids.map(id=>root.getElementById?.(id)?.innerText||'').join(' ').trim() || '';
    }""")


def fill_step(dialog, config, resume: Path, selected_resume=False):
    pending = []
    for field in dialog.locator('input, textarea, select, [role="checkbox"], [role="radio"]').all():
        kind = (field.get_attribute("type") or field.get_attribute("role") or "").lower()
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
        field_id = field.get_attribute("id")
        labels = dialog.locator('label[for=' + json.dumps(field_id) + ']').all() if field_id else []
        labels += field.locator("xpath=ancestor::label[1]").all()
        visible_labels = [label for label in labels if label.is_visible()]
        if kind == "radio" and selected_resume:
            document_label = field.evaluate("el=>el.closest('div[aria-label]')?.getAttribute('aria-label')||''")
            if re.search(r"\.(pdf|docx?)$", document_label, re.I):
                continue
        if not field.is_visible() and not visible_labels:
            continue
        label = label_of(field).strip()
        if kind in {"radio", "checkbox"}:
            if norm(label).startswith(("follow ", "seguir ", "siga ")):
                checked = field.is_checked() if field.evaluate("el=>el.tagName") == "INPUT" else field.get_attribute("aria-checked") == "true"
                if checked:
                    (field if field.is_visible() else visible_labels[0]).click()
                continue
            # No consent, demographic, or other checkbox is silently accepted.
            pending.append(label or "Checkbox/radio sem identificacao")
            continue
        if not label:
            pending.append("Campo sem rotulo reconhecivel")
            continue
        answer = answer_for(label, config)
        if answer is None:
            required = "*" in label or field.evaluate("el=>!!el.required || el.getAttribute('aria-required')==='true' || el.getAttribute('aria-invalid')==='true'")
            if not required and not field.input_value().strip():
                # Empty optional questions stay blank; unknown prefilled values still need review.
                continue
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


def select_uploaded_resume(page, dialog, resume: Path) -> bool:
    """Current UI opens a file chooser instead of exposing an input[type=file]."""
    upload = visible_button(dialog, r"^(Carregar currículo|Upload resume|Upload CV)$")
    if upload is None:
        return False
    digest = hashlib.sha256(resume.read_bytes()).hexdigest()
    history_path = ROOT / "data/resume-uploads.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {}
    name_pattern = re.compile(r"(?:^|\s)" + re.escape(resume.name) + r"(?:\s|$)", re.I)
    choices = dialog.get_by_role("button", name=name_pattern)
    if history.get(resume.name) != digest or not choices.count():
        with page.expect_file_chooser(timeout=10000) as chooser:
            upload.click()
        chooser.value.set_files(str(resume))
        choices.first.wait_for(state="visible", timeout=20000)
    choices.first.click()
    selected = False
    for container in dialog.locator("div[aria-label]").all():
        if container.get_attribute("aria-label") == resume.name:
            radio = container.locator('input[type="radio"]')
            if radio.count() and radio.first.is_checked():
                selected = True
    if not selected:
        raise Review("O PDF foi carregado, mas sua selecao nao foi confirmada")
    history[resume.name] = digest
    save_json(history_path, history)
    return True


def confirm_submission_by_status(page, job):
    """One read-only GET can confirm an accepted application after a lost modal."""
    guard(page)
    try:
        response = page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
        guard(page)
        if response and response.status == 429:
            raise StopRun("Limite ou pausa HTTP 429 do LinkedIn; nenhuma nova tentativa hoje (UTC).")
        if response and response.status != 200:
            return False
        page.wait_for_function(STATUS_CONFIRMATION_JS, timeout=10000)
        return True
    except BrowserError:
        try:
            guard(page)
        except BrowserError:
            pass
        return False


def apply(page, job, config, ledger):
    requirement = eligibility_review(job, config)
    if requirement:
        return "needs_review", [requirement]
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
        # The dialog shell appears before its asynchronous form has rendered.
        page.wait_for_function(FORM_READY_JS, arg=dialog.element_handle(), timeout=15000)
        try:
            upload_seen |= select_uploaded_resume(page, dialog, resume)
        except Review as e:
            return "needs_review", [str(e)]
        upload_seen |= dialog.locator('input[type="file"]').count() > 0
        pending = fill_step(dialog, config, resume, selected_resume=upload_seen)
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
                )).first.wait_for(state="visible", timeout=CONFIRMATION_TIMEOUT_MS)
                ledger.record(job, "submitted", "Confirmacao visivel de envio")
                return "submitted", []
            except Exception:
                ledger.record(job, "uncertain", "Clique de envio iniciado; confirmar no LinkedIn antes de qualquer nova tentativa")
                if confirm_submission_by_status(page, job):
                    ledger.record(job, "submitted", "Confirmado por consulta somente de leitura ao status da candidatura")
                    return "submitted", []
                guard(page)
                raise StopRun("Resultado de envio incerto; registrado sem repetir a candidatura.")
        next_button = visible_button(dialog, r"^(Next|Avançar|Continuar|Continue|Review|Revisar|Avaliar)\b")
        if not next_button:
            buttons = [button.inner_text().strip()[:80] for button in dialog.get_by_role("button").all() if button.is_visible()]
            return "needs_review", ["Etapa ou botao nao reconhecido: " + ", ".join(text for text in buttons if text)]
        before = dialog.evaluate(STEP_SIGNATURE_JS)
        element = dialog.element_handle()
        next_button.click()
        # React may keep the previous controls visible while the next step loads.
        # Audit the new step only after its structure changes, never click Next twice.
        page.wait_for_function("""args => !args.element.isConnected ||
            args.element.querySelector('[aria-invalid="true"], .artdeco-inline-feedback--error') ||
            (""" + STEP_SIGNATURE_JS + ")(args.element) !== args.before", arg={"element": element, "before": before}, timeout=10000)
        dialog.wait_for(state="visible", timeout=10000)
        errors = dialog.locator('.artdeco-inline-feedback--error').all_inner_texts()
        errors += [label_of(field) + " (campo invalido)" for field in dialog.locator('[aria-invalid="true"]').all()]
        if errors:
            return "needs_review", ["Validacao do formulario: " + " ".join(errors)]
    return "needs_review", ["Formulario excedeu 12 etapas"]


def checkpoint(summary, jobs=None):
    summary["updated_at"] = Ledger.now()
    save_json(ROOT / "data/last-run.json", summary)
    if jobs is not None:
        save_json(ROOT / "data/ranked-jobs.json", [asdict(job) | {"url": job.url} for job in sorted(jobs, key=lambda j: (-j.score, j.id))])


def scan_jobs(page, ids, ledger, summary, deadline):
    jobs = []
    for job_id in ids:
        if time.monotonic() >= deadline:
            summary["finish_reason"] = "Tempo reservado para leitura encerrado"
            break
        if ledger.protected(job_id):
            summary["skipped_registered"] += 1
            checkpoint(summary, jobs)
            continue
        summary["scanned"] += 1
        summary["phase"] = "read_job:" + job_id
        try:
            job = read_job(page, job_id, deadline)
            if eligible(job):
                jobs.append(job)
                summary["candidates"] = len(jobs)
            else:
                summary["excluded"] += 1
        except BudgetReached:
            summary["finish_reason"] = "Tempo reservado para leitura encerrado"
            break
        except Unavailable as e:
            summary["skipped_unavailable"] += 1
            summary["read_issues"].append({"job_id": job_id, "kind": "unavailable", "reason": str(e)})
        except (Review, BrowserError) as e:
            summary["read_issues"].append({"job_id": job_id, "kind": "read_error", "reason": str(e) if isinstance(e, Review) else type(e).__name__})
        finally:
            checkpoint(summary, jobs)
    return sorted(jobs, key=lambda j: (-j.score, j.id))


def send_applications(context, jobs, config, ledger, summary, deadline):
    for job in jobs:
        if time.monotonic() >= deadline:
            summary["finish_reason"] = "Tempo maximo da execucao atingido"
            break
        if ledger.protected(job.id):
            summary["skipped_registered"] += 1
            continue
        if config.get("max_per_day") is not None and ledger.count_today() >= config["max_per_day"]:
            summary["finish_reason"] = "Cota local atingida"
            break
        page = context.new_page()
        try:
            summary["phase"] = "apply:" + job.id
            fresh_job = read_job(page, job.id, deadline)
            if not eligible(fresh_job):
                summary["excluded"] += 1
                continue
            status, pending = apply(page, fresh_job, config, ledger)
        except BudgetReached:
            summary["finish_reason"] = "Tempo maximo da execucao atingido"
            break
        except Unavailable:
            status, pending = "unavailable", []
            summary["skipped_unavailable"] += 1
        except (Review, BrowserError) as e:
            # Never turn a reserved submission into a retryable form error.
            if ledger.status(job.id) == "submitting":
                ledger.record(job, "uncertain", "Resultado ambiguo apos reserva de envio")
                raise StopRun("Resultado de envio incerto; verificar pessoalmente antes de tentar novamente.") from None
            try:
                guard(page)
            except BrowserError:
                pass
            status, pending = "needs_review", [str(e) if isinstance(e, Review) else type(e).__name__ + ": formulario nao carregado; demais vagas continuam"]
        finally:
            try:
                page.close()
            except BrowserError:
                pass
        if status not in {"submitted", "already_applied"}:
            ledger.record(job, status, "; ".join(pending))
        summary["results"].append({"id": job.id, "title": job.title, "company": job.company, "status": status, "url": job.url})
        if pending:
            summary["pending"].append({"job_id": job.id, "url": job.url, "questions": pending})
        summary["submitted"] += status == "submitted"
        checkpoint(summary)
        if status == "submitted":
            remaining = max(0, deadline - time.monotonic())
            time.sleep(min(remaining, max(0, config.get("seconds_between_applications", 30))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "login", "plan", "run"])
    parser.add_argument("--max-candidates", type=int, help="Limite de vagas examinadas nesta execucao")
    parser.add_argument("--minutes", type=float, help="Tempo maximo desta execucao")
    parser.add_argument("--job-id", action="append", help="Examinar uma vaga especifica; os filtros continuam obrigatorios")
    args = parser.parse_args()
    summary = {"at": Ledger.now(), "mode": args.command, "submitted": 0, "candidates": 0,
               "discovered": 0, "scanned": 0, "excluded": 0, "skipped_registered": 0,
               "skipped_unavailable": 0, "read_issues": [], "search_issues": [],
               "pending": [], "results": [], "status": "running"}
    ledger = None
    owns_report = False
    try:
        config = load_config()
        if args.job_id and any(not re.fullmatch(r"\d+", value) for value in args.job_id):
            raise StopRun("--job-id deve conter somente o numero da vaga")
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
            owns_report = True
            checkpoint(summary)
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
                    started = time.monotonic()
                    duration = 60 * config.get("max_minutes", 60)
                    deadline = started + duration
                    page = context.pages[0] if context.pages else context.new_page()
                    summary["phase"] = "search"
                    # Keep part of the run available for applications, even with slow ads.
                    search_deadline = started + duration * 0.25 if args.command == "run" else deadline
                    ids = args.job_id or collect(page, config, search_deadline, summary["search_issues"])
                    summary["discovered"] = len(ids)
                    checkpoint(summary)
                    read_deadline = started + duration * 0.70 if args.command == "run" else deadline
                    jobs = scan_jobs(page, ids, ledger, summary, read_deadline)
                    if args.command == "run":
                        send_applications(context, jobs, config, ledger, summary, deadline)
                    if not jobs and (any(issue["kind"] == "read_error" for issue in summary["read_issues"]) or summary["search_issues"]) and not summary["excluded"] and not summary["skipped_registered"] and not summary["skipped_unavailable"]:
                        summary.update(status="error", stop_reason="Nenhuma vaga pode ser classificada; consulte os problemas de leitura/pesquisa no relatorio")
                        return 1
                finally:
                    context.close()
    except StopRun as e:
        summary.update(status="stopped", stop_reason=str(e))
        if ledger and ("Limite ou pausa" in str(e) or "Verificacao de seguranca" in str(e) or "CAPTCHA" in str(e)):
            ledger.pause(str(e))
        return 2
    except BudgetReached:
        summary.update(status="partial", finish_reason="Tempo maximo da execucao atingido")
        return 0
    except Exception as e:
        # Log exception class only: browser exceptions can embed personal form values.
        summary.update(status="error", stop_reason=f"{type(e).__name__} ({summary.get('phase', 'startup')}): layout, sessao ou rede precisa de revisao local")
        return 1
    finally:
        if args.command != "check":
            if summary["status"] == "running":
                summary["status"] = "partial" if any(summary[k] for k in ("read_issues", "search_issues", "pending")) or summary.get("finish_reason") else "ok"
            if owns_report:
                checkpoint(summary)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            if owns_report:
                save_json(ROOT / f"data/reports/{stamp}-{args.command}.json", summary)
            if ledger:
                ledger.close()
            print(json.dumps({k: summary[k] for k in ["status", "submitted", "candidates"]} | {"reason": summary.get("stop_reason", "")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
