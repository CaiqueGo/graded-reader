# Ideias para a v2

**Nada aqui está decidido.** É a lista do que foi levantado, com o que já existe
no código, o que falta, e a pergunta que decide cada uma. O §12 do
[MVP](graded-reader-mvp.md) manda usar duas semanas antes de escrever qualquer
linha da v2 — o banco foi zerado em 22/09/2026 e esse é o relógio.

A ordem abaixo é por custo, não por importância.

---

## 1. Apagar textos na Biblioteca

**Hoje não existe.** Não há rota, nem função no `library.py`: um texto importado
fica para sempre.

O trabalho é pequeno, mas tem uma decisão dentro dele que não é. O cartão guarda
`word.first_text_id` como **chave estrangeira** para `text.id`, e o SQLite roda
com a checagem **desligada** (é o padrão, e nada no app liga). Apagar o texto
hoje deixaria cartões apontando para um fantasma, em silêncio.

Três saídas, e a escolha é de produto:

| Saída | O que acontece com os cartões |
|---|---|
| Soltar a referência (`SET NULL`) | Ficam, sem origem. O baralho é seu, o texto era só onde você achou a palavra |
| Apagar junto (`CASCADE`) | Somem com o texto, história de revisão incluída |
| Recusar enquanto houver cartão | Você decide cartão a cartão antes |

**Recomendação:** soltar a referência. O baralho é o ativo; o texto é o andaime.
E ligar `PRAGMA foreign_keys = ON` junto, senão a próxima FK repete o problema.

---

## 2. Áudio do texto

Ouvir enquanto lê é o ganho clássico de um leitor graduado, e a primeira versão
é quase de graça: o navegador tem `speechSynthesis` embutido — sem dependência,
sem API, sem custo, funciona offline.

O que dá para fazer com isso: um play no texto, um play no cartão de frase, e o
destaque acompanhando a frase que está sendo lida (o evento `boundary` do
próprio navegador dá a posição).

**O que decide:** se a voz do sistema for boa o bastante para o seu ouvido. Se
não for, o degrau seguinte é uma API de TTS — aí tem custo por caractere e vale
guardar o áudio em disco, porque o mesmo texto relido não deve pagar duas vezes.

---

## 3. O Review parecendo mais um Anki

Precisa virar lista concreta antes de virar código — "parecer o Anki" são umas
seis coisas diferentes, e algumas nós já temos:

| Peça | Situação |
|---|---|
| Teclado, quatro notas, intervalos nos botões | **existe** |
| Desfazer | **existe** |
| Editar o cartão durante a revisão | **existe** |
| Limite diário de cartões novos | **existe** |
| **Navegador do baralho** (ver, buscar, filtrar, apagar) | falta |
| **Suspender e adiar** um cartão | falta |
| Contadores separados por tipo, com cor (novo / aprendendo / vencido) | falta (hoje são três números lisos) |
| Tela inicial de estudo antes da fila | falta |
| Tipos de nota e modelos de cartão | falta, e provavelmente não deve existir |

**Recomendação:** começar pelo **navegador do baralho**. É a lacuna mais óbvia
do app hoje — não há como responder "o que eu tenho?", achar aquele cartão ruim
que você lembra de ter feito, ou apagar um. Tudo por baixo já existe
(`words.all_cards`, a edição, o `CardKind`); falta a página.

Depois **suspender**, que é o que se faz com um cartão que não vale a pena e não
se quer apagar.

**O que não copiar:** tipos de nota e modelos. É a parte do Anki que mais gera
configuração e menos gera estudo, e aqui os dois tipos de cartão saem do jeito
que você salvou.

---

## 4. PDF, EPUB e outros arquivos

A extração é a parte fácil (`pypdf` ou `pdfminer` para PDF, `ebooklib` para
EPUB). O problema é de modelo de dados: hoje **um texto é um artigo**, e o
limite é de 2.000 palavras (`sources.MAX_WORDS`). Um livro tem 80.000.

Então isso não é "mais um formato de entrada", é a noção de **obra dividida em
partes**: um capítulo vira um texto, os capítulos se conhecem, a leitura lembra
onde parou, e a Biblioteca agrupa em vez de listar 40 itens soltos.

**O que decide:** se o que você quer é ler livro ou só tirar um trecho de um
PDF. Se for a segunda, o caminho é bem mais curto — aceitar o arquivo, extrair,
e deixar você escolher o pedaço.

---

## 5. Vídeo do YouTube com legenda adaptada embaixo

O vídeo embutido na página e a legenda no seu nível abaixo dele.

Extrair a transcrição é resolvido (`yt-dlp` pega as legendas, inclusive as
automáticas). O problema real é outro, e é bom encará-lo antes de começar:

**adaptar destrói o alinhamento.** As legendas vêm em trechos com tempo
(`00:01:12 --> 00:01:15`). A adaptação reescreve o texto inteiro — junta frases,
corta outras, troca palavras — e o resultado não tem mais como saber qual pedaço
corresponde a qual segundo. Ou seja: ou a legenda acompanha o vídeo, ou ela está
no seu nível. As duas ao mesmo tempo exigem escolher um dos dois desenhos:

- **Adaptar trecho a trecho**, preservando o tempo de cada um. A legenda
  acompanha o vídeo de verdade. O texto fica pior — o adaptador perde a visão do
  todo e não pode reorganizar nada.
- **Adaptar o todo e não sincronizar.** O texto fica bom, aparece ao lado do
  vídeo como um artigo, e você lê antes ou depois de assistir. Sem karaokê.

**Recomendação:** a segunda, porque é a que respeita o que o app já faz bem — e
porque "ler o texto adaptado, depois assistir ao original" é um exercício melhor
do que legenda correndo.

---

## 6. Remontar a página com o texto trocado (X, Reddit)

A ideia: buscar a página, trocar o texto pelo texto no seu nível, e devolver a
página **com a cara dela**, para ler threads de rede social adaptadas.

É a mais ambiciosa da lista e a única sobre a qual eu tenho ressalva séria — em
três frentes:

**Segurança.** Devolver HTML de terceiro dentro do seu app é XSS por construção.
Não é hipótese: hoje o app já busca qualquer endereço que você digitar, e a
única coisa que segura isso é que o conteúdo buscado vira **texto**, nunca
marcação. Manter a marcação original significa sanitizar HTML hostil, o que é
uma disciplina inteira, e fazer isso num app **sem autenticação nenhuma**.

**Acesso.** X e Reddit não são páginas comuns. O X exige login e bloqueia
leitura anônima. O Reddit tem JSON público, mas com termos que restringem uso
automatizado. Essa ideia não está travada por código — está travada por acesso,
e nenhuma linha escrita aqui destrava.

**Valor.** Numa thread, o que vale não é o CSS: é **quem disse o quê e
respondendo a quem**. Isso é estrutura, e estrutura dá para preservar como
dados.

**Contraproposta:** em vez de remontar a página, guardar a **estrutura da
conversa** — autor, ordem, aninhamento — e renderizar na sua própria tela de
leitura, com cada fala adaptada ao seu nível. Você ganha a thread legível, com
todo o resto do app funcionando em cima (clicar palavra, salvar frase), e sem
herdar HTML de ninguém. Fica faltando só a aparência do site — que é justamente
a parte que não ensina inglês.

---

## Sugestão de ordem

1. **Apagar textos** — dias, e conserta uma falha de integridade que já existe
2. **Áudio** — dias, e é a melhor relação valor/esforço da lista
3. **Navegador do baralho** — a maior lacuna do app hoje
4. **PDF/EPUB ou YouTube** — semanas, e a escolha depende do que você lê
5. **Threads** — só depois de decidir estrutura em vez de remontagem

Fora da lista, mas rondando: **celular**. Não foi pedido, e continua sendo onde
a leitura realmente acontece. Só que sair do localhost significa autenticação,
HTTPS e deploy — projeto declarado, não puxadinho.
