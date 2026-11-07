# JobPilot BR

Assistente local em Python para buscar, classificar e candidatar-se a vagas com **Candidatura simplificada** no LinkedIn. O foco é desenvolvimento de software/jogos, QA de software e suporte de TI: presencial/híbrido em Curitiba ou remoto no Brasil. Vagas genéricas de loja, caixa, vendas, financeiro, administração e qualidade industrial ficam de fora, inclusive quando a empresa atua com tecnologia.

**Estado:** em 6 de novembro de 2026, a seleção foi corrigida após envios fora do foco. As regras de área e salário agora bloqueiam candidaturas, em vez de apenas alterar a ordenação. Há testes de regressão para loja, caixa, salário abaixo do piso, benefícios/comissões, releitura antes de envio e permissão de cargos de maior senioridade. O funcionamento depende da sessão e do layout atual da conta; não há garantia de envio em todo formulário nem de atingir a cota diária.

O LinkedIn [proíbe automação por terceiros](https://www.linkedin.com/help/linkedin/answer/a1341543) e pode restringir contas. [Há limites diários e por velocidade](https://www.linkedin.com/help/linkedin/answer/a8068422), sem número universal publicado. A rotina para diante de limite, CAPTCHA ou verificação de segurança; não usa stealth, proxies, rotação de contas, resolução de CAPTCHA ou tentativa de contornar bloqueios.

## Uso no computador já configurado

1. Abra `Entrar-LinkedIn.cmd` e faça o login pessoalmente na janela do Edge. O programa não solicita nem registra sua senha. O perfil separado do navegador mantém a sessão em `data/browser-profile/`; mantenha essa pasta privada.
2. Abra `Verificar-Vagas.cmd` para buscar e ordenar sem enviar candidaturas. Consulte `data/ranked-jobs.json` e `data/last-run.json`.
3. Abra `Candidatar.cmd` para enviar as candidaturas que o programa conseguir preencher apenas com dados confirmados.

A configuração pessoal fica em `config.local.json`, ignorada pelo Git. O agendamento diário, quando ativado pelo Codex, executa `Executar.ps1 -Mode run` às **17h de America/Sao_Paulo**. O computador precisa estar ligado, conectado à internet e com o aplicativo Codex aberto. A rotina não roda no GitHub Actions e não depende de plugin do GitHub ou API paga.

## Instalação a partir do GitHub

Requer Windows, Python 3.12+ e Microsoft Edge instalado. No PowerShell, dentro da pasta do projeto:

Use uma instalação independente do Python registrada no Windows (`py -0p`). Não crie o ambiente com o Python interno de uma versão do Codex: uma atualização do aplicativo pode remover o caminho-base e inutilizar o ambiente. Em 07/11/2026, o ambiente local foi recriado com uma instalação do usuário, preservando a configuração, os currículos, a sessão e o histórico. Os 71 testes passaram com Python 3.14.8 e Playwright 1.64.1.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.local.json
New-Item -ItemType Directory -Force resumes
```

Coloque seus dois PDFs em `resumes/` e edite `config.local.json`. Defina seus dois pisos mensais positivos em `minimum_monthly_salary` e escolha explicitamente se aceita salário não divulgado. Verifique os caminhos dos PDFs e seus contatos antes do primeiro envio. A configuração de exemplo não permite envio enquanto os pisos estiverem nulos.

```powershell
.\.venv\Scripts\python.exe jobpilot.py check
.\.venv\Scripts\python.exe jobpilot.py login
.\.venv\Scripts\python.exe jobpilot.py plan
.\.venv\Scripts\python.exe jobpilot.py run
```

Para uma verificação curta sem envio, use `jobpilot.py plan --max-candidates 8 --minutes 5`. Para examinar uma vaga específica, acrescente `--job-id NUMERO`; os filtros de região/modalidade e o histórico continuam obrigatórios.

## Como escolhe as vagas

As buscas usam os filtros do LinkedIn para candidatura simplificada, últimos sete dias e região/modalidade. A rotina confere também a localização e a modalidade visíveis na vaga. Localização ou modalidade sem confirmação ficam de fora.

Antes de ordenar, `policy.py` confere função, região, salário e requisitos técnicos ainda não confirmados. A função deve ser de software, QA de software ou suporte de informática; palavras como “support”, “QA” ou o nome de uma empresa de tecnologia não bastam. Requisitos técnicos especializados fora dos conhecimentos confirmados ficam para revisão. Estágios e aprendiz são bloqueados; vagas pleno, sênior e acima, inclusive exigências maiores de tempo de experiência, são permitidas sem inventar respostas sobre experiência.

`minimum_monthly_salary` define os pisos individuais de salário fixo mensal em reais para CLT e PJ, na configuração privada. Faixas são comparadas pelo menor valor. Benefícios, comissões e bônus não elevam o salário fixo. Valores explícitos como “Salário R$ 2.050,00 + VT” são lidos mesmo sem a palavra “mensal”. Remuneração por hora/ano, moeda estrangeira, teto sem piso ou regime ambíguo entre os dois pisos exige revisão. Não se faz conversão nem estimativa. `allow_undisclosed_salary: true` permite salário não divulgado, por escolha explícita do usuário, desde que as demais regras sejam atendidas.

A ordenação dos anúncios aprovados usa compatibilidade com C#/Unity/Godot, desenvolvimento, QA e suporte; bônus para entrada/júnior, remuneração mensal publicada e menções a mentoria/treinamento. Não há penalidade por senioridade superior. Os bônus são heurísticas, não estimativas salariais nem garantias de crescimento. Uma pontuação alta nunca supera os bloqueios. Os filtros são repetidos após a releitura da vaga e antes da reserva de envio.

O currículo em inglês é escolhido quando a descrição contém predominância de termos ingleses; nos demais casos usa o português. Ambos devem estar atualizados. O currículo nunca é reescrito e suas qualificações nunca são inventadas.

## Formulários e limites

- Preenche contatos inequívocos, respostas exatas cadastradas em `answers` e perguntas identificadas sobre pretensão salarial, emprego atual ou disponibilidade, usando os fatos confirmados descritos abaixo.
- Vagas explicitamente afirmativas ou para PCD exigem revisão antes de abrir a candidatura; o programa não infere deficiência nem pertencimento a grupos. Depois de confirmar pessoalmente a elegibilidade de uma vaga, seu número pode ser incluído em `reviewed_eligibility_job_ids`. Essa lista não altera os filtros de região/modalidade nem o histórico.
- Perguntas obrigatórias desconhecidas ou respostas desconhecidas pré-preenchidas pelo site ficam em `pending` no relatório. Campos opcionais desconhecidos e vazios permanecem em branco. Não presume autorização de trabalho, tempo de experiência, formação concluída, disponibilidade, pretensão salarial ou dados demográficos. Checkboxes e radios desconhecidos ficam para revisão pessoal.
- Para adicionar uma resposta textual ou de seleção, copie o rótulo exato para `answers` em `config.local.json` e informe o valor verdadeiro. Por exemplo: `"Código do país": "Brasil (+55)"`, se essa for a opção exibida. Não use respostas genéricas por palavra-chave.
- `salary_expectations` guarda as pretensões mensais em reais para `clt`, `pj` e, opcionalmente, `unspecified`. Esse último valor só é usado quando o usuário o forneceu explicitamente e a pergunta não distingue os regimes. Ele não altera `minimum_monthly_salary`, não reduz o piso PJ e não é usado para salário anterior/atual, valor anual/semanal/por hora, líquido, benefícios ou valores por tarefa/projeto. Uma resposta exata em `answers` tem precedência.
- `employment.currently_employed: false` permite responder perguntas identificadas sobre empresa atual com “Não trabalho atualmente” / “Not currently employed”, sem citar antigos empregadores como atuais. Perguntas sobre último emprego e salário anterior continuam independentes desse dado.
- `employment.relatives_or_close_contacts_at_employer: false` só deve ser configurado quando o usuário declarar essa resposta para todas as empresas. Permite responder “Não” a perguntas identificadas sobre parentes ou conhecidos que trabalham na empresa; não responde perguntas sobre nomes, outros vínculos ou elegibilidade.
- `consent` registra autorizações prévias para aceitar termos de candidatura, tratamento de dados pessoais e contato sobre oportunidades de emprego. Cada opção começa desativada no exemplo e só deve ser ativada com autorização explícita. Nos consentimentos identificados, marca checkboxes ou a alternativa afirmativa de seleções/radios, sem perguntar novamente. Não transforma declarações pessoais ou contratos de trabalho em consentimentos. O leitor reconhece também os parágrafos adjacentes aos checkboxes com rótulo vazio no layout atual.
- `availability` pode registrar horas por dia, dias da semana e modalidades de trabalho confirmados. Perguntas identificadas de jornada diária/semanal, período integral e disponibilidade recebem somente esses fatos; não se presume início imediato nem um horário de entrada/saída. Com oito horas de segunda a sexta, a jornada semanal corresponde a 40 horas. Modalidades permitidas não ampliam os limites geográficos.
- Exige upload identificado como currículo antes do envio. Na interface atual, usa o botão de upload e confirma que o PDF ficou selecionado. Em candidaturas seguintes, reutiliza apenas o mesmo nome de arquivo cujo conteúdo foi previamente identificado por SHA-256 no registro local. Pedidos de outros documentos ficam para revisão.
- `max_per_day: null` significa **sem cota artificial local**. Pode definir um inteiro para limitar os envios. O limite real continua sendo o do LinkedIn.
- `max_candidates`, `max_minutes` e `pages_per_search` limitam o trabalho de uma execução, não representam a cota do LinkedIn. Portanto a execução pode terminar antes de gastar a cota.
- Um limite ou alerta de segurança pausa o restante do dia UTC. O script não repete tentativas naquela data.
- Antes de clicar em enviar, registra a tentativa no SQLite. Se a janela de sucesso não aparecer, faz uma única consulta de leitura à página da vaga e aceita como confirmação somente o cartão de status da candidatura, separado da descrição. Se isso também não confirmar o envio, mantém o resultado incerto e encerra. Essa vaga não será reenviada automaticamente. Verifique a aba de candidaturas do LinkedIn antes de qualquer correção manual no histórico.
- Uma trava de arquivo impede duas execuções simultâneas com o mesmo perfil.

## Recuperação de falhas

O leitor suporta os títulos apresentados como parágrafos na interface atual, inclusive quando a empresa não tem link e quando a ação de candidatura é um link em vez de um botão. A descrição é obtida separadamente do cabeçalho. Modalidade ausente exige revisão; não é tratada como vaga fora da região. Alterações de localização na segunda leitura são registradas individualmente no relatório.

Os formulários também carregam de forma assíncrona. O programa aguarda os controles da janela e a mudança real de etapa antes de preencher a próxima página, evitando interpretar a tela de carregamento como um formulário desconhecido ou clicar duas vezes em avançar.

Uma navegação de leitura que falhar recebe no máximo uma nova tentativa. Anúncios removidos, campos ausentes, pesquisas incompletas e falhas de formulário anteriores à reserva de envio são registrados por item; as outras vagas continuam. Essa recuperação nunca repete um clique de envio. Login expirado, limite da plataforma, verificação de segurança e resultado incerto de envio continuam interrompendo a execução.

O tempo da execução é dividido entre busca, leitura e candidaturas, para que anúncios lentos não consumam toda a janela antes dos envios. O relatório e a lista ordenada são atualizados durante a leitura; os candidatos já encontrados permanecem registrados se uma etapa posterior precisar parar.

## Privacidade e relatórios

`config.local.json`, `data/`, `resumes/`, PDFs, logs e `.venv/` são ignorados pelo Git. Não publique esses arquivos. Os relatórios e o banco ficam somente no computador e podem conter perguntas de recrutadores e dados de candidaturas. Não há telemetria ou serviço de IA externo neste programa.

`data/last-run.json` mostra o resultado mais recente; `data/reports/` conserva cada execução; `data/applications.sqlite3` conserva os envios e tentativas incertas para evitar duplicatas. Novas leituras completas são preservadas em `data/job-snapshots/` para permitir auditoria futura. As execuções antigas não tinham esse arquivo por anúncio; ausência de descrição antiga não comprova salário nem compatibilidade.

O relatório separa `discovered` (anúncios coletados), `scanned` (examinados), `candidates` (aprovados pela política para candidatura), `excluded` (reprovados por escopo/salário), `skipped_registered` (já registrados) e `submitted` (envios confirmados). `policy_exclusions` explica os bloqueios; `policy_reviews` explica os anúncios ainda dependentes de revisão. `read_issues` e `search_issues` são diagnósticos por item; `pending` contém perguntas/formulários. `status: partial` indica conclusão com itens ignorados ou pendências, sem equivaler automaticamente a falha global. Relatórios anteriores a 06/11 usavam `candidates` somente para região/modalidade e não devem ser interpretados como prova de compatibilidade.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes de navegador usam o Edge em páginas HTML simuladas, sem acessar o LinkedIn. Verificam upload, preenchimento de contatos, perguntas desconhecidas, consentimentos, confirmação de envio e cota antes do clique. Os testes de regras cobrem região, modalidade, salário, idioma e histórico persistente.

## Pausar a rotina

Pause a automação na área de agendamentos do Codex. Para remover o login local sem apagar o histórico, use o próprio Edge desse perfil para sair do LinkedIn. Feche esse navegador antes da próxima execução.
