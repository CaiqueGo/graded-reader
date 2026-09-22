# Graded Reader

Leitor de inglês graduado por nível, com flashcards próprios e painel de evolução.

O Graded Reader pega **qualquer** texto e o reescreve no nível certo, extrai o vocabulário
que vale a pena aprender, e acompanha o que foi realmente aprendido ao longo do
tempo.

Todo o trabalho determinístico é do app — validação, lematização, medição de
cobertura, agendamento, estatística. A reescrita é do Claude Code, pelo binário
que você já tem instalado, e você pode acioná-la pela web ou pelo terminal.

## Por onde começar

- [`docs/como-funciona.md`](docs/como-funciona.md) — **como o sistema funciona hoje**:
  cada tela, o que é o glossário, como a fila do review decide o que mostrar, e
  o que cada número do painel afirma. Comece por aqui para usar.
- [`docs/graded-reader-mvp.md`](docs/graded-reader-mvp.md) — a especificação completa: o problema,
  a tese do produto, o contrato de importação, o modelo de dados e as etapas.

## Estado

| Etapa | O que entrega | Status |
|---|---|---|
| M0 | Léxico: bandas, lematização, cobertura, `reader analyze` | pronto |
| M1 | O laço fechado via terminal: banco, `profile`, importador | pronto |
| M2 | Leitura na web: ler, clicar palavra, salvar no baralho | pronto |
| M3 | Revisão: fila do dia, teclado, desfazer, limite diário | pronto |
| M4 | Painel: números, escada de níveis, histórico, retenção | pronto |
| M5 | Exportação para o Anki | pronto |
| — | Adaptar pela web (antecipa o `ApiAdapter` do §14) | pronto |

## Como rodar

```
uv venv --python 3.11
uv pip install -e ".[lexicon,db,srs,web,adapt,dev]"
python -m spacy download en_core_web_sm
```

O caminho normal é `reader serve` e o botão **Add a text** — cola um endereço ou
o artigo, escolhe o nível, e pronto. O mesmo laço pelo terminal, quando quiser
controle:

```
/adapt A1 materia.txt
```

Ele roda `reader profile`, adapta o texto, grava o JSON em `inbox/`, roda
`reader import` e reporta a cobertura. Os comandos por trás dos dois:

| Comando | O que faz |
|---|---|
| `reader profile --level A1` | Imprime o bloco de contexto para o prompt de adaptação |
| `reader import` | Valida, mede e grava o que está no `inbox/` |
| `reader texts` | Lista o que já entrou |
| `reader analyze arq.txt --level A1` | Mede um texto solto, sem gravar nada |
| `reader serve` | Sobe a interface de leitura em http://127.0.0.1:8000 |
| `reader export` | Escreve o baralho como CSV para o Anki |

`analyze` sai com código 1 quando o texto não alcança o limiar — é isso que permite
ao laço saber que precisa tentar de novo. Aceita `-` para ler da entrada padrão e
`--known arquivo.txt` (um lema por linha).

## Como funciona

O passo a passo de cada tela está em
[`docs/como-funciona.md`](docs/como-funciona.md) — o que é o glossário, como a
fila do review decide o que mostrar, e o que cada número do painel afirma. Em
uma frase cada:

- **Biblioteca** — *Add a text* recebe um endereço ou o artigo colado e adapta
  sozinho; *Import waiting texts* e *Or paste the document* são as portas sem
  terminal para o `inbox/`.
- **Leitura** — clicar numa palavra cria um cartão de palavra, selecionar um
  trecho cria um cartão de frase. O **glossário** embaixo do texto é o
  vocabulário que a adaptação escolheu ensinar, e *Save all* manda tudo de uma
  vez para o baralho sem reiniciar o agendamento de quem já estava lá.
- **Review** — a fila do dia, movida pelo teclado: **espaço** revela, **1–4**
  avaliam, **u** desfaz. O limite diário de cartões novos (padrão 10) conta
  primeiras aparições, não avaliações.
- **Painel** — a escada de níveis, o histórico, a retenção, e a exportação para
  o Anki.

## Decisões que parecem detalhe

A adaptação pela web é o `Adapter` do §14 com uma segunda implementação, o
`ClaudeCliAdapter`, ao lado do `InboxAdapter`. O §3 do MVP punha isso na v2, e o
§13 diz que a fricção do modo manual é o sinal de que a v2 vale a pena — o sinal
veio cedo.

**Não é a API paga.** O adaptador chama o binário `claude` que já está instalado
e logado na sua máquina, então gasta o mesmo plano que digitar o comando no
terminal. O `total_cost_usd` que o CLI reporta é o equivalente em API, não uma
cobrança.

- **O processo filho roda sem ferramenta nenhuma** (`--tools ""`). O artigo que
  você manda adaptar é texto não confiável; um agente com `Write` e `Bash` lendo
  uma página hostil é um risco diferente de um modelo sem mãos. De quebra,
  consome ~3× menos da sua janela, e contorna um defeito real: rodar o `/adapt`
  por `claude -p` **com** ferramentas falha de forma reproduzível no CLI 2.0.76
  (`API Error 400: tool_use ids must be unique`).
- **O prompt vai por stdin.** Como argumento ele é truncado em silêncio no limite
  de linha de comando do Windows — o processo sai com código 0 e não imprime
  nada.
- **A resposta é desembrulhada antes de virar JSON.** O modelo cerca o JSON em
  ```` ```json ```` por mais que se peça o contrário. O `--json-schema` do CLI
  seria a solução certa e hoje devolve 400.

O `/adapt` continua existindo como caminho manual, e é o mesmo bloco de perfil nos
dois — se divergirem, as adaptações ficariam sutilmente piores por uma das portas
e nada avisaria.

Os arquivos estáticos são servidos com a impressão digital do conteúdo na URL
(`app.css?v=fb6014d5`). Sem isso, o navegador guarda a folha de estilo antiga e
uma mudança de CSS chega como tela quebrada — que foi exatamente o que
aconteceu, e é um bug que se parece com CSS errado sendo cache velho.

A **exportação para o Anki** sai por `reader export` ou pelo botão no painel, e
os dois produzem o mesmo arquivo byte a byte. Detalhes que decidem entre
importar direto e ter que mexer no diálogo:

- **`#tags column:3`.** Sem essa linha o Anki lê a terceira coluna como um
  *campo*, não como tags — e num tipo de nota de dois campos ela some.
- **O escape vem antes da marcação, nunca depois.** Com `#html:true` o Anki lê
  cada campo como HTML, então um `&` na tradução tem que virar `&amp;` — mas o
  `<br>` e o `<i>` que o app insere precisam sobreviver literais. Invertido, a
  formatação aparece como sinais de maior e menor em cada cartão.
- **`#deck` e `#notetype` só pré-selecionam "se existirem"**, diz o manual. O
  nome do deck vai; o do tipo de nota não, porque o padrão se chama diferente em
  cada idioma do Anki (`--notetype` se você quiser).
- A primeira coluna é a palavra, e o Anki usa o primeiro campo como identidade
  da nota — então reexportar **atualiza** as mesmas notas em vez de dobrar o
  baralho.

Referência: [Text Files, no manual do
Anki](https://docs.ankiweb.net/importing/text-files.html).

## Limites conhecidos

`reader serve` escuta só em localhost, e deve continuar assim: **não há
autenticação nenhuma neste app**, por decisão do MVP. Qualquer um que alcance a
porta lê e altera o baralho.

O app busca qualquer endereço http(s) que você digitar, inclusive de rede local.
Como só você digita, e o app só escuta em localhost, isso é aceitável aqui — mas
é uma porta que não existe se um dia houver um segundo usuário.

`import` nunca apaga a sua entrada: arquivo inválido vai para `inbox/rejected/`
com um `.error.txt` ao lado dizendo o motivo, e reimportar o mesmo texto não
duplica (a identidade é o hash do inglês adaptado).

Os portões, na ordem em que valem:

```
ruff format . && ruff check . && mypy && pytest
```

## Calibragem

`data/bands.toml` guarda os cortes que transformam frequência em nível CEFR. Eles
são **chutes calibrados, não verdade**: a NGSL é uma lista de frequência geral, não
um mapa CEFR oficial. Depois de uns 20 textos, compare a sensação de dificuldade
com a cobertura calculada e ajuste o arquivo — sem mexer em código.

Isso já aconteceu uma vez, e vale como aviso. A NGSL lematiza `their`→`they`,
`these`→`this`, `his`→`he`, `an`→`a`; o spaCy não. Treze das palavras mais comuns
do inglês não batiam com entrada nenhuma, caíam na escala do Zipf — que só tinha
degrau até B2 — e eram classificadas como **B2**. Todo texto com "my" ou "your"
perdia cobertura por isso. O conserto foram quatro linhas em `data/bands.toml`,
com os pisos tirados da mediana de Zipf de cada faixa da NGSL (A1 5,43, A2 4,96,
B1 4,59), **sem uma linha de código**. Os testes em `tests/test_calibration.py`
travam isso contra os dados reais.

Outro aviso, de uso: nível de gramática e nível de vocabulário são coisas
diferentes, e a cobertura só mede o segundo. O texto `exemplo/bees-adaptado.txt`
foi escrito com gramática A1 — frases de 6 a 10 palavras, sem subordinação — e
mede 74,7% em A1, 90,6% em B1 e 100% em B2. O assunto é que carrega o
vocabulário: `bee` sozinho é 6,1% do texto e é B2. Um tema não vira A1 só porque
as frases encurtaram.

## Dados

O diretório de dados é `data/`, e `GRADED_READER_DATA_DIR` sobrescreve — é o que permite
aos testes rodarem contra um diretório temporário sem tocar nas listas reais.

- `data/ngsl.csv` — **New General Service List** (2.809 palavras), de
  [newgeneralservicelist.com](https://www.newgeneralservicelist.com/new-general-service-list),
  por Browne, Culligan e Phillips. Licença **CC BY-SA 4.0**. O arquivo publicado
  é o `NGSL_12_stats.csv`, guardado aqui sem alteração.
- `data/bands.toml` — os cortes de banda e os limiares de cobertura.
- `data/levels.toml` — o orçamento gramatical de cada nível, citado no prompt.

O banco é um arquivo SQLite (`graded-reader.db`, sobrescrevível por `GRADED_READER_DB`) e o
`inbox/` por `GRADED_READER_INBOX_DIR`. Nenhum dos dois vai para o git.

Frequência fora da NGSL vem do [wordfreq](https://pypi.org/project/wordfreq/).

## Licenças

O código está sob Apache 2.0 ([`LICENSE`](LICENSE)). Dois arquivos dentro do
repositório **não** são deste projeto e têm termos próprios — a lista NGSL em
`data/` (CC BY-SA 4.0, que é *share-alike* e não é a licença do código) e o htmx
em `src/graded_reader/web/static/` (0BSD). Os dois estão detalhados no
[`NOTICE`](NOTICE).
