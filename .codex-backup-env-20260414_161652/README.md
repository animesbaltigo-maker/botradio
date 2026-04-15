# Rádio Animes

Bot de Telegram para ouvir trilhas de anime com foco em:

- busca de anime com seletor de faixa
- MP3 com capa e banner
- idiomas em português, inglês e espanhol
- gate de canal + idioma
- modo inline baseado no `/anime`
- cache, concorrência e fluxo pronto para uso real

## Principais recursos

- `/radio`: toca uma faixa aleatória
- `/anime <nome>`: busca o anime e abre seletor com OP + ED
- `/op <nome>`: busca o anime e abre seletor só de aberturas
- `/ed <nome>`: busca o anime e abre seletor só de encerramentos
- `/idioma`: escolhe o idioma do bot
- modo inline: busca animes e abre o bot no anime específico

## Estrutura

- `bot.py`: bootstrap do app, handlers, persistência e concorrência
- `config.py`: leitura das variáveis de ambiente
- `handlers/`: comandos, callbacks, start, idioma e inline
- `services/animethemes_client.py`: integração com a API pública
- `services/media_pipeline.py`: download, cache, conversão MP3 e thumb
- `services/gatekeeper.py`: validação de idioma + canal com cache
- `services/i18n.py`: traduções centralizadas
- `services/radio_ui.py`: textos e teclados padronizados
- `services/user_state.py`: preferências do usuário e ação pendente

## Como rodar

1. Crie e ative um ambiente virtual.
2. Instale as dependências:

```bash
pip install -r requirements.txt
```

3. Copie `.env.example` para `.env`.
4. Preencha `BOT_TOKEN`.
5. Rode:

```bash
python bot.py
```

## Variáveis de ambiente

- `BOT_TOKEN`: token do bot
- `ANIMETHEMES_BASE_URL`: padrão `https://api.animethemes.moe`
- `ANIMETHEMES_REQUEST_TIMEOUT`: timeout HTTP da API
- `RADIO_ANIMES_CHANNEL_CHAT`: canal obrigatório para o gate
- `RADIO_ANIMES_CHANNEL_URL`: URL do canal obrigatório
- `CHANNEL_MEMBERSHIP_TTL_SECONDS`: cache da validação do canal

## Observações

- O bot usa persistência local com `PicklePersistence` para salvar idioma e ação pendente por usuário.
- O pipeline de mídia mantém cache em disco de áudio, capa e miniatura.
- A API pública segue sendo a fonte dos temas, mas toda a experiência visível para o usuário é da marca Rádio Animes.
