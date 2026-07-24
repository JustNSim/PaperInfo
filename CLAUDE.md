# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py

# Access web interface at http://localhost:5000
```

`FLASK_DEBUG` env var controls debug mode of `python app.py` (default off). The app listens on `127.0.0.1:5000` unless `PAPERINFO_HOST`/`PAPERINFO_PORT` are explicitly changed. The background entry `run_server.py` always runs with `debug=False`/`use_reloader=False`.

## Running as a Background Service (Windows)

Auto-start at logon via Task Scheduler (no third-party tools). Runs hidden (`pythonw.exe`), restarts on failure, works on battery; the built-in catch-up job (`check_and_catch_up`) backfills fetches missed while the machine was off.

```powershell
# Install/uninstall both require an elevated (Run as Administrator) PowerShell,
# because registering scheduled tasks is denied from non-elevated sessions on this machine.

# Install (registers 'PaperInfo' scheduled task and starts it immediately)
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1

# Uninstall (stops the background instance and removes the task)
powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1

# Restart (needed to pick up .env changes, e.g. a new LLM API key; no admin required)
powershell -ExecutionPolicy Bypass -File scripts\restart_server.ps1

# Check status
Get-ScheduledTask PaperInfo
```

The task runs `venv\Scripts\pythonw.exe run_server.py` (GUI subsystem → no console window) with the project root as working directory, trigger AtLogOn, principal = current user (Interactive). `run_server.py` is the background entry point: it forces `debug=False`/`use_reloader=False` and redirects `sys.stdout`/`sys.stderr` to `logs/server.log` before importing `app` (required under `pythonw`, where they start as `None`). Settings: restart 3× at 1-minute intervals on failure, no execution time limit, run on battery, `IgnoreNew` for duplicate instances.

## Architecture Overview

PaperInfo is a Flask-based paper research tool that automatically fetches academic papers from multiple sources (arXiv, DBLP) and displays them through a web interface.

### Core Components

1. **Flask Application (`app.py`)**: Entry point that initializes database, scheduler, and routes. Uses `create_app()` pattern with `register_routes()` defining all API endpoints.

2. **Database Models (`models.py`)**:
   - `Domain`: Research domains with keywords, arXiv categories, and CCF venues
   - `Paper`: Papers with deduplication via unique constraint on `(source, title)`

3. **Crawlers (`crawler/`)**:
   - `BaseCrawler`: Abstract base class with rate limiting (`_wait_for_rate_limit()`)
   - `ArxivCrawler`: Fetches from arXiv Atom API, parses with feedparser
   - `DBLPCrawler`: Fetches from DBLP JSON API
   - Each crawler's `search()` returns normalized paper dicts

4. **Scheduler (`scheduler.py`)**: APScheduler runs daily at configured hour (`Config.SCHEDULE_HOUR`). Calls `fetch_papers_for_domain()` for each enabled domain, then `_save_papers()` handles deduplication.

5. **Configuration (`config.py`)**: Central config with `DEFAULT_DOMAINS` for pre-seeded research areas.

### Data Flow

```
Config.DEFAULT_DOMAINS → Domain table → Scheduler triggers → Crawler.search() → _save_papers() → Paper table → Web display
```

## Adding New Research Domains

Edit `config.py` → add to `DEFAULT_DOMAINS`:

```python
{
    'name': 'Machine Learning',
    'keywords': ['neural network', 'deep learning', 'transformer'],
    'arxiv_categories': ['cs.AI', 'cs.LG'],
    'ccf_venues': ['NeurIPS', 'ICML', 'ICLR']
}
```

Or use API: `POST /api/domains` with JSON body.

## Adding New Data Sources

1. Create `crawler/newsource.py` inheriting from `BaseCrawler`
2. Implement `search(keywords, **kwargs)` returning list of paper dicts
3. Add import to `scheduler.py` and call in `fetch_papers_for_domain()`
4. Update domain schema if needed (add new categories/venues fields)

Paper dict format must match `Paper` model: `title`, `authors`, `abstract`, `source`, `source_id`, `year`, `venue`, `url`, `pdf_url`, `published_date`.

## Important Constraints

- **arXiv rate limit**: Must wait 3 seconds between requests (enforced by `BaseCrawler._wait_for_rate_limit()`)
- **Deduplication**: Papers checked via `Paper.exists_by_source_and_title(source, title)` before insertion
- **Timezone**: Scheduler uses `Asia/Shanghai` timezone
- **Python 3.13 compatibility**: Use `feedparser>=6.0.11`

## Database Location

SQLite database at `data/papers.db`. Recreated on app start via `db.create_all()`. Domains are seeded from `DEFAULT_DOMAINS` on first run.
