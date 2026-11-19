# Destaque Freela

Sistema pessoal para encontrar bons projetos freelance e responder rápido com uma proposta bem feita.

Cada parte entrega algo que a próxima usa:

1. **Robô (radar.py)** busca projetos no 99Freelas e na Workana a cada 2 horas, pelo GitHub Actions.
2. **Nota** cada projeto ganha uma nota de 0 a 100 conforme as habilidades do `config.json`. Projetos com termos que não são da minha área perdem pontos, e projetos com menos propostas ganham um pouco mais.
3. **Mercado** o robô calcula quais habilidades mais aparecem, a mediana de propostas por projeto e o orçamento típico.
4. **Alerta** projetos com nota alta chegam no Telegram.
5. **Página** (`docs/index.html`) mostra os projetos ordenados pela nota. Um toque em "Gerar proposta" leva o anúncio para a aba de proposta, e os dados do mercado alimentam o perfil e os serviços.

## Como usar

1. Suba esta pasta para um repositório no GitHub.
2. Em **Settings > Actions > General**, marque "Read and write permissions".
3. Em **Settings > Pages**, escolha a branch `main` e a pasta `/docs`. A página fica em `https://SEU-USUARIO.github.io/NOME-DO-REPO/`.
4. Em **Actions > radar > Run workflow**, rode a primeira busca.

### Alerta no Telegram (opcional)

1. No Telegram, fale com o @BotFather, crie um bot e copie o token.
2. Mande qualquer mensagem para o bot e abra `https://api.telegram.org/botTOKEN/getUpdates` para ver o seu `chat id`.
3. Em **Settings > Secrets and variables > Actions**, crie `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.

## Ajustes

Tudo fica no `config.json`:

- `buscas`: termos pesquisados nos sites
- `habilidades`: peso de cada habilidade na nota
- `evitar`: termos que derrubam a nota
- `nota_minima_alerta`: a partir de qual nota chega aviso

## Rodar no computador

```
pip install -r requirements.txt
python radar.py
```

## Observações

- O robô só lê as listas públicas de projetos, com pausa entre os pedidos. Ele não entra na conta nem envia propostas: isso eu faço manualmente.
- Se os sites mudarem o layout, a busca pode parar de achar projetos e o Actions avisa com erro.

Ideias inspiradas em [workaholic](https://github.com/DykstraBruno/workaholic), [radar-freelas](https://github.com/b4rao-dg/radar-freelas) e [workana-stats](https://github.com/godoimatheus/workana-stats).
