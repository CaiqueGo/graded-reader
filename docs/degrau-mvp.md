# Degrau — Documento de MVP

Leitor de inglês graduado por nível, com flashcards próprios e painel de evolução.
Documento de especificação para implementação assistida por Claude Code.

**Versão:** 1.0 — 21/09/2026
**Status das decisões:** fechadas, salvo onde marcado `[aberto]`.

---

## 1. O problema

Ler em inglês só funciona como aprendizado quando o texto está pouco acima do que
a pessoa já sabe. Material autêntico (notícia, artigo, transcrição) quase nunca
está. As soluções existentes ou são textos graduados prontos — poucos, chatos, sobre
assuntos que não interessam — ou são tradutores, que não ensinam nada.

O Degrau pega **qualquer** texto e o reescreve no nível certo, extrai o vocabulário
que vale a pena aprender, e acompanha o que foi realmente aprendido ao longo do tempo.

**Usuário da v1:** uma pessoa (o autor), falante nativo de português brasileiro,
estudando inglês. Sem autenticação, sem multiusuário, sem nuvem.

---

## 2. A tese do produto

Três afirmações que o MVP existe para testar:

1. **O nível precisa ser verificado, não pedido.** Um LLM instruído a "escrever em A1"
   erra silenciosamente. A garantia vem de comparar o texto gerado com uma lista de
   frequência, em código, e exigir cobertura mínima.
2. **O nível CEFR é grosseiro; o vocabulário pessoal é o que importa.** A meta é
   adaptar para "as 1.000 palavras do A1 **mais** as 312 que este usuário já provou
   dominar", introduzindo de 5 a 8 palavras novas por texto (i+1).
3. **O vocabulário aprendido é o único indicador de progresso que não mente.** Não
   "dias de ofensiva", não "textos lidos": palavras que sobrevivem à repetição espaçada.

---

## 3. Escopo da v1

### Está dentro

- Importar um texto adaptado (JSON) produzido pelo Claude Code.
- Gerar o prompt de adaptação a partir do perfil do usuário (nível + vocabulário).
- Validar a cobertura de nível do texto importado, em código.
- Ler o texto adaptado na interface, clicar em palavras e salvá-las.
- Flashcards com repetição espaçada (FSRS) e tela de revisão com teclado.
- Painel com evolução de vocabulário, revisões e cobertura por nível.
- Exportar o baralho para o Anki (CSV).

### Está fora (v2 ou depois)

- Transcrição de áudio e vídeo (Whisper, `yt-dlp`).
- Chamada direta à API do Claude de dentro do app.
- Aplicativo ou sincronização com o celular.
- Multiusuário, login, deploy em servidor.
- Geração de exercícios além das perguntas de compreensão que vêm no JSON.

---

## 4. Fluxo principal

```
                      ┌──────────────────────────────┐
                      │  Claude Code (no repositório)│
                      │  /adapt A1 materia.txt       │
                      └──────────────┬───────────────┘
                                     │ 1. lê o perfil
                    ┌────────────────┴────────────────┐
                    │  degrau profile --level A1      │  ← CLI do próprio app
                    │  (nível, exceções, alvo i+1)    │
                    └────────────────┬────────────────┘
                                     │ 2. adapta e grava
                              inbox/2026-09-21-groenlandia.json
                                     │ 3. importa e valida
                    ┌────────────────┴────────────────┐
                    │  degrau import  /  botão na UI  │
                    │  cobertura 96% A1 · 4 fora      │
                    └────────────────┬────────────────┘
                                     │
              ┌──────────────────────┴──────────────────────┐
              │  Web (localhost:8000)                        │
              │  ler → clicar palavras → revisar → painel     │
              └──────────────────────────────────────────────┘
```

O ponto importante: **o app não chama LLM nenhum na v1.** Ele gera o prompt, recebe
o JSON e faz todo o trabalho determinístico (validação, lematização, agendamento,
estatística). Quem paga e executa a inteligência é o Claude Code, que você já tem
aberto no repositório.

---

## 5. Decisões técnicas

| Área | Decisão | Por quê |
|---|---|---|
| Linguagem | Python 3.12+ | Ecossistema linguístico (spaCy, wordfreq) é o coração do projeto |
| Web | FastAPI + Jinja2 + HTMX | Server-rendered, quase sem JS; a revisão precisa de teclado, não de SPA |
| Banco | SQLite (arquivo `degrau.db`) via SQLModel | Um processo, um arquivo, backup é `cp` |
| SRS | `fsrs` (py-fsrs) | Algoritmo do Anki moderno; não reimplementar SM-2 |
| Lematização | spaCy `en_core_web_sm` | `running → run`, evita cartões duplicados |
| Frequência | NGSL (2.809 palavras) + `wordfreq` | NGSL cobre A1–B1; Zipf do wordfreq cobre o resto |
| CLI | Typer | `degrau profile`, `degrau import`, `degrau analyze` |
| Testes | pytest | Só na camada de léxico e SRS (ver §11) |
| Formatação | ruff | — |

`[aberto]` Se a interface web travar o progresso, existe a saída de emergência de
fazer a v0 em Streamlit e migrar depois — mas isso custa a tela de revisão boa.

### Dependências

```
fastapi, uvicorn[standard], jinja2, python-multipart, sqlmodel,
fsrs, spacy, wordfreq, typer, httpx, pytest, ruff
```
Mais o modelo: `python -m spacy download en_core_web_sm`.

---

## 6. Camada de vocabulário (o núcleo)

Módulo `degrau/lexicon.py`. É a peça que dá valor ao resto; construa primeiro.

### Bandas de nível

Cada palavra recebe uma banda a partir de duas fontes, nesta ordem:

1. **NGSL**, pelo rank de frequência (arquivo CSV, licença CC BY-SA 4.0 — a atribuição
   vai no README):
   - rank 1–500 → `A1`
   - 501–1000 → `A2`
   - 1001–2000 → `B1`
   - 2001–2809 → `B2`
2. **wordfreq**, pelo Zipf, para o que não está na NGSL:
   - Zipf ≥ 4.0 → `B2`
   - 3.0 ≤ Zipf < 4.0 → `C1`
   - Zipf < 3.0 → `C2`
3. Nomes próprios, números e siglas (detectados pelo POS do spaCy) → `NA`, fora da conta.

Os limites acima são chutes calibrados; guarde-os em `data/bands.toml` para poder
ajustar sem mexer no código. Depois de uns 20 textos, compare a sensação de
dificuldade com a cobertura calculada e recalibre.

### Cobertura

```python
def coverage(text: str, level: str, known: set[str]) -> CoverageReport
```
Retorna: total de tokens, tokens dentro do nível-alvo, tokens fora (com lema, banda e
frequência no texto), percentual de cobertura, e a lista de candidatos a cartão
(palavras fora do nível **ou** acima da banda-alvo que não estão em `known`).

**Critério de aceitação de um texto:** cobertura ≥ 95% para A1/A2, ≥ 92% para B1/B2,
≥ 90% para C1/C2. Abaixo disso, a UI marca o texto como "fora do nível" e sugere
rodar `/adapt` de novo. Não bloqueie a importação — mostre o número e deixe decidir.

---

## 7. Contrato do arquivo de importação

Este é o contrato mais importante do projeto: é a fronteira entre o Claude Code e o app.
Arquivo JSON em `inbox/`, nome livre, UTF-8.

```json
{
  "schema": 1,
  "level": "A1",
  "title": "Iceland Turns Carbon Into Stone",
  "source": {
    "kind": "url",
    "value": "https://example.com/materia",
    "original_text": "texto integral original, como foi colado"
  },
  "adapted_text": "Parágrafo um.\n\nParágrafo dois.",
  "glossary": [
    {
      "en": "underground",
      "pt": "subterrâneo",
      "example_en": "They put the gas deep underground.",
      "example_pt": "Eles colocam o gás bem fundo, no subsolo."
    }
  ],
  "questions": [
    { "q": "What do they put into the rock?", "a": "Carbon dioxide and water." }
  ],
  "prompt_used": "o prompt completo, para reprodutibilidade",
  "generated_at": "2026-09-21T22:40:00-03:00"
}
```

Regras do importador:
- `schema`, `level`, `adapted_text` obrigatórios; o resto tolerado se faltar.
- Arquivo inválido vai para `inbox/rejeitados/` com um `.error.txt` ao lado. Nunca
  apague a entrada do usuário.
- Arquivo importado com sucesso vai para `inbox/processados/`.
- Importação é idempotente por hash do `adapted_text`: reimportar não duplica.
- Após importar, rodar a validação de cobertura e gravar o resultado no registro do texto.

---

## 8. Geração do prompt

Comando `degrau profile --level A1 [--new-words 8]` imprime o bloco de contexto que o
Claude Code injeta no prompt de adaptação. Ele contém:

- O nível-alvo e suas regras gramaticais (tabela fixa em `data/levels.toml`, uma
  entrada por nível: orçamento de vocabulário, estruturas permitidas, tamanho de frase).
- **Exceções para cima:** até 40 palavras que o usuário já domina e que estão acima do
  nível-alvo — podem ser usadas à vontade.
- **Alvo i+1:** 5 a 8 palavras da banda imediatamente superior, escolhidas entre as mais
  frequentes que o usuário ainda não tem no baralho — devem aparecer no texto e no glossário.
- **Reforço:** até 10 palavras que estão em aprendizado e vencem nos próximos 3 dias —
  se couberem naturalmente, devem reaparecer no texto.

**Não mande a lista inteira do nível no prompt.** O modelo já tem boa noção das faixas
de frequência do inglês; o que ele não tem é o seu perfil. A conferência rigorosa
acontece depois, em código.

O comando de barra do Claude Code fica em `.claude/commands/adapt.md` no próprio
repositório e descreve estes passos: rodar `degrau profile`, ler o texto de origem,
adaptar, escrever o JSON em `inbox/`, rodar `degrau import` e reportar a cobertura.

---

## 9. Modelo de dados

```
word
  id              INTEGER PK
  lemma           TEXT UNIQUE NOT NULL     -- forma base, minúscula
  display         TEXT NOT NULL            -- como apareceu no texto
  pt              TEXT
  example_en      TEXT
  example_pt      TEXT
  band            TEXT                     -- A1..C2, NA
  first_text_id   INTEGER FK -> text.id
  created_at      TEXT
  fsrs_json       TEXT NOT NULL            -- Card serializado (py-fsrs)
  due             TEXT NOT NULL            -- desnormalizado do Card, para consultar
  stability       REAL                     -- idem, para o painel
  state           TEXT                     -- new | learning | review | relearning

review
  id              INTEGER PK
  word_id         INTEGER FK -> word.id
  rating          INTEGER                  -- 1..4 (Again, Hard, Good, Easy)
  reviewed_at     TEXT
  log_json        TEXT                     -- ReviewLog serializado

text
  id              INTEGER PK
  title           TEXT
  level           TEXT
  source_kind     TEXT                     -- url | file | paste
  source_value    TEXT
  original_text   TEXT
  adapted_text    TEXT
  glossary_json   TEXT
  questions_json  TEXT
  prompt_used     TEXT
  coverage_pct    REAL
  out_of_level    TEXT                     -- JSON: [{lemma, band, count}]
  content_hash    TEXT UNIQUE
  created_at      TEXT

setting
  key             TEXT PK
  value           TEXT                     -- nível atual, limite diário de novas, etc.
```

Guardar `fsrs_json` inteiro e denormalizar `due`/`stability`/`state` evita reimplementar
o modelo do FSRS e ainda permite consultar a fila com SQL puro. A tabela `review` nunca
é apagada: é dela que sai todo o painel.

---

## 10. Telas

Três, em `localhost:8000`. Referência visual: o protótipo já construído (três abas,
tipografia serifada na área de leitura, painel com escada de níveis).

**Leitura** — lista dos textos importados; abrir um mostra o texto adaptado em fonte de
leitura, com as palavras já no baralho destacadas. Clicar numa palavra abre o cartão
lateral (lema, banda, tradução do glossário se houver) com o botão de salvar. Abaixo, o
glossário com "salvar todas", as perguntas de compreensão com resposta escondida, e a
faixa de cobertura ("96% dentro do A1 · 4 palavras acima").
Ao lado, o texto original acessível em uma aba — reler o original depois de entender o
adaptado é metade do valor do método.

**Revisão** — um cartão por vez, centralizado. Frente: a palavra e o exemplo com ela
apagada. Verso: tradução, exemplo completo, e os quatro botões do FSRS mostrando o
intervalo que cada um produz. **Teclado obrigatório:** espaço revela, 1–4 avaliam,
`u` desfaz a última avaliação. Sem teclado, revisar 40 cartões é sofrimento.

**Painel** — no topo, os números: palavras no baralho, dominadas (estabilidade ≥ 21
dias), em aprendizado, revisões hoje, retenção de 30 dias.
Depois, **a escada**: para cada nível, quantas das palavras daquela banda já estão
dominadas, sobre o tamanho da banda. É a métrica honesta de "quanto do A1 eu tenho" —
bem diferente de contar cartões.
Depois, o histórico de revisões (barras, 30 dias) e a curva de retenção prevista pelo
FSRS para os próximos 30 dias, que é o gráfico que mostra a carga de trabalho chegando.

---

## 11. O que testar

Testes só onde o erro é silencioso:

- `lexicon`: lematização de formas irregulares (`went → go`, `children → child`),
  atribuição de banda, cálculo de cobertura contra um texto fixo de referência.
- `importer`: JSON válido, JSON quebrado, reimportação (idempotência), campos ausentes.
- `srs`: uma sequência de avaliações produz intervalos crescentes; `Again` derruba;
  serialização e desserialização do `Card` sobrevivem a um round-trip pelo banco.

Não escreva teste de rota nem de template no MVP.

---

## 12. Etapas de construção

Cada etapa é utilizável sozinha e tem um critério de pronto verificável. Construa e
teste uma por vez.

**M0 — Léxico (o núcleo, sem interface)**
Projeto, dependências, NGSL baixada para `data/`, `lexicon.py` completo.
*Pronto quando:* `degrau analyze materia.txt --level A1` imprime a cobertura e a lista
de palavras fora do nível, e os testes de lematização passam.

**M1 — O laço fechado, via terminal**
Schema e banco, `degrau profile`, importador com validação, `.claude/commands/adapt.md`.
*Pronto quando:* você roda `/adapt A1 materia.txt` no Claude Code e o texto entra no
banco com a cobertura calculada, sem tocar em nenhuma interface.

**M2 — Leitura**
FastAPI, telas de lista e de leitura, clicar palavra, salvar no baralho, glossário,
perguntas.
*Pronto quando:* você lê um texto importado e termina com 8 palavras no baralho.

**M3 — Revisão**
py-fsrs integrado, fila do dia, tela de revisão com teclado, desfazer, limite diário
de cartões novos.
*Pronto quando:* você revisa três dias seguidos e os intervalos se comportam.

**M4 — Painel**
Números, escada de níveis, histórico, curva de retenção.
*Pronto quando:* o painel responde "quantas palavras do A1 eu já sei" com um número
que você acredita.

**M5 — Exportação Anki**
CSV com `termo, tradução<br><i>exemplo</i>, tags` e cabeçalho `#separator:Comma`,
`#html:true`.
*Pronto quando:* o arquivo importa no Anki sem ajuste manual.

Depois de M4, pare e use por duas semanas antes de escrever qualquer linha da v2. A
calibragem das bandas e o limite diário de cartões novos só aparecem com uso real.

---

## 13. Riscos conhecidos

**A lematização vai errar.** Formas irregulares e phrasal verbs (`give up` ≠ `give`)
vão gerar cartões estranhos. Mitigação: permitir editar o lema do cartão na interface e
tratar expressões de múltiplas palavras como um cartão só quando vierem do glossário.

**As bandas de nível são uma aproximação.** NGSL é lista de frequência geral, não mapa
CEFR oficial. A correspondência rank → nível é calibrável de propósito; não a trate como
verdade.

**O modo manual tem fricção.** Se rodar `/adapt` toda vez incomodar, esse é exatamente
o sinal de que vale a pena a v2 com API — e a interface `Adapter` já estará lá.

**Cartões demais cedo demais.** Salvar 12 palavras por texto vira 300 cartões vencidos
em um mês e abandono. Limite padrão: 10 cartões novos por dia, ajustável nas configurações.

---

## 14. Design para a v2 (o que não fazer errado agora)

Uma única abstração no MVP, definida em `degrau/adapters/base.py`:

```python
class Adapter(Protocol):
    def adapt(self, source_text: str, profile: Profile) -> AdaptedText: ...
```

`InboxAdapter` na v1 (lê de `inbox/`). `ApiAdapter` na v2 (chama a API do Claude com a
mesma `Profile` e devolve o mesmo `AdaptedText`). Nada mais precisa ser abstraído —
resista a criar camadas para banco, para renderização ou para transcrição antes de
existir um segundo caso de uso real.

Para a v2 no celular, o que importa é que a regra de negócio esteja em módulos puros
(`lexicon`, `srs`, `profile`), não dentro das rotas do FastAPI. Se isso for respeitado,
expor uma API JSON depois é um dia de trabalho.

---

## Fontes

- FSRS em Python: https://github.com/open-spaced-repetition/py-fsrs (pacote `fsrs`)
- New General Service List: https://www.newgeneralservicelist.com/new-general-service-list
  (2.809 palavras, CC BY-SA 4.0)
- wordfreq: https://pypi.org/project/wordfreq/
