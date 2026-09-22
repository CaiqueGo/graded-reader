# Degrau

Leitor de inglês graduado por nível, com flashcards próprios e painel de evolução.

O Degrau pega **qualquer** texto e o reescreve no nível certo, extrai o vocabulário
que vale a pena aprender, e acompanha o que foi realmente aprendido ao longo do
tempo. O app não chama LLM nenhum: ele gera o prompt, recebe o JSON e faz todo o
trabalho determinístico — validação, lematização, agendamento, estatística.

## Por onde começar

- [`docs/degrau-mvp.md`](docs/degrau-mvp.md) — a especificação completa: o problema,
  a tese do produto, o contrato de importação, o modelo de dados e as etapas.

## Estado

| Etapa | O que entrega | Status |
|---|---|---|
| M0 | Léxico: bandas, lematização, cobertura, `degrau analyze` | pronto |
| M1 | O laço fechado via terminal: banco, `profile`, importador | a fazer |
| M2 | Leitura na web | a fazer |
| M3 | Revisão com FSRS | a fazer |
| M4 | Painel | a fazer |
| M5 | Exportação para o Anki | a fazer |

## Como rodar

```
uv venv --python 3.11
uv pip install -e ".[lexicon,dev]"
python -m spacy download en_core_web_sm
```

Medir um texto contra um nível:

```
degrau analyze exemplo/iceland-a1.txt --level A1
```

Sai com código 1 quando o texto não alcança o limiar do nível — é isso que permite
ao laço de adaptação saber que precisa tentar de novo. Aceita `-` para ler da
entrada padrão, e `--known arquivo.txt` (um lema por linha) para descontar o que
você já sabe da lista de candidatos a cartão.

Os portões, na ordem em que valem:

```
ruff format . && ruff check . && mypy && pytest
```

## Calibragem

`data/bands.toml` guarda os cortes que transformam frequência em nível CEFR. Eles
são **chutes calibrados, não verdade**: a NGSL é uma lista de frequência geral, não
um mapa CEFR oficial. Depois de uns 20 textos, compare a sensação de dificuldade
com a cobertura calculada e ajuste o arquivo — sem mexer em código.

Um sinal de que isso é necessário: o texto de exemplo em `exemplo/`, escrito à mão
tentando ficar em A1, mede 80,2% de cobertura A1. Parte disso é o texto realmente
derrapar; parte é a banda estar severa demais com palavras como `air`, `hot` e
`deep`.

## Dados

O diretório de dados é `data/`, e `DEGRAU_DATA_DIR` sobrescreve — é o que permite
aos testes rodarem contra um diretório temporário sem tocar nas listas reais.

- `data/ngsl.csv` — **New General Service List** (2.809 palavras), de
  [newgeneralservicelist.com](https://www.newgeneralservicelist.com/new-general-service-list),
  por Browne, Culligan e Phillips. Licença **CC BY-SA 4.0**. O arquivo publicado
  é o `NGSL_12_stats.csv`, guardado aqui sem alteração.
- `data/bands.toml` — os cortes de banda e os limiares de cobertura.

Frequência fora da NGSL vem do [wordfreq](https://pypi.org/project/wordfreq/).
