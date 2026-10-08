# Nudge with Ollama: a model of your own

[Ollama](https://ollama.com) runs open models (Qwen, Llama, Gemma, Mistral, DeepSeek) on a
machine of yours. Nudge speaks to it like to any provider.

- **Free.** No key, no bill, no limit per minute. What it costs is the machine and its power.
- **Private.** Questions, screens, files and what the agent reads stay on that machine.
- **Open models.** Qwen and Mistral are under Apache 2.0; Llama has a licence of its own, open to
  companies of every usual size.

## Which machine, which model

| Machine | Model | Tools Given | What to expect |
|---|---|---|---|
| Laptop, graphics card of 6–8 GB | `qwen3:4b-instruct` | Essential tools | Talk works well; Agent and Developer mode answer, slowly and simply |
| Server, graphics card of 16–24 GB | `qwen3:14b` to `qwen3:32b` | All the tools | Talk, Agent and Developer mode |
| No graphics card | — | — | Too slow for an answer in 90 seconds |

Qwen is the best of its size in Arabic and with tools. On a small card, take a model that does not
think (`-instruct`): a thinking model spends a minute before its first word, and the thinking-only
builds (`qwen3:4b`) write their thinking into the answer when told not to think. A model that sees pictures (`qwen2.5vl`,
`gemma3`) reads the pictures sent with a question; Ollama does not turn speech into text, so
speech stays with Chrome and Edge, or with another provider that listens.

## Install

**Windows**

```
winget install Ollama.Ollama
setx OLLAMA_MODELS "D:\ollama\models"
```

The second line keeps the models (5 GB and more each) off a full drive C; open Ollama again
after it.

**Linux**

```
curl -fsSL https://ollama.com/install.sh | sh
```

Then pull a model:

```
ollama pull qwen3:4b-instruct
```

## Connect Nudge

1. **Nudge ‣ Configuration ‣ AI Providers ‣ Ollama (local)**: in **Base URL**, the address of
   the machine followed by `/v1`.
   - Odoo on the same machine: `http://localhost:11434/v1`.
   - Odoo on another machine, or on Odoo.sh: the address Odoo reaches it by (see below).
     `localhost` there would be the Odoo server itself.
2. **Fetch Models.** Nudge sees that Ollama answers, says so, and chooses the **essential
   tools** the first time. The list shows, for each model, its size, whether it uses **Tools**
   (needed by Agent and Developer modes), whether it sees **Pictures**, and its context at most.
3. Press **Use** on a model with tools, then **Test Connection**.
4. When Nudge says the server gives the model less context than it needs, press **Prepare for
   Nudge**: a copy of the model with enough context is made on the server and chosen. Nothing
   is downloaded; Ollama shares the weights.

### Why the context matters

Ollama gives every model a context it chooses by the memory of the graphics card: 4096 tokens
on a card under 24 GB. A request of Nudge in Developer mode is larger (the instructions, the
tools, the conversation), and Ollama cuts what does not fit without a word: the model then
answers without its instructions, and wrongly. Prepare for Nudge, or on the server:

```
setx OLLAMA_CONTEXT_LENGTH 32768        (Windows)
OLLAMA_CONTEXT_LENGTH=32768             (Linux, in the service's environment)
```

More context takes more memory of the graphics card: on a small card, keep the essential tools,
which need less.

## Measured on a laptop

RTX 4050 with 6 GB, 16 GB of memory, Ollama 0.40, the essential tools, no reasoning, a context of
24,576 tokens made by Prepare for Nudge, the interface in Arabic, one question per mode:

| Model | Talk: how do I make a quotation | Agent: how many contacts are companies | Developer: a welcome email for new companies |
|---|---|---|---|
| `qwen3:4b-instruct` | 28 s, the steps in Arabic | 86 s, read where the companies are, then asked | 58 s, asked what it needed before building |
| `qwen3:8b` | 30 s, pointed the menu | 67 s, answered in English, no count | over the time |
| `qwen3:4b` (thinks) | its thinking came out as the answer | — | — |

The requests weighed 6,800 tokens in Talk, 10,900 in Agent and 17,800 in Developer mode. The 8B
model does not fit a 6 GB card with that context: two thirds of it ran on the processor. On such a
card, more speed comes from `OLLAMA_KV_CACHE_TYPE=q8_0` on the server (the context takes half the
memory). For real use of Agent and Developer mode, a server with a card of 16 GB or more.

## Odoo.sh, or Odoo on another machine

Odoo.sh cannot run Ollama, and Ollama asks for no password. Never open its port 11434 to the
internet as it is.

- **A server of yours:** put a reverse proxy with HTTPS in front of it, which lets through only
  the requests carrying your key. With nginx:

  ```
  server {
      listen 443 ssl;
      server_name ollama.example.com;
      # ssl_certificate and ssl_certificate_key of the domain
      location / {
          if ($http_authorization != "Bearer A-LONG-RANDOM-KEY") { return 401; }
          proxy_pass http://127.0.0.1:11434;
          proxy_buffering off;
          proxy_read_timeout 120s;
      }
  }
  ```

  On the provider: Base URL `https://ollama.example.com/v1`, tick **Requires an API Key** and
  paste the key. Nudge sends it with every request, to the chat and to what it asks Ollama.

- **A trial from a laptop:** a tunnel gives the laptop an address for a while:

  ```
  cloudflared tunnel --url http://localhost:11434
  ```

  Whoever has that address can use the model while the tunnel is open: for a short trial only.

## When something is off

| Nudge says | What to do |
|---|---|
| Ollama does not answer at … | Start Ollama (the app, or `ollama serve`) on its machine |
| Ollama has no model … | `ollama pull <model>` on its machine |
| Ollama took too long to answer | The model was loading, or is too large for the machine: ask again; a smaller model or the essential tools |
| The model … cannot use tools | Choose a model with Tools ticked in the list |
| The server gives … tokens; Nudge needs about … | Prepare for Nudge, or `OLLAMA_CONTEXT_LENGTH` |
