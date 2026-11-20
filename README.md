# discord-meet-recorder

Enregistre les meets vocaux Discord, les transcrit en local avec Whisper, et les met à disposition de Claude via un serveur MCP (`list_meetings`, `get_meeting`, `get_meeting_transcript`), comme Claude Meetings le fait pour Google Meet.

```
Discord (salon vocal)
   │  un flux Opus par personne
   ▼
bot ──► /data/meetings/<id>/segments/*.wav + segments.jsonl + meeting.json
                     │
transcriber (faster-whisper, local) ──► transcript.json
                     │
mcp (lecture seule, HTTP + jeton) ◄── Claude Code
```

- Discord envoie un flux séparé pour chaque personne, donc on sait toujours qui parle : pas de reconnaissance des voix à faire.
- L'audio ne sort pas du serveur : la transcription tourne sur la machine, et les WAV sont supprimés une fois la transcription faite (`KEEP_AUDIO=false`).
- Le résumé, les décisions et les actions, c'est Claude qui les fait au moment où tu lui demandes, à partir de la transcription.

## Utilisation dans Discord

| Commande | Effet |
|---|---|
| `/meet start [titre]` | Le bot rejoint ton salon vocal et annonce dans le salon texte que le meet est enregistré |
| `/meet stop` | Arrête l'enregistrement ; le bot prévient dans le salon texte quand la transcription est prête |
| `/meet status` | Indique si un meet est en cours |

**Arrêt à la voix** : n'importe quel participant peut dire la phrase définie dans `STOP_PHRASE` (par défaut « merci Claude pour les travaux ») pour arrêter le meet, comme avec `/meet stop`. Pendant le meet, le transcripteur écoute chaque prise de parole de moins de 15 s avec un petit modèle rapide (`WHISPER_STOP_MODEL`) ; la phrase doit finir la prise de parole (quelques mots après sont tolérés), pour qu'en parler au milieu d'une explication ne coupe rien. Compter 3 à 5 s entre la phrase et l'arrêt. `STOP_PHRASE` vide désactive la fonction.

Le bot s'arrête aussi tout seul quand il reste seul dans le salon (`AUTO_STOP_EMPTY_SECONDS`) ou après `MAX_MEETING_MINUTES`.

Le message de début d'enregistrement sert à informer tout le monde : préviens aussi les invités qui arrivent en cours de meet.

## Installation

### 1. Créer le bot Discord

1. https://discord.com/developers/applications > **New Application**.
2. Onglet **Bot** : **Reset Token** et copier le token dans `.env` (`DISCORD_TOKEN`). Aucun « Privileged Gateway Intent » n'est nécessaire.
3. Onglet **OAuth2 > URL Generator** : scopes `bot` et `applications.commands` ; permissions `View Channels`, `Connect`, `Send Messages`. Ouvrir l'URL et ajouter le bot au serveur.

### 2. Lancer

```bash
cp .env.example .env   # remplir DISCORD_TOKEN et MCP_TOKEN (openssl rand -hex 32)
docker compose up -d --build
docker compose logs -f
```

Au premier démarrage, le transcripteur télécharge le modèle Whisper (environ 1,5 Go pour `medium`), qui est gardé dans le volume `whisper-models`.

La transcription se fait au fil du meet : chaque prise de parole est transcrite dès qu'elle est finie, et à l'arrêt il ne reste que les dernières, donc la transcription est prête environ une minute après. Ordre de grandeur sans GPU avec `medium` en `int8` : transcrire prend entre un tiers et la totalité de la durée de parole, selon le CPU ; si le CPU ne suit pas pendant un meet très bavard, le retard est rattrapé à l'arrêt. Si c'est trop lent sur le LXC, passe à `WHISPER_MODEL=small`.

### 3. Brancher Claude Code

```bash
claude mcp add --transport http --scope user discord-meetings http://<hôte-du-lxc>:3333/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

Puis, dans n'importe quelle conversation : « résume le dernier meet Discord », « quelles décisions on a prises au meet de mardi ? », « liste les actions pour Alice dans le meet `2026-11-20_1400_dev` ».

`get_meeting` et `get_meeting_transcript` acceptent un identifiant complet, un début d'identifiant ou `latest`.

Le serveur parle en HTTP simple : garde le port 3333 sur le réseau interne, ou mets-le derrière le reverse proxy en HTTPS. Les connecteurs de claude.ai (web et desktop hors Claude Code) demandent une URL publique en HTTPS avec OAuth : ce n'est pas géré ici.

En local sans Docker, le serveur MCP peut aussi tourner en stdio :

```bash
claude mcp add discord-meetings -e MCP_TRANSPORT=stdio -e DATA_DIR=/chemin/vers/data -- node /chemin/vers/mcp/dist/index.js
```

## Développement

```bash
cd bot && npm ci && npm run build && npm test   # test de l'enregistreur avec de l'Opus réel
cd mcp && npm ci && npm run build && npm test
cd transcriber && python3 -m unittest test_stop_phrase   # reconnaissance de la phrase d'arrêt
```

Fichiers d'un meet (`/data/meetings/<id>/`) : `meeting.json` (statut `recording` → `recorded` → `transcribing` → `transcribed` ou `failed`), `segments.jsonl`, `segments/*.wav`, `partial.jsonl` (transcription faite pendant le meet), `transcript.json`, `.notified`.

Si le bot redémarre pendant un meet, ce qui a déjà été enregistré est transcrit. Si le transcripteur redémarre pendant une transcription, il la reprend là où il en était.
