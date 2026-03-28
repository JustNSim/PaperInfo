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
