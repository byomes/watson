# Python — Watson Conventions

## Environment
- Python 3, venv at ~/watson/venv
- All jobs run as modules: python3 -m jobs.jobname
- PYTHONPATH=/home/billyomes/watson required on all cron entries
- requirements.txt at repo root

## Patterns
- Jobs are discrete scripts under jobs/
- Shared utilities in core/ (messaging, db, etc.)
- All DB access via watson.db helper functions
- Logging to ~/watson/logs/

## Conventions
- snake_case for files and functions
- Jobs named by function: intake.py, email_reports.py, sync.py
- All jobs syntax-checked before commit
- .env for credentials — never hardcoded
