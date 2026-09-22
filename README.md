# Degrau

Leitor de inglês graduado por nível, com flashcards próprios e painel de evolução.

O Degrau pega **qualquer** texto e o reescreve no nível certo, extrai o vocabulário
que vale a pena aprender, e acompanha o que foi realmente aprendido ao longo do
tempo.

Todo o trabalho determinístico é do app — validação, lematização, medição de
cobertura, agendamento, estatística. A reescrita é do Claude Code, pelo binário
que você já tem instalado, e você pode acioná-la pela web ou pelo terminal.

## Por onde começar

- [`docs/degrau-mvp.md`](docs/degrau-mvp.md) — a especificação completa: o problema,
  a tese do produto, o contrato de importação, o modelo de dados e as etapas.

## Estado

| Etapa | O que entrega | Status |
|---|---|---|
| M0 | Léxico: bandas, lematização, cobertura, `degrau analyze` | pronto |
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

O caminho normal é `degrau serve` e o botão **Add a text** — cola um endereço ou
o artigo, escolhe o nível, e pronto. O mesmo laço pelo terminal, quando quiser
controle:

```
/adapt A1 materia.txt
```

Ele roda `degrau profile`, adapta o texto, grava o JSON em `inbox/`, roda
`degrau import` e reporta a cobertura. Os comandos por trás dos dois:

| Comando | O que faz |
|---|---|
| `degrau profile --level A1` | Imprime o bloco de contexto para o prompt de adaptação |
| `degrau import` | Valida, mede e grava o que está no `inbox/` |
| `degrau texts` | Lista o que já entrou |
| `degrau analyze arq.txt --level A1` | Mede um texto solto, sem gravar nada |
| `degrau serve` | Sobe a interface de leitura em http://127.0.0.1:8000 |
| `degrau export` | Escreve o baralho como CSV para o Anki |

`analyze` sai com código 1 quando o texto não alcança o limiar — é isso que permite
ao laço saber que precisa tentar de novo. Aceita `-` para ler da entrada padrão e
`--known arquivo.txt` (um lema por linha).

A revisão (`/review`) é movida pelo teclado, e isso não é enfeite: **espaço**
revela, **1–4** avaliam (Again, Hard, Good, Easy), **u** desfaz a última nota. Os
intervalos nos botões vêm marcados com `~` de propósito — o FSRS embaralha os
intervalos para que oito palavras salvas do mesmo texto não voltem todas no mesmo
dia para sempre, então o número exibido é a ordem de grandeza, não a promessa.

Desfazer restaura o cartão a partir de um retrato guardado na hora da nota, e
apaga a linha da revisão. Um clique errado não é história, e deixá-lo lá sujaria a
curva de retenção e a contagem do dia.

O limite diário de cartões novos (padrão 10, em `setting.daily_new_cards`) conta
**primeiras aparições**, não avaliações: um cartão revisto quatro vezes hoje
gastou uma vaga, não quatro.

Em *Add a text*, você cola um endereço ou o texto e o app adapta sozinho. O §3 do
MVP punha isso na v2, e o §13 diz que a fricção do modo manual é o sinal de que a
v2 vale a pena — o sinal veio cedo. É o `Adapter` do §14 com uma segunda
implementação, o `ClaudeCliAdapter`, ao lado do `InboxAdapter`.

**Não é a API paga.** O adaptador chama o binário `claude` que já está instalado
e logado na sua máquina, então gasta o mesmo plano que digitar o comando no
terminal. O `total_cost_usd` que o CLI reporta é o equivalente em API, não uma
cobrança.

Três decisões que parecem detalhe e não são:

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

**Limite conhecido:** o app busca qualquer endereço http(s) que você digitar,
inclusive de rede local. Como só você digita, e o app só escuta em localhost,
isso é aceitável aqui — mas é uma porta que não existe se um dia houver um
segundo usuário.

Nada disso exige terminal. Na **Biblioteca**, *Import waiting texts* faz o mesmo
que `degrau import`, e *Paste a document* aceita o JSON colado direto no
navegador — ele é gravado no `inbox/` antes de ser lido, então um documento
inválido acaba em `inbox/rejected/` com o motivo ao lado, em vez de sumir quando
a página troca. Texto abaixo do limiar entra assim mesmo, marcado **out of
level**: o §6 manda mostrar o número e deixar você decidir.

No **Review** dá para adicionar palavra direto ao baralho, sem passar por texto
nenhum, e corrigir o cartão na hora em que ele aparece — inclusive **o lema**. O
§13 aponta a lematização como o ponto fraco conhecido (`give up` ≠ `give`), e
poder consertar a forma base é a mitigação que ele pede. Corrigir o lema não
mexe no agendamento: é o mesmo cartão. Renomear para um lema que já existe é
recusado, porque fundir dois históricos é decisão sua, não do app.

A leitura é onde o baralho nasce: abra um texto, clique numa palavra e salve. As
palavras já salvas aparecem destacadas, inclusive nas formas flexionadas — salvar
`machine` destaca `machines`. O texto original fica a um clique, na aba ao lado.

`degrau serve` escuta só em localhost, e deve continuar assim: **não há
autenticação nenhuma neste app**, por decisão do MVP. Qualquer um que alcance a
porta lê e altera o baralho.

`import` nunca apaga a sua entrada: arquivo inválido vai para `inbox/rejected/`
com um `.error.txt` ao lado dizendo o motivo, e reimportar o mesmo texto não
duplica (a identidade é o hash do inglês adaptado).

Os portões, na ordem em que valem:

```
ruff format . && ruff check . && mypy && pytest
```

O **painel** responde uma pergunta só, e ela não é "quantos cartões eu tenho".
A escada mostra, para cada faixa, quantas palavras daquela faixa você já
aprendeu sobre o tamanho da faixa — 58 de 500 do A1 é 11,6%, e isso é
informação; 140 cartões no baralho não é. O denominador vem da lista de
palavras, então recalibrar `bands.toml` move a escada junto.

Duas coisas o painel se recusa a responder, de propósito. **C1 e C2 não têm
barra**: vêm de uma escala de frequência sem fim, não existe total para dividir,
e imprimir um seria inventá-lo. E a **retenção fica em branco** até haver
tentativas reais de recordação — os passos de aprendizagem são separados por
minutos, e contá-los como retenção infla o número sem dizer nada. Um painel
confiantemente errado é pior que painel nenhum, porque nada na tela avisa para
duvidar.

Os arquivos estáticos são servidos com a impressão digital do conteúdo na URL
(`app.css?v=fb6014d5`). Sem isso, o navegador guarda a folha de estilo antiga e
uma mudança de CSS chega como tela quebrada — que foi exatamente o que
aconteceu, e é um bug que se parece com CSS errado sendo cache velho.

Os gráficos são SVG inline, sem biblioteca: barras de 30 dias para trás, curva de
retenção para 30 dias à frente. O eixo da retenção vai de 0 a 100% inteiros —
cortá-lo transformaria um declínio suave em precipício. Cada marca tem um
`<title>`, e cada gráfico tem uma tabela embaixo, para quem quer o número exato.

A **exportação para o Anki** sai por `degrau export` ou pelo botão no painel, e
os dois produzem o mesmo arquivo byte a byte. Três colunas — a palavra, a
tradução com o exemplo em itálico, e as tags — sob os cabeçalhos que o Anki
precisa. Detalhes que decidem entre importar direto e ter que mexer no diálogo:

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

Cartão salvo clicando num texto sai com o verso vazio; o comando diz quantos
estão assim. Referência: [Text Files, no manual do
Anki](https://docs.ankiweb.net/importing/text-files.html).

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

O diretório de dados é `data/`, e `DEGRAU_DATA_DIR` sobrescreve — é o que permite
aos testes rodarem contra um diretório temporário sem tocar nas listas reais.

- `data/ngsl.csv` — **New General Service List** (2.809 palavras), de
  [newgeneralservicelist.com](https://www.newgeneralservicelist.com/new-general-service-list),
  por Browne, Culligan e Phillips. Licença **CC BY-SA 4.0**. O arquivo publicado
  é o `NGSL_12_stats.csv`, guardado aqui sem alteração.
- `data/bands.toml` — os cortes de banda e os limiares de cobertura.
- `data/levels.toml` — o orçamento gramatical de cada nível, citado no prompt.

O banco é um arquivo SQLite (`degrau.db`, sobrescrevível por `DEGRAU_DB`) e o
`inbox/` por `DEGRAU_INBOX_DIR`. Nenhum dos dois vai para o git.

Frequência fora da NGSL vem do [wordfreq](https://pypi.org/project/wordfreq/).
