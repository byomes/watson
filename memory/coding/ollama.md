# Ollama — Model Usage

## Models on Beelink
- llama3.2:3b — primary chat (dashboard + Open WebUI)
- phi3:mini — background tasks
- qwen2.5-coder:7b — code agent spec drafting, structured reasoning
- gemma3:1b — fast/lightweight queries

## API
Base URL: http://localhost:11434
Chat endpoint: POST /api/chat
Generate endpoint: POST /api/generate

## Patterns
- stream: false for all Watson job calls
- Always set a timeout (45s minimum for classification tasks)
- Normalize smart quotes before sending to model
- Classification prompts return single word: urgent/queue/discard
