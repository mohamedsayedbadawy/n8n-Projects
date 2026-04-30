# AVA Knowledge Base

Fast local knowledge base for AVA AI Voice Agent.
Upload PDFs → indexed instantly → AVA searches in <10ms.

## Architecture — Same Server (192.168.1.57)

```
                    192.168.1.57
┌──────────────────────────────────────────────┐
│                                              │
│  ┌─────────────────┐    HTTP localhost:8001  │
│  │   AVA Docker    │ ──────────────────────► │
│  │  (ai_engine)    │ ◄──────────────────────  │
│  └─────────────────┘    JSON { answer: ... } │
│                                              │
│  ┌─────────────────┐                         │
│  │  ava-kb Docker  │  port 8001              │
│  │  FastAPI+SQLite │  UI: :8001              │
│  └─────────────────┘                         │
│                                              │
└──────────────────────────────────────────────┘
```

## Deployment Steps

### 1. Upload to server
```bash
scp -r ava-kb/ maintelecom@192.168.1.57:~/ava-kb/
```

### 2. Build & start
```bash
cd ~/ava-kb
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

### 3. Open Web UI
```
http://192.168.1.57:8001
```

### 4. Add to ai-agent.local.yaml
```yaml
    tools:
      - name: knowledge_base
        type: http
        description: "Search company knowledge base"
        url: "http://localhost:8001/search"
        method: GET
        parameters:
          q:
            type: string
            description: "The caller's question"
            required: true
        response_field: answer
```

### 5. Restart AVA
```bash
cd ~/AVA-AI-Voice-Agent-for-Asterisk
sudo docker compose restart
```

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/upload` | POST | Upload PDF |
| `/search?q=...` | GET | Search (AVA uses this) |
| `/documents` | GET | List all docs |
| `/documents/{id}` | DELETE | Remove doc |
| `/stats` | GET | Stats |

## Backup
```bash
cp ~/ava-kb/data/knowledge.db ~/backups/knowledge.db.$(date +%Y%m%d)
```
