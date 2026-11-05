# JobPilot BR

Assistente local em Python para buscar, classificar e candidatar-se a vagas com **Candidatura simplificada** no LinkedIn. Aceita vagas presenciais/híbridas em Curitiba e remotas no Brasil. Inclui buscas de desenvolvimento, QA, suporte e funções de entrada mais amplas.

**Estado:** versão com recuperação de falhas por anúncio. Em 5 de novembro de 2026, os quatro anúncios envolvidos na falha do agendamento foram lidos corretamente após a correção. Há 53 testes de regras, formulários e recuperação de falhas. O funcionamento continua dependendo da sessão e do layout atual da conta; não há garantia de envio em todo formulário nem de atingir a cota diária.

O LinkedIn [proíbe automação por terceiros](https://www.linkedin.com/help/linkedin/answer/a1341543) e pode restringir contas. [Há limites diários e por velocidade](https://www.linkedin.com/help/linkedin/answer/a8068422), sem número universal publicado. A rotina para diante de limite, CAPTCHA ou verificação de segurança; não usa stealth, proxies, rotação de contas, resolução de CAPTCHA ou tentativa de contornar bloqueios.

## Uso no computador já configurado

1. Abra `Entrar-LinkedIn.cmd` e faça o login pessoalmente na janela do Edge. O programa não solicita nem registra sua senha. O perfil separado do navegador mantém a sessão em `data/browser-profile/`; mantenha essa pasta privada.
2. Abra `Verificar-Vagas.cmd` para buscar e ordenar sem enviar candidaturas. Consulte `data/ranked-jobs.json` e `data/last-run.json`.
3. Abra `Candidatar.cmd` para enviar as candidaturas que o programa conseguir preencher apenas com dados confirmados.

A configuração pessoal fica em `config.local.json`, ignorada pelo Git. O agendamento diário, quando ativado pelo Codex, executa `Executar.ps1 -Mode run` às **17h de America/Sao_Paulo**. O computador precisa estar ligado, conectado à internet e com o aplicativo Codex aberto. A rotina não roda no GitHub Actions e não depende de plugin do GitHub ou API paga.

## Instalação a partir do GitHub

Requer Windows, Python 3.12+ e Microsoft Edge instalado. No PowerShell, dentro da pasta do projeto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.local.json
New-Item -ItemType Directory -Force resumes
```

Coloque seus dois PDFs em `resumes/` e edite `config.local.json`. Verifique os caminhos dos PDFs e seus contatos antes do primeiro envio.

```powershell
.\.venv\Scripts\python.exe jobpilot.py check
.\.venv\Scripts\python.exe jobpilot.py login
.\.venv\Scripts\python.exe jobpilot.py plan
.\.venv\Scripts\python.exe jobpilot.py run
```

Para uma verificação curta sem envio, use `jobpilot.py plan --max-candidates 8 --minutes 5`. Para examinar uma vaga específica, acrescente `--job-id NUMERO`; os filtros de região/modalidade e o histórico continuam obrigatórios.

## Como escolhe as vagas

As buscas usam os filtros do LinkedIn para candidatura simplificada, últimos sete dias e região/modalidade. A rotina confere também a localização e a modalidade visíveis na vaga. Localização ou modalidade sem confirmação ficam de fora.

A ordenação usa regras transparentes: compatibilidade com C#/Unity/Godot, desenvolvimento, QA e suporte; preferência por vagas júnior; penalidade para cargos sênior; bônus limitado para salário mensal em reais publicado e menções a mentoria/treinamento. Faixas múltiplas que incluem explicitamente júnior, como “Júnior/Pleno”, mantêm a prioridade de entrada. Os bônus são heurísticas, não estimativas salariais nem garantias de crescimento. Salários ausentes, anuais ou por hora não são comparados. Não consulta salários externos.

O currículo em inglês é escolhido quando a descrição contém predominância de termos ingleses; nos demais casos usa o português. Ambos devem estar atualizados. O currículo nunca é reescrito e suas qualificações nunca são inventadas.

## Formulários e limites

- Preenche somente contatos inequívocos ou perguntas com resposta exata cadastrada em `answers`.
- Vagas explicitamente afirmativas ou para PCD exigem revisão antes de abrir a candidatura; o programa não infere deficiência nem pertencimento a grupos. Depois de confirmar pessoalmente a elegibilidade de uma vaga, seu número pode ser incluído em `reviewed_eligibility_job_ids`. Essa lista não altera os filtros de região/modalidade nem o histórico.
- Perguntas obrigatórias desconhecidas ou respostas desconhecidas pré-preenchidas pelo site ficam em `pending` no relatório. Campos opcionais desconhecidos e vazios permanecem em branco. Não presume autorização de trabalho, tempo de experiência, formação concluída, disponibilidade, pretensão salarial ou dados demográficos. Checkboxes e radios ficam para revisão pessoal.
- Para adicionar uma resposta textual ou de seleção, copie o rótulo exato para `answers` em `config.local.json` e informe o valor verdadeiro. Por exemplo: `"Código do país": "Brasil (+55)"`, se essa for a opção exibida. Não use respostas genéricas por palavra-chave.
- `salary_expectations` guarda as pretensões mensais em reais para `clt` e `pj`. Só preenche perguntas sobre expectativa/pretensão que identifiquem um único regime; não usa esses valores para salário anterior, valor anual, por hora ou regime ambíguo. Uma resposta exata em `answers` tem precedência.
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

`data/last-run.json` mostra o resultado mais recente; `data/reports/` conserva cada execução; `data/applications.sqlite3` conserva os envios e tentativas incertas para evitar duplicatas.

O relatório separa `discovered` (anúncios coletados), `scanned` (anúncios examinados), `candidates` (elegíveis), `excluded` (fora da região/modalidade), `skipped_registered` (já registrados) e `submitted` (envios confirmados). `read_issues` e `search_issues` são diagnósticos de anúncios/pesquisas; `pending` contém perguntas ou formulários para revisão. `status: partial` indica conclusão com itens ignorados ou pendências, sem equivaler automaticamente a uma falha global.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes de navegador usam o Edge em páginas HTML simuladas, sem acessar o LinkedIn. Verificam upload, preenchimento de contatos, perguntas desconhecidas, consentimentos, confirmação de envio e cota antes do clique. Os testes de regras cobrem região, modalidade, salário, idioma e histórico persistente.

## Pausar a rotina

Pause a automação na área de agendamentos do Codex. Para remover o login local sem apagar o histórico, use o próprio Edge desse perfil para sair do LinkedIn. Feche esse navegador antes da próxima execução.
