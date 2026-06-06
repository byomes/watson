# Watson Build
**Status:** Active
**Goal:** Build Watson into a fully operational personal AI assistant

## Key Files
- Repo: github.com/byomes/watson
- Beelink path: ~/watson
- DB: ~/watson/data/watson.db
- Dashboard: jobs/dashboard/app.py (port 5200)

## Current State
- Beelink live as primary server
- Dashboard running via Tailscale
- Open WebUI running via Docker
- Telegram bot live
- Connect Cards live (pending first Sunday run)
- People Registry live
- Google Calendar OAuth active
- Bible API active
- Code Agent pipeline active

## Next Steps
- Memory system (this build)
- Skill builder
- Email intake job
- Morning briefing auto-push
- KB search job

## Notes
- Stream retired
- Claude Code installed on Beelink
- All cron entries require PYTHONPATH=/home/billyomes/watson
