"""Conservative, auditable gates applied before ranking and before sending."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import Job, eligible, eligibility_review, norm


@dataclass
class Decision:
    status: str
    reasons: list[str] = field(default_factory=list)


MONEY = re.compile(r"(?:R\$|\bBRL)\s*(\d+(?:[.,]\d+)*)(?:\s*(mil|k)\b)?", re.I)
PAY_LABEL = re.compile(r"salari|remuner|salary|compensation|base pay|valor mensal|faixa salarial|vencimento")
PERIOD = re.compile(r"/\s*(?:h\b|hr\b|hora|hour|ano|year)|por hora|por ano|per hour|per year|\b(hourly|annual|annually|anual|anuais|yearly|diari[ao]|daily)\b")
BENEFIT = re.compile(r"\b(vr|va|vt|vale|beneficio|bonus|bonificacao|comiss|commission|ajuda de custo|reembolso|refeicao|alimentacao|transporte)")


def brl_number(value: str, suffix: str | None = None) -> float:
    if ',' in value:
        number = float(value.replace('.', '').replace(',', '.'))
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        number = float(value.replace('.', ''))
    else:
        number = float(value)
    return number * (1000 if suffix else 1)


def salary_evidence(job: Job) -> tuple[list[float], list[str], str | None]:
    """Fixed BRL salary only; never add benefits, commissions or convert periods."""
    amounts, issues = [], []
    text = '\n'.join((job.title, job.salary_text, job.description))
    regimes = set(re.findall(r"\b(clt|pj)\b", norm(text)))
    regime = next(iter(regimes)) if len(regimes) == 1 else None
    for raw in text.splitlines():
        line = norm(raw)
        if not MONEY.search(raw):
            if re.search(r"\$\s*\d|\b(?:usd|eur)\s*\d", line) and (PAY_LABEL.search(line) or PERIOD.search(line) or raw == job.title):
                issues.append('Remuneracao em outra moeda/periodo exige revisao')
            elif PAY_LABEL.search(line) and re.search(r"\b\d{3,}(?:[.,]\d+)*\b", line):
                issues.append('Remuneracao numerica sem moeda/periodo confirmado')
            continue
        label = PAY_LABEL.search(line)
        benefit = BENEFIT.search(line)
        # Benefits alone do not constitute a published salary.
        if benefit and (not label or benefit.start() < label.start()):
            continue
        if not label and not re.search(r"/mes|mensal|mensais|month|/mo\b", line):
            issues.append('Valor monetario sem identificacao inequívoca de salario fixo mensal')
            continue
        base = re.split(r"\+|\b(?:mais beneficios|plus benefits)\b", raw, maxsplit=1, flags=re.I)[0]
        if PERIOD.search(norm(base)):
            issues.append('Salario anunciado por hora/dia/ano; comparacao mensal exige revisao')
            continue
        # A variable/commission-only total cannot satisfy the fixed salary floor.
        if re.search(r"ate\b|up to|variavel|comissionad|ote\b|potencial|media de ganhos", norm(base)):
            issues.append('Remuneracao maxima/variavel sem piso fixo confirmado')
            continue
        values = [brl_number(m[0], m[1]) for m in MONEY.findall(base)]
        if not values or any(value < 500 or value > 150000 for value in values):
            issues.append('Formato ou valor de salario exige revisao')
            continue
        amounts.extend(values)
    return amounts, sorted(set(issues)), regime


def salary_gate(job: Job, config: dict) -> Decision:
    amounts, issues, regime = salary_evidence(job)
    floors = config.get('minimum_monthly_salary')
    if not isinstance(floors, dict) or set(floors) != {'clt', 'pj'} or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 for v in floors.values()
    ):
        return Decision('review', ['Pisos mensais CLT/PJ nao configurados; nao enviar'])
    if amounts:
        amount = min(amounts)
        lowest, highest = min(floors.values()), max(floors.values())
        floor = floors.get(regime) if regime else None
        if amount < (floor if floor is not None else lowest):
            return Decision('excluded', [f'Salario fixo anunciado R$ {amount:.2f} abaixo do piso ' + (f'{regime.upper()} de R$ {floor:.2f}' if floor is not None else f'minimo de R$ {lowest:.2f}')])
        if floor is None and amount < highest:
            issues.append('Regime CLT/PJ nao confirmado para aplicar o piso salarial correto')
    if issues:
        return Decision('review', issues)
    if not amounts and not config.get('allow_undisclosed_salary', False):
        return Decision('review', ['Salario nao divulgado: revisao antes de enviar'])
    return Decision('accepted')


COMPUTING = r"software|\bc#|\bunity\b|\bgodot\b|\bgdscript\b|\bpython\b|\bjava\b|\bjavascript\b|\btypescript\b|\bweb\b|\bsaas\b|computador|informatica|\bwindows\b|\blinux\b|banco de dados|database|test cases|casos de teste|\bbugs?\b|redes de computadores|\bapi\b|\bsql\b|aplicativ|sistemas? de informacao"
NON_TECH_TITLE = r"\bloja\b|\bcaixa\b|\bvendas?\b|comercial|sales|recrutamento|recruit|financeir|faturamento|billing|contabil|fiscal|laboratorio|eletrotecnic|eletromecanic|tecnico eletronico|marketing|pedagog|pos obra|legal assistant|executive assistant|virtual assistant|administrativ|business development|desenvolvimento de negocios|intake|ancord"
UNCONFIRMED = {
    r"\bnode(?:\.js|js)?\b": 'Node.js', r"\bjavascript\b": 'JavaScript', r"\btypescript\b": 'TypeScript',
    r"\breact\b": 'React', r"\bangular\b": 'Angular', r"\bvue\b": 'Vue',
    r"asp\.?net|(?<!\w)\.net\b": '.NET/ASP.NET fora de Unity',
    r"\bsql\b|\bsql server\b|\bpostgresql\b": 'SQL/bancos relacionais',
    r"\bsap\b|\babap\b": 'SAP/ABAP', r"\boracle\b|\bavalara\b": 'Oracle/Avalara',
    r"\blinux\b|\bbash\b|shell script": 'Administracao Linux/Shell',
    r"\baws\b|\bazure\b|\bkubernetes\b|\bdocker\b": 'Cloud/containers',
    r"\bselenium\b|\bcypress\b|\bplaywright\b": 'Automacao de testes',
    r"\bsalesforce\b|\bdynamics\b|\bprotheus\b|\btotvs\b": 'ERP/CRM especializado',
    r"\bdutch\b|\bholandes\b|\brussian\b|\brusso\b|\bgerman\b|\balemao\b|\bspanish\b|\bespanhol\b": 'Idioma profissional nao confirmado',
}


def requirements_text(description: str) -> str:
    lines, active, selected = description.splitlines(), False, []
    for raw in lines:
        line = norm(raw)
        if re.match(r"^(requirements|qualifications|requisitos|qualificacoes|o que esperamos|o que buscamos)\b", line):
            active = True
        elif re.match(r"^(benefits|beneficios|diferenciais|nice.to.have|desirable|sera considerado diferencial|responsibilities|responsabilidades|atividades|accountabilities)\b", line):
            active = False
        optional = re.search(r"desejavel|diferencial|nice.to.have|not required|nao .*obrigatorio|optional|considered a plus|preferred", line)
        explicit = re.search(r"obrigator|imprescindivel|required|must have|necessario|indispensavel|experiencia (?:solida|comprovada)|proficien", line)
        if not optional and (active or explicit):
            selected.append(line)
    return '\n'.join(selected)


def screen_job(job: Job, config: dict) -> Decision:
    """A score cannot override scope, salary or unconfirmed technical requirements."""
    if not eligible(job):
        return Decision('excluded', ['Fora de Curitiba presencial/hibrido ou Brasil remoto'])
    restricted = eligibility_review(job, config)
    if restricted:
        return Decision('review', [restricted])
    title, desc = norm(job.title), norm(job.description)
    if re.search(NON_TECH_TITLE, title):
        return Decision('excluded', ['Cargo fora de desenvolvimento, QA de software ou suporte de TI'])
    development = bool(re.search(r"desenvolvedor|programador|developer|software engineer|engenheir.*software|game programmer|\bdev\b|\bsdet\b", title))
    qa = bool(re.search(r"\bqa\b|quality assurance|testador|tester|analista de testes|engineer in test", title))
    support = bool(re.search(r"suporte|technical support|application support|help.?desk|service.?desk|it support|system administrator|sysadmin", title))
    if not (development or qa or support):
        return Decision('excluded', ['Cargo sem funcao de tecnologia compativel identificada'])
    if not re.search(COMPUTING, desc + ' ' + title):
        return Decision('review', ['Descricao nao confirma atividade de software/informatica; nao inferir pelo nome da empresa'])
    reasons = []
    # The user explicitly welcomes higher seniority and larger experience demands.
    # This changes selection only; it never changes the truthful form answers.
    if re.search(r"estagio|\bintern\b|internship|aprendiz|apprentice", title):
        return Decision('excluded', ['Cargo abaixo da senioridade minima: estagio/aprendiz'])
    required = requirements_text(job.description)
    for pattern, name in UNCONFIRMED.items():
        if re.search(pattern, title + '\n' + required):
            reasons.append('Requisito/stack nao confirmado no curriculo: ' + name)
    pay = salary_gate(job, config)
    if pay.status == 'excluded':
        return pay
    reasons.extend(pay.reasons)
    return Decision('review', sorted(set(reasons))) if reasons else Decision('accepted')
