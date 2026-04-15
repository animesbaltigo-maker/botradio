# AnimeThemes Radio Bot

Bot de Telegram focado em "radio de animes" usando a API publica do AnimeThemes.

## O que ele faz

- `/radio`: toca um tema aleatorio
- `/op`: toca uma abertura aleatoria
- `/ed`: toca um encerramento aleatorio
- `/anime <nome>`: toca um tema de um anime especifico
- `/op <nome>`: toca uma OP do anime informado
- `/ed <nome>`: toca uma ED do anime informado

O bot tenta enviar o audio remoto primeiro. Se o Telegram nao aceitar o arquivo remoto, ele cai para video e, por ultimo, envia os links diretos.

## Estrutura

- `bot.py`: bootstrap do bot
- `config.py`: leitura das variaveis de ambiente
- `handlers/`: comandos e callbacks do Telegram
- `services/animethemes_client.py`: integracao com a API do AnimeThemes

## Como rodar

1. Crie e ative um ambiente virtual.
2. Instale as dependencias:

```bash
pip install -r requirements.txt
```

3. Copie `.env.example` para `.env` e preencha `BOT_TOKEN`.
4. Exporte as variaveis do `.env`.
5. Rode:

```bash
python bot.py
```

## Variaveis de ambiente

- `BOT_TOKEN`: token do bot do Telegram
- `ANIMETHEMES_BASE_URL`: padrao `https://api.animethemes.moe`
- `ANIMETHEMES_REQUEST_TIMEOUT`: timeout HTTP em segundos

## Observacoes

- A busca por nome funciona melhor com o titulo oficial do anime.
- A API publica do AnimeThemes entrega `video.link` e `audio.link`, entao o bot nao depende de scraping.
- O bot foi montado como projeto separado para ficar 100% focado no AnimeThemes.

## Referencias

- API publica: https://api.animethemes.moe
- Documentacao: https://api-docs.animethemes.moe
