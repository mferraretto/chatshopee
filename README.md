# Duoke Auto-Responder (Gemini + Playwright)

Um pacote completo para ler conversas no **Douke**, classificar a intenção com **Google Gemini**, 
aplicar **templates** (do seu PDF) e **responder automaticamente**. Inclui scripts para login, 
execução única e execução contínua.

> **Aviso importante**: os seletores da interface do Douke podem variar. Este projeto traz seletores
padrão em `config/selectors.json`. Se a sua conta tiver variações na UI, ajuste esses seletores usando
o modo **headful** (navegador visível) com o script de login/inspeção. A lógica e a estrutura já estão
prontas; normalmente basta ajustar 2–4 seletores para funcionar 100%.

---

## Requisitos

- **Python 3.10+**
- **Node** (apenas se desejar instalar browsers Playwright via CLI alternativa; recomendável usar Python)
- **Google Gemini API Key** (obtenha em: https://ai.google.dev/)
- Sistema com acesso a rede para o Douke

## Instalação

```bash
# 1) Crie e ative um virtualenv (opcional, mas recomendado)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2) Instale dependências
pip install -r requirements.txt

# 3) Instale os navegadores do Playwright
python -m playwright install --with-deps

# 4) Configure variáveis de ambiente (crie .env a partir do exemplo)
cp .env.example .env
# edite .env e coloque sua GEMINI_API_KEY
```

## Primeira execução (login manual)

O Douke pode exigir login/2FA. Use o script de **login** abaixo para abrir o site em modo visível.
Faça login normalmente e, quando terminar, **volte ao terminal e pressione Enter**. O script salvará
a sessão em `storage_state.json` para reutilizar nas execuções seguintes.

```bash
python -m src.login
```

> Se a interface for diferente, aproveite esse momento para inspecionar e ajustar seletores em `config/selectors.json`.

## Executar o bot (uma varredura)

```bash
python -m src.run_once
```

O bot vai:
1) Abrir o Douke com sua sessão salva
2) (Opcional) Aplicar o filtro “Precisa responder”
3) Iterar pelas conversas listadas
4) Ler as **últimas N mensagens** (configurável)
5) Perguntar ao **Gemini** qual a intenção e **se deve responder** (lembrando das suas regras)
6) Montar resposta a partir dos **templates** e **enviar**
7) Registrar logs no console

## Execução contínua (loop)

```bash
python -m src.run_loop
```
Por padrão, roda uma varredura a cada 120 segundos (config em `.env`).

## Regras de negócio implementadas

- Ler **não apenas a última mensagem**; considerar histórico recente.
- Se for **reclamação por quebra/defeito** → enviar texto das **3 opções**.
- Se **faltou peça** → enviar “posso enviar a peça que faltou ou reembolsar”.
- Se for **alguém reclamando que ainda não recebeu o Pix** **ou** **que ainda não enviamos a peça que faltou** → **pular** (não responder).
- Elogios/recebido → agradecer e se colocar à disposição.
- Dúvidas comuns (envio/rastreio) → templates FAQ.
- Permite **inserir código de rastreio** caso esteja visível (depende dos seletores e do layout atual).

## Estrutura

```
duoke-gemini-bot/
├─ .env.example
├─ requirements.txt
├─ README.md
├─ templates/templates.json
├─ config/selectors.json
├─ src/
│  ├─ config.py
│  ├─ gemini_client.py
│  ├─ classifier.py
│  ├─ templates.py
│  ├─ duoke.py
│  ├─ run_once.py
│  ├─ run_loop.py
│  └─ login.py
└─ storage_state.json  (gerado após login)
```

## Ajustando seletores (se necessário)

- **Lista de conversas**: `chat_list_item`
- **Texto das mensagens no painel**: `msg_text`
- **Campo de entrada**: `input_textarea`
- **Botão enviar**: `send_button` (opcional; Enter já envia)
- **Filtro “Precisa responder”**: `filter_needs_reply` (opcional)

Edite `config/selectors.json` caso a sua UI seja diferente.

## Segurança

- A chave do Gemini fica em `.env` (não commitar).
- O script não grava nenhuma credencial do Douke, apenas o **estado da sessão** (`storage_state.json`).
- Respeite os termos de uso do Douke/Shopee. RPA sempre tem risco de bloqueio se abusar.

## Troubleshooting

- **Login automático não persiste**: rode `python -m src.login` novamente e confirme que `storage_state.json` foi gerado.
- **Seletores quebraram**: use o login headful para inspecionar a UI e ajuste seletores.
- **Erros 429/anti‑bot**: aumente delays e reduza frequência de varredura.
- **Gemini indisponível**: o classificador faz fallback para regras simples.

## Dicas de Deploy / Performance

- Hospede o serviço o mais perto possível do Brasil (ex.: região São Paulo em GCP/AWS/Fly.io) para reduzir latência.
- Execute o robô Playwright em um **worker** dedicado e mantenha um web service leve só para a UI/WS.
- Prefira instâncias com CPU dedicada e pelo menos 2&nbsp;vCPU e 2–4&nbsp;GB de RAM.
- Desative auto-suspend/"sleep" para evitar cold starts.
- Bloqueie fontes, mídia e analytics via `route()` e use viewport menor (ex.: 1366×768).
- Faça screenshots apenas da área relevante e com intervalo maior (2–3&nbsp;s).
- Reaproveite browser/context entre tarefas e defina timeouts curtos (`page.set_default_timeout(6000)`), com retries/backoff.
- Instale fontes básicas no container (ex.: `fonts-liberation`, `fonts-noto`) ou aborte requisições de fonte.
- Ajuste a frequência de health checks (30–60&nbsp;s) e, se possível, execute o espelho em processo separado do robô.

Bom uso!
