---
description: Adapta um texto para o nível do leitor, grava o JSON no inbox e importa
argument-hint: <nível> <arquivo-ou-url> [--new-words N]
allowed-tools: Bash(degrau:*), Bash(python -m degrau.cli:*), Read, Write, WebFetch
---

# /adapt

Argumentos recebidos: `$ARGUMENTS`

Primeiro token é o nível-alvo (`A1`…`C2`). Segundo é o texto de origem: um caminho
de arquivo ou uma URL. `--new-words N` é opcional.

Se faltar o nível ou a origem, **pare e pergunte** — não invente um nível.

## Passos

### 1. Leia o perfil

```
degrau profile --level <nível> [--new-words N]
```

A saída é o bloco de contexto. Ela traz o orçamento gramatical do nível e, o que
importa mais, **o vocabulário deste leitor**: o que ele já domina acima do nível,
as palavras que devem ser ensinadas neste texto, e as que estão prestes a
apagar. Use esse bloco como está — não resuma, não reordene.

### 2. Leia a origem

Arquivo → `Read`. URL → `WebFetch`. Guarde o texto **integral e literal**: ele vai
inteiro no campo `source.original_text`, e reler o original depois de entender o
adaptado é metade do método.

### 3. Adapte

Reescreva no nível-alvo, obedecendo o bloco do passo 1. As regras que não se
negociam:

- **Preserve o sentido e a ordem do original.** Não invente fato, não acrescente
  opinião, não corte um parágrafo porque ficou difícil — reescreva-o.
- **As palavras-alvo do passo 1 devem aparecer** no texto e no glossário.
- **Toda palavra fora do nível entra no glossário**, com tradução em português e
  uma frase de exemplo em inglês contendo a palavra.
- Escreva de 3 a 6 perguntas de compreensão, respondíveis só com o texto.

### 4. Grave o JSON

Em `inbox/AAAA-MM-DD-slug.json`, UTF-8, exatamente neste formato:

```json
{
  "schema": 1,
  "level": "A1",
  "title": "Título curto em inglês",
  "source": {
    "kind": "url",
    "value": "https://… ou o caminho do arquivo",
    "original_text": "o texto original integral"
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
  "prompt_used": "o prompt completo que você usou, para reprodutibilidade",
  "generated_at": "2026-09-21T22:40:00-03:00"
}
```

`schema`, `level` e `adapted_text` são obrigatórios. `kind` é `url`, `file` ou
`paste`.

### 5. Importe

```
degrau import
```

Ele valida, mede a cobertura e grava no banco. Arquivo inválido vai para
`inbox/rejected/` com um `.error.txt` ao lado dizendo o motivo — leia esse arquivo
antes de tentar de novo. Reimportar o mesmo texto não duplica.

### 6. Reporte, e decida se vale outra volta

Diga ao usuário: o título, o id do texto, a cobertura medida e quantas palavras
ficaram acima do nível.

Se o `degrau import` disser **below threshold**, ofereça uma segunda volta. O
limiar é 95% para A1/A2, 92% para B1/B2, 90% para C1/C2. Para investigar antes de
regravar:

```
degrau analyze <arquivo.txt> --level <nível>
```

Ele lista, por frequência, exatamente quais lemas passaram do nível — é essa lista
que diz o que reescrever. Não force a barra: um texto a 93% de A1 com quatro
palavras técnicas inevitáveis é melhor do que um texto mutilado a 96%. Mostre o
número e deixe o usuário decidir.
