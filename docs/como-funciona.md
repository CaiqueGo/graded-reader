# Como funciona, hoje

Este documento descreve o sistema **como ele está**, não como foi planejado. A
especificação completa, com as decisões e o que ficou para depois, está em
[`graded-reader-mvp.md`](graded-reader-mvp.md).

## O laço

Tudo gira em volta de um ciclo de quatro passos. Cada tela do app é um deles.

```
   adaptar            ler              revisar           medir
  ---------        --------          ---------        ---------
  um texto   -->   no seu    -->     o que você  -->   se está
  qualquer         nível             salvou            colando
      ^                                                    |
      +----------------------------------------------------+
            o painel diz se o nível já pode subir
```

O app faz a parte determinística — medir, lematizar, agendar, contar. A
reescrita do texto é do Claude Code. O que você aprende sai do que **você**
escolhe salvar enquanto lê.

## Mapa das telas

| Tela | Endereço | Para quê |
|---|---|---|
| Biblioteca | `/` | Adicionar textos e abrir os que já entraram |
| Leitura | `/texts/{id}` | Ler, salvar palavras, salvar frases, ver o glossário |
| Review | `/review` | A fila do dia |
| Painel | `/dashboard` | Se está funcionando, e exportar para o Anki |

Sobe com `reader serve`, em http://127.0.0.1:8000.

## Três palavras que aparecem em toda tela

**Lema** é a forma base de uma palavra: `machines` e `machine` são o mesmo lema,
`machine`. O baralho guarda lemas, e é por isso que salvar `machine` faz
`machines` aparecer destacada no texto.

**Banda** é o nível CEFR estimado da palavra — A1 a C2. Sai da frequência com
que a palavra aparece no inglês em geral, não de uma lista oficial da CEFR; os
cortes estão em `data/bands.toml` e podem ser recalibrados sem mexer em código.

**Cobertura** é a porcentagem das palavras de um texto que estão no seu nível ou
abaixo. É o número que decide se um texto é legível para você. Ela mede
**vocabulário, não gramática** — um texto pode medir 95% e ainda assim ter
subordinação demais para um A1.

---

## Biblioteca: de onde vêm os textos

Três portas, todas levando ao mesmo lugar:

- **Add a text** — cola um endereço ou o artigo inteiro, escolhe o nível, e o
  app adapta sozinho. É o caminho normal.
- **Import waiting texts** — lê o que estiver no diretório `inbox/`. É o que o
  comando `/adapt` do terminal usa: ele grava um JSON ali e você importa.
- **Or paste the document** — cola o JSON adaptado direto no navegador.

Nada é apagado quando dá errado. Um documento inválido vai para
`inbox/rejected/` com um `.error.txt` ao lado explicando o motivo. Reimportar o
mesmo texto não duplica: a identidade é o hash do inglês adaptado.

Texto que fica abaixo do limiar de cobertura **entra assim mesmo**, marcado
`out of level`. O app mostra o número e deixa a decisão com você.

---

## Leitura: onde o baralho nasce

A tela tem o texto adaptado, e o original a um clique na aba ao lado.

### Clicar uma palavra

Abre um painel lateral com a tradução e o exemplo, quando o texto trouxe, e um
botão para salvar. Vira um **cartão de palavra**. Palavras já salvas aparecem
destacadas no texto, inclusive nas formas flexionadas.

### Selecionar um trecho

Arraste o mouse por duas palavras ou mais e aparece **Save this sentence**.
Você escolhe qual palavra do trecho é o alvo, escreve o que a frase quer dizer,
e vira um **cartão de frase**.

O recorte é exatamente o que você selecionou — uma oração, uma expressão, meia
linha. A palavra-alvo é **marcada, não apagada**: o ponto é ler a frase e saber
o que ela diz, não completar lacuna.

Na hora de escolher o alvo, as palavras que o texto marcou como acima do seu
nível vêm primeiro — são as que provavelmente fizeram você selecionar aquilo.

### O glossário

Embaixo do texto fica o **glossário**: a lista de palavras que a adaptação
decidiu ensinar naquele texto, com tradução e frase de exemplo.

Quem escolhe é o adaptador, na hora em que o texto foi processado, olhando o seu
perfil: o que você já sabe, o que está prestes a esquecer, e quantas palavras
novas cabem. Um texto adaptado costuma trazer de 10 a 30 entradas. O glossário
fica guardado com o texto — não é recalculado, e não muda quando o seu baralho
muda.

O que a tela acrescenta é só uma coisa: quais dessas palavras **já estão no seu
baralho**, marcadas com `in deck`.

**Save all** salva todas de uma vez, como cartões de palavra. As que já estão no
baralho são deixadas exatamente como estão, **agendamento incluído** — clicar
duas vezes não reinicia o cronograma de nada.

### Glossário e cartão de frase não são a mesma coisa

|  | Glossário | Selecionar um trecho |
|---|---|---|
| Quem escolhe | a adaptação, antes de você ler | você, enquanto lê |
| Frente do cartão | a palavra, com o exemplo como lacuna | a frase, com a palavra destacada |
| Verso | tradução da palavra | o que **você** escreveu |
| Custo | um clique para 20 palavras | um por frase |

O glossário é o caminho rápido e as palavras são escolha do adaptador. A seleção
é lenta e é sua. Os dois convivem: a mesma palavra pode ter um cartão de palavra
e um ou mais cartões de frase.

---

## Review: a fila do dia

O topo da tela é um placar:

```
1 due   9 new   +3 waiting   1 done today
```

| Contador | O que é |
|---|---|
| `due` | Cartões que já venceram e precisam voltar hoje |
| `new` | Cartões novos que ainda cabem **no limite de hoje** |
| `+N waiting` | Cartões que você salvou e que o limite segurou |
| `done today` | Avaliações desde a meia-noite |

### O teclado é a interface

**espaço** revela a resposta. **1–4** avaliam: Again, Hard, Good, Easy. **u**
desfaz a última nota. O mouse funciona, mas ninguém que revisa todo dia usa.

Os intervalos escritos nos botões vêm com `~` de propósito. O FSRS embaralha os
intervalos para que oito palavras salvas do mesmo texto não voltem todas juntas
para sempre — o número é a ordem de grandeza, não a promessa.

### Desfazer é desfazer mesmo

`u` restaura o cartão a partir de um retrato guardado na hora da nota, e **apaga
a linha da revisão**. Um clique errado não vira história: se ficasse lá, sujaria
a curva de retenção e a contagem do dia.

### O limite diário de cartões novos

Padrão **10**, ajustável na própria tela de review. Ele conta **primeiras
aparições**, não avaliações — um cartão revisto quatro vezes hoje gastou uma
vaga, não quatro.

O limite existe para você não acordar com 300 cartões vencidos daqui a um mês e
abandonar o baralho. Mas ele esconde coisa, e por isso a tela diz quantos estão
esperando e por quê. Os cartões novos saem **do mais antigo para o mais
recente**: uma pilha atrasada é resolvida, não soterrada por chegadas frescas —
então salvar uma frase hoje não fura a fila das palavras de ontem.

### Editar e adicionar

Dá para **adicionar uma palavra** direto ao baralho, sem passar por texto
nenhum, e **corrigir o cartão** na hora em que ele aparece — inclusive o lema,
que é o ponto fraco conhecido da lematização (`give up` não é `give`). Corrigir
o lema não mexe no agendamento: é o mesmo cartão. Renomear para um lema que já
existe é recusado, porque fundir dois históricos é decisão sua.

---

## Painel: se está funcionando

O painel responde uma pergunta, e ela **não** é "quantos cartões eu tenho".

### Os cinco números

| Número | O que significa |
|---|---|
| **In the deck** | Cartões salvos, no total |
| **Learned** | Cartões com estabilidade ≥ **21 dias** — os que você provavelmente ainda terá em três semanas |
| **In flight** | Ainda em aprendizagem ou reaprendizagem, não assentaram |
| **Reviewed today** | Avaliações desde a meia-noite |
| **Retention** | Quantos você acertou, das tentativas reais de recordação dos últimos 30 dias |

"Learned" é uma afirmação sobre o futuro, e o app usa a mesma constante de 21
dias para dizer isso no painel e para montar a lista de palavras que o adaptador
pode considerar conhecidas. Uma definição só, nos dois lugares.

### A escada

Para cada banda, quantas palavras daquela banda você já aprendeu sobre o tamanho
da banda: **58 de 500 do A1 é 11,6%**, e isso é informação. "140 cartões no
baralho" não é.

A barra tem duas camadas: o que está no baralho e, mais forte, o que já conta
como aprendido. O denominador vem da lista de palavras, então recalibrar
`bands.toml` move a escada junto.

**C1 e C2 não têm barra.** Elas vêm de uma escala de frequência sem fim; não
existe um total para dividir, e imprimir um seria inventá-lo. Você vê a
contagem, não a porcentagem.

### Os dois gráficos

SVG desenhado pelo app, sem biblioteca. **Histórico**: barras dos últimos 30
dias. **Retenção**: para cada um dos próximos 30 dias, a chance média de você
recordar um cartão — junto com quantos vencem naquele dia.

A curva usa **só os cartões já avaliados**. Um cartão que nunca foi revisto não
tem memória para decair, e jogá-lo na média como 0 ou como 100% moveria a linha
sem significar nada.

O eixo da retenção vai de **0 a 100% inteiros**. Cortá-lo em 80% transformaria
um declínio suave em precipício. Cada marca tem um `<title>` no passar do mouse,
e cada gráfico tem uma tabela embaixo para quem quer o número exato.

### O painel se recusa a responder duas coisas

A **retenção fica em branco** até haver tentativas reais de recordação. Os
passos de aprendizagem são separados por minutos; contá-los como retenção infla
o número sem dizer nada.

E, como já dito, **C1 e C2 não ganham porcentagem**. Um painel confiantemente
errado é pior que painel nenhum, porque nada na tela avisa para duvidar.

---

## Exportar para o Anki

Pelo botão **Export for Anki** no painel ou por `reader export` — os dois
produzem o mesmo arquivo, byte a byte.

São três colunas: a palavra (ou a frase), a tradução com o exemplo em itálico, e
as tags. A tag diz qual tipo de cartão é (`graded-reader word` e `graded-reader
sentence`), então você pode dar tratamentos diferentes aos dois no Anki.

Reexportar **atualiza** as mesmas notas em vez de dobrar o baralho: o Anki usa o
primeiro campo como identidade da nota.

Cartão salvo clicando numa palavra sai com o verso vazio. O comando diz quantos
estão assim.

---

## O que o sistema não faz

Vale saber antes de confiar demais:

- **Não há autenticação.** `reader serve` escuta só em localhost, e deve
  continuar assim. Quem alcançar a porta lê e altera o baralho.
- **A cobertura não mede gramática.** Só vocabulário.
- **As bandas são chutes calibrados**, não verdade oficial da CEFR.
- **A lematização erra em verbo frasal.** `give up` vira `give`. Dá para
  corrigir o lema na tela de review.
- **O adaptador busca qualquer endereço http(s) que você digitar**, inclusive de
  rede local. Aceitável porque só você digita — não seria, com um segundo
  usuário.
