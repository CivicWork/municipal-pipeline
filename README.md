# Municipal Pipeline

Open data infrastructure for municipal law. Crawls, indexes, and enables full-text search across municipal codes of ordinances from publicly available sources.

Built by [CivicWork, Inc.](https://civicwork.ai) — open-source AI infrastructure for local government.

## Why This Exists

Municipal ordinances are public law — they belong to the people. But accessing them programmatically is gated behind vendor platforms that increasingly restrict API access. A policy analyst who wants to search for "sanctuary city" ordinances across 3,300+ municipalities can't do it without an enterprise vendor contract.

This pipeline makes public law actually accessible:
- **Crawl** municipal codes from public sources
- **Index** them with full-text search locally
- **Export** sections for AI agent consumption (text, markdown, JSON)
- **Search** across municipalities by keyword — the capability vendors block

## Quick Start

```bash
# Crawl a municipality's table of contents (~5 min)
python3 crawlers/municode_crawler.py --state IL --municipality Elgin

# Crawl with full section content (~50 min for a typical city)
python3 crawlers/municode_crawler.py --state IL --municipality Elgin --content

# Search across all crawled codes
python3 crawlers/municode_crawler.py --search "liquor license"
python3 crawlers/municode_crawler.py --search "sanctuary" --state IL

# Export a chapter for LLM consumption
python3 crawlers/municode_crawler.py --export "Alcoholic Liquor" --format markdown
python3 crawlers/municode_crawler.py --export "Procurements" --format json

# Check crawl progress
python3 crawlers/municode_crawler.py --stats
```

## What's Here

```
municipal-pipeline/
├── crawlers/
│   └── municode_crawler.py    # Recursive Municode crawler with FTS search
├── schema.sql                 # Database schema (SQLite → PostgreSQL ready)
├── docs/                      # Internal docs (not committed)
└── README.md
```

## Database Schema

The schema supports the full intelligence pipeline, not just Municode:

| Table | Purpose | Status |
|---|---|---|
| `municipalities` | Universal join point — population, coordinates, vendor IDs, signals | Active |
| `municode_sections` | Code of ordinances document tree with content | Active |
| `municode_fts` | Full-text search index across all crawled codes | Active |
| `legistar_meetings` | Meeting agendas and minutes | Schema ready |
| `legistar_agenda_items` | Individual agenda items with legislation links | Schema ready |
| `events` | Unified event stream across all data sources | Schema ready |
| `intelligence` | LLM-enriched analysis of events | Schema ready |
| `crawl_jobs` | Crawl progress tracking | Active |

## Data Sources

### Currently Implemented
- **Municode** — 3,300+ municipal codes of ordinances via public API (browse, read, list)

### Planned
- **Legistar** — Meeting agendas, minutes, legislation via WebMCP tools
- **Census Bureau** — Population, geography, fiscal data
- **State comptroller databases** — Per-capita spending, technology budgets
- **Municipal websites** — AI policies, innovation offices, job postings, RFPs

## Legal

Municipal ordinances are public law and cannot be copyrighted (*Georgia v. Public.Resource.Org*, 2020). This tool accesses publicly available data through the same API endpoints used by the vendor's own website.

## Related Projects

- [CivicWork WebMCP](https://github.com/CivicWork/municipal-webmcp) — Browser AI agent tools for Legistar and Municode
- [CivicWork Plugin](https://github.com/CivicWork/municipal-governance) — Claude plugin for municipal governance
- [MunicipalMCP](https://github.com/CivicWork/municipal-mcp) — Python MCP server for Municode API

## License

Apache 2.0 — see [LICENSE](LICENSE).
