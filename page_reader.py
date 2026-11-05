"""Read public job fields from both LinkedIn layouts without company-link assumptions."""

# The new layout renders titles as paragraphs and sometimes omits the company link.
# Do not depend on impression-only data-view-name attributes or generated CSS classes.
JOB_READY_JS = r"""() => {
  const text = document.body?.innerText || '';
  if (/nenhuma vaga|vaga não (está mais )?disponível|no longer available|page not found|página não encontrada/i.test(text.slice(0,2500))) return true;
  if (document.querySelector('#job-details, .jobs-description__content, .jobs-description-content__text')) return true;
  return [...document.querySelectorAll('h2,h3')].some(el => /^(Sobre a vaga|About the job)$/i.test(el.innerText.trim()));
}"""

JOB_SNAPSHOT_JS = r"""applySelector => {
  const clean = text => (text || '').replace(/\s+/g,' ').trim();
  const prefix = (document.body?.innerText || '').split(/Sobre a vaga|About the job/i)[0].slice(0,3500);
  const pieces = document.title.replace(/\s*\|\s*LinkedIn\s*$/i,'').split(/\s+\|\s+/);
  let company = pieces.length >= 2 ? clean(pieces.pop()) : '';
  let title = pieces.length && company ? clean(pieces.join(' | ')) : '';
  const h1 = document.querySelector('h1');
  if (h1) title = clean(h1.innerText);
  let titleNode = title ? [...document.querySelectorAll('h1,p,a')].find(el=>clean(el.innerText)===title) : null;
  const apply = document.querySelector(applySelector);
  let top = null;
  for (let el=(apply || titleNode); el && el!==document.body; el=el.parentElement) {
    const paras=[...el.querySelectorAll('p')].map(p=>clean(p.innerText)).filter(Boolean);
    const link=el.querySelector('a[href*="/company/"]');
    if (!title && link && paras.length >= 3) {
      company=clean(link.innerText);title=paras.find(text=>text!==company)||'';
      titleNode=[...el.querySelectorAll('p,h1')].find(p=>clean(p.innerText)===title);
    }
    if (titleNode && el.contains(titleNode) && paras.some(text=>text.includes('·'))) {top=el;break;}
  }
  const paras=top ? [...top.querySelectorAll('p')].map(p=>clean(p.innerText)).filter(Boolean) : [];
  const titleIndex=paras.indexOf(title);
  if (!company && titleIndex>0) company=paras[titleIndex-1];
  const location=(paras.slice(Math.max(0,titleIndex+1)).find(text=>text.includes('·'))||'').split('·')[0].trim();
  const modePattern=/^(Remoto|Remote|Híbrido|Hybrid|Presencial|On.?site)$/i;
  const modes=top ? [...top.querySelectorAll('a,button')].map(el=>clean(el.innerText)).filter(text=>modePattern.test(text)) : [];
  let description='';
  const about=[...document.querySelectorAll('h2,h3')].find(el=>/^(Sobre a vaga|About the job)$/i.test(clean(el.innerText)));
  if (about) {
    for (let el=about.parentElement, n=0; el && el!==document.body && n<4; el=el.parentElement,n++) {
      const content=el.querySelector('[data-testid="expandable-text-box"]');
      if(content) {description=content.innerText.trim();break;}
    }
  }
  return {title,company,location,workplace:modes.join(' '),description,pay:top?.innerText||'',prefix,
    unavailable:!apply && /não (se )?aceita(m)? mais candidaturas|no longer accepting applications|vaga não (está mais )?disponível|no longer available|page not found|página não encontrada/i.test(prefix)};
}"""
