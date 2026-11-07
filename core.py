"""Local matching, truthful answers, and an auditable submission ledger."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode


def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().split())


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    workplace: str
    description: str
    salary_text: str = ""
    score: float = 0
    reasons: str = ""

    @property
    def url(self) -> str:
        return f"https://www.linkedin.com/jobs/view/{self.id}/"


def eligible(job: Job) -> bool:
    loc, mode = norm(job.location), norm(job.workplace)
    remote = bool(re.search(r"\b(remoto|remote|home office)\b", mode))
    brazil = bool(re.search(r"\b(brasil|brazil)\b", loc))
    curitiba = bool(re.search(r"\bcuritiba\b", loc))
    local = bool(re.search(r"\b(presencial|on.?site|hibrido|hybrid)\b", mode))
    # Unknown geography/workplace is review-only; the search filter alone is not proof.
    return (remote and brazil) or (curitiba and local)


def monthly_salary(text: str) -> float | None:
    """Compare only explicit monthly BRL figures. Never fabricate undisclosed pay."""
    t = norm(text)
    if not re.search(r"mensal|mensais|mes\b|month|/mo\b", t):
        return None
    values = re.findall(r"R\$\s*([\d.]+(?:,\d{1,2})?)\s*(mil|k)?", text, flags=re.I)
    amounts = []
    for value, suffix in values:
        number = float(value.replace(".", "").replace(",", "."))
        if suffix:
            number *= 1000
        if 500 <= number <= 150000:
            amounts.append(number)
    return min(amounts) if amounts else None


def rank(job: Job) -> Job:
    title, desc = norm(job.title), norm(job.description)
    score, reasons = 10.0, []
    tracks = [
        (r"\b(c#|csharp|unity|godot|gdscript)\b|c#", 35, "C#/Unity/Godot"),
        (r"desenvolv|developer|programador|software|\.net", 25, "desenvolvimento"),
        (r"\bqa\b|testador|quality assurance|tester|analista de testes", 20, "QA/testes"),
        (r"suporte|help.?desk|service.?desk|support", 17, "suporte"),
        (r"assistente|auxiliar|atendimento|administrativ|customer service", 5, "entrada ampla"),
    ]
    for pattern, points, reason in tracks:
        if re.search(pattern, title):
            score += points
            reasons.append(reason)
    if re.search(r"junior|\bjr\b|trainee|estagio|intern|assistente|auxiliar|entry.level", title):
        score += 20
        reasons.append("entrada/junior")
    # Higher seniority is permitted; the user wants to reach for stronger roles.
    for skill in ["c#", "gdscript", "unity", "godot", "git", "python", "java", "ingles", "english"]:
        if skill in desc:
            score += 2
    if re.search(r"plano de carreira|mentoria|mentoring|treinamento|training|career development", desc):
        score += 5
        reasons.append("desenvolvimento profissional anunciado")
    salary = monthly_salary(job.salary_text)
    if salary is not None:
        score += min(salary / 1000, 15)
        reasons.append(f"remuneracao mensal anunciada R$ {salary:.0f}")
    job.score, job.reasons = round(score, 2), "; ".join(reasons)
    return job


def resume_language(job: Job) -> str:
    text = norm(job.description)
    en = len(re.findall(r"\b(the|your|you|our|with|requirements|experience|responsibilities)\b", text))
    pt = len(re.findall(r"\b(voce|sua|nosso|com|requisitos|experiencia|responsabilidades|para)\b", text))
    return "en" if en >= 4 and en > pt else "pt"


def eligibility_review(job: Job, config: dict) -> str | None:
    reviewed = {str(job_id) for job_id in config.get("reviewed_eligibility_job_ids", [])}
    if job.id in reviewed:
        return None
    if re.search(r"\bpcd\b|pessoas? com deficiencia|\bafirmativ[ao]s?\b|\baffirmative\b", norm(job.title)):
        return "Vaga afirmativa ou com requisito especifico de elegibilidade: confirmar pessoalmente antes do envio"
    return None


def personal_answer(label: str, config: dict) -> str | None:
    """Answer only identified questions from explicit, current personal facts."""
    question = norm(label).rstrip(" *:?.")
    employment = config.get("employment") or {}
    if employment.get("relatives_or_close_contacts_at_employer") is False:
        if re.match(r"do you have (?:any )?(?:relatives?|family members?|close friends?|acquaintances?)\b", question) and re.search(r"\b(?:work at|working at|employed by)\b", question) and not re.search(r"\bnot\b", question):
            return "No"
        if re.match(r"(?:voce )?(?:tem|possui) (?:algum[a]?s? )?(?:parentes?|familiares?|amigos?|conhecidos?)\b", question) and re.search(r"\btrabalh(?:a|am|ando|e|em)\b.*\b(?:empresa|companhia|organizacao)\b", question) and not re.search(r"\bnao\b", question):
            return "Não"
    if employment.get("currently_employed") is False:
        company_pt = {
            "empresa atual", "empregador atual", "qual sua empresa atual",
            "qual e sua empresa atual", "em qual empresa voce trabalha atualmente",
            "confirme o nome da empresa em que voce trabalha atualmente",
            "confirm the name of the company where you work/ confirme o nome da empresa em que voce trabalha atualmente",
        }
        company_en = {"current company", "current employer", "what is your current company", "what is your current employer", "confirm the name of the company where you work"}
        if question in company_pt:
            return "Não trabalho atualmente"
        if question in company_en:
            return "Not currently employed"
        if question in {"voce esta trabalhando atualmente", "voce trabalha atualmente", "voce esta empregado atualmente"}:
            return "Não"
        if question in {"are you currently employed", "are you currently working"}:
            return "No"
        if question == "employment status":
            return "Unemployed"
        if question in {"situacao profissional atual", "situacao de emprego atual"}:
            return "Desempregado"
    availability = config.get("availability") or {}
    hours = availability.get("hours_per_day")
    weekdays = set(availability.get("weekdays", []))
    monday_to_friday = weekdays == {"monday", "tuesday", "wednesday", "thursday", "friday"}
    if hours is not None:
        if question in {"quantas horas por dia voce tem disponibilidade para trabalhar", "how many hours per day are you available to work"}:
            return str(hours)
    if hours == 8 and monday_to_friday:
        if question in {"quantas horas por semana voce tem disponibilidade para trabalhar", "how many hours per week are you available to work"}:
            return "40"
        if question in {"qual sua disponibilidade de horario", "qual e sua disponibilidade de horario", "qual sua disponibilidade de dias e horarios"}:
            return "8 horas por dia, de segunda a sexta-feira"
        if question in {"what is your work availability", "what is your availability", "what is your working availability"}:
            return "8 hours per day, Monday to Friday"
        if question in {
            "voce possui disponibilidade para trabalhar de segunda a sexta-feira",
            "voce possui disponibilidade para trabalhar 8 horas por dia, de segunda a sexta-feira",
            "voce possui disponibilidade para trabalhar 40 horas semanais",
            "voce tem disponibilidade para trabalhar em periodo integral",
        }:
            return "Sim"
        if question in {"are you available to work full-time", "are you available to work 40 hours per week", "can you work 8 hours per day, monday to friday"}:
            return "Yes"
        if question in {"quantos dias por semana voce tem disponibilidade para trabalhar", "how many days per week are you available to work"}:
            return "5"
        if question in {"pode trabalhar aos sabados", "voce tem disponibilidade para trabalhar aos sabados", "voce tem disponibilidade para trabalhar aos domingos", "aceita escala 12x36", "aceita atuar em escala 6x1"}:
            return "Não"
        if question in {"are you available to work weekends", "are you available to work on saturdays", "are you available to work on sundays"}:
            return "No"
    modes = set(availability.get("work_modes", []))
    if "onsite" in modes and availability.get("onsite_city") == "Curitiba":
        if question in {
            "voce possui disponibilidade para atuar em modelo presencial em curitiba/pr",
            "voce possui disponibilidade para atuar no modelo presencial em curitiba/pr",
            "voce possui disponibilidade de trabalhar presencialmente em curitiba",
        }:
            return "Sim"
    if "remote" in modes and question in {"are you available to work remotely", "are you willing to work remotely"}:
        return "Yes"
    if "remote" in modes and question == "voce possui disponibilidade para trabalhar remotamente":
        return "Sim"
    if "hybrid" in modes and question == "voce trabalharia em um modelo hibrido":
        return "Sim"
    if "hybrid" in modes and question == "are you willing to work in a hybrid model":
        return "Yes"
    return None


def answer_for(label: str, config: dict, *, contract_regime: str | None = None) -> str | None:
    """Fill identified questions only from explicit answers or confirmed facts."""
    key = norm(label).rstrip(" *:")
    explicit = {norm(k).rstrip(" *:"): str(v) for k, v in config.get("answers", {}).items()}
    if key in explicit:
        return explicit[key]
    # An unspecified-regime monthly target is used only when explicitly provided.
    # It never changes advertised salary floors or authorizes period conversions.
    salary_question = re.search(r"pretens|expectativ|expected|desired|salary expectation|compensation expectation", key) and re.search(r"salar|remuner|\bcompensation\b|\bpay\b", key)
    # These exact parenthetical instructions concern numeric formatting, not pay
    # inclusive of benefits. Keep all other salary-basis restrictions intact.
    salary_basis = re.sub(r"\((?:no currency, text, or benefits|sem moeda, texto ou beneficios)\)", "", key)
    unsupported_period = re.search(r"ultimo|ultima|previous|last salary|salario atual|current salary|anual|annual|year|hora|hour|diari|daily|semana|weekly|\bweek\b|liquid[oa]|\bnet\b|bonus|beneficio|\bbenefits?\b|comiss|equity|stock|tarefa|\btasks?\b|projeto|\bprojects?\b", salary_basis)
    if salary_question and not unsupported_period:
        clt, pj = bool(re.search(r"\bclt\b", key)), bool(re.search(r"\bpj\b", key))
        if clt != pj:
            value = config.get("salary_expectations", {}).get("clt" if clt else "pj")
        elif clt and pj and re.search(r"separad|respectiv|each|both", key):
            return None
        else:
            value = config.get("salary_expectations", {}).get("unspecified")
        if value is not None:
            # Even a generic question must not quote below a known contract's floor.
            if clt == pj and contract_regime in {"clt", "pj"}:
                floor = config.get("minimum_monthly_salary", {}).get(contract_regime)
                if isinstance(value, (int, float)) and isinstance(floor, (int, float)):
                    return f"{max(value, floor):g}"
            return str(value)
    personal = personal_answer(label, config)
    if personal is not None:
        return personal
    contact = config.get("contact", {})
    fields = {
        "email": "email", "email address": "email", "endereco de email": "email", "e-mail": "email",
        "mobile phone number": "phone", "phone number": "phone", "numero de telefone": "phone",
        "telefone celular": "phone", "numero de telefone celular": "phone", "celular": "phone",
        "numero de celular": "phone", "telefone": "phone", "mobile number": "phone", "phone": "phone",
        "codigo do pais": "phone_country", "country/region code": "phone_country",
        "first name": "first_name", "nome": "first_name", "last name": "last_name", "sobrenome": "last_name",
        "linkedin profile": "linkedin", "perfil do linkedin": "linkedin", "linkedin": "linkedin",
        "github": "github", "portfolio": "portfolio",
        "current location": "location", "what is your current location?": "location",
        "localizacao atual": "location", "qual e a sua localizacao atual?": "location",
    }
    if key in {"full name", "nome completo"}:
        value = contact.get("full_name") or " ".join(contact.get(part, "") for part in ("first_name", "last_name")).strip()
        return value or None
    if key == "how did you hear about this job?":
        return "LinkedIn"
    value = contact.get(fields.get(key, ""))
    return str(value) if value else None


def search_urls(config: dict):
    for query in config["queries"]:
        for area in ("remote", "curitiba"):
            for page in range(config.get("pages_per_search", 1)):
                params = {"keywords": query, "location": "Brasil" if area == "remote" else "Curitiba, Paraná, Brasil",
                          "geoId": "106057199" if area == "remote" else "103501557",
                          "f_WT": "2" if area == "remote" else "1,3", "f_AL": "true",
                          "f_TPR": "r604800", "sortBy": "DD", "start": page * 25}
                yield "https://www.linkedin.com/jobs/search/?" + urlencode(params)


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS applications
            (job_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, submit_day TEXT,
             title TEXT, company TEXT, detail TEXT)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS pauses (day TEXT PRIMARY KEY, reason TEXT)")
        self.db.commit()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def day():
        return datetime.now(timezone.utc).date().isoformat()

    def protected(self, job_id: str) -> bool:
        return self.status(job_id) in {"submitting", "submitted", "uncertain", "already_applied"}

    def status(self, job_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM applications WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else None

    def count_today(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM applications WHERE submit_day=?", (self.day(),)).fetchone()[0]

    def pause(self, reason: str):
        self.db.execute("INSERT OR REPLACE INTO pauses VALUES (?, ?)", (self.day(), reason))
        self.db.commit()

    def paused(self) -> str | None:
        row = self.db.execute("SELECT reason FROM pauses WHERE day=?", (self.day(),)).fetchone()
        return row[0] if row else None

    def record(self, job: Job, status: str, detail: str = ""):
        # Preserve the submission day even when confirming a pending/uncertain outcome.
        self.db.execute("""INSERT INTO applications VALUES (?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
            updated_at=excluded.updated_at, detail=excluded.detail""",
                        (job.id, status, self.now(), job.title, job.company, detail))
        self.db.commit()

    def reserve(self, job: Job, cap: int | None) -> bool:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.protected(job.id) or self.paused() or (cap is not None and self.count_today() >= cap):
                self.db.rollback()
                return False
            self.db.execute("""INSERT INTO applications VALUES (?, 'submitting', ?, ?, ?, ?, '')
                ON CONFLICT(job_id) DO UPDATE SET status='submitting', updated_at=excluded.updated_at,
                submit_day=excluded.submit_day""", (job.id, self.now(), self.day(), job.title, job.company))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
