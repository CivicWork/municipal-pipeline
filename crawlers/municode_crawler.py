"""
CivicWork Municipal Code Crawler
Recursively crawls a municipality's code of ordinances from Municode's public API
and stores it in a local SQLite database with full-text search.

Usage:
    python municode_crawler.py --state IL --municipality Elgin
    python municode_crawler.py --state IL --municipality Elgin --content  # also fetch section text
    python municode_crawler.py --search "liquor license"                  # search crawled codes
    python municode_crawler.py --search "sanctuary" --state IL            # search within a state
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MUNICODE_API_BASE = "https://api.municode.com"
DB_PATH = Path(__file__).parent / "civicwork.db"
RATE_LIMIT_SECONDS = 1.0  # be respectful
USER_AGENT = "CivicWork-Municipal-Crawler/0.1 (civic research; https://civicwork.ai)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("municode_crawler")

# ---------------------------------------------------------------------------
# HTML stripper
# ---------------------------------------------------------------------------

class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts).strip()


def strip_html(html):
    s = HTMLStripper()
    s.feed(html or "")
    text = s.get_text()
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    # Initialize schema if needed
    schema_path = Path(__file__).parent / "schema.sql"
    if schema_path.exists():
        db.executescript(schema_path.read_text())

    return db


def get_or_create_municipality(db, state_abbr, municipality_name):
    row = db.execute(
        "SELECT id, municode_client_id FROM municipalities WHERE state_abbr = ? AND municode_name = ?",
        (state_abbr.upper(), municipality_name),
    ).fetchone()
    if row:
        return row["id"], row["municode_client_id"]

    db.execute(
        "INSERT INTO municipalities (name, state_abbr, municode_name) VALUES (?, ?, ?)",
        (municipality_name, state_abbr.upper(), municipality_name),
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0], None

# ---------------------------------------------------------------------------
# Municode API
# ---------------------------------------------------------------------------

def api_get(path):
    url = f"{MUNICODE_API_BASE}{path}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        log.error(f"HTTP {e.code} for {url}")
        raise
    except URLError as e:
        log.error(f"URL error for {url}: {e}")
        raise


def resolve_client(state_abbr, municipality_name):
    """Find Municode's internal IDs for a municipality."""
    data = api_get(f"/Clients/stateAbbr?stateAbbr={state_abbr.upper()}")
    clients = data if isinstance(data, list) else []

    target = municipality_name.lower().strip()
    for client in clients:
        name = (client.get("ClientName") or client.get("clientName") or client.get("name") or "").strip()
        if name.lower() == target:
            client_id = client.get("ClientID") or client.get("clientId") or client.get("id")

            # Get product and job IDs
            content = api_get(f"/ClientContent/{client_id}")
            codes = content.get("codes", content if isinstance(content, list) else [])
            if not codes:
                raise ValueError(f"No codes found for {municipality_name}")

            product = codes[0] if isinstance(codes, list) else codes
            product_id = product.get("productId") or product.get("id")
            product_name = product.get("productName") or product.get("name")

            # Get latest job ID
            job_data = api_get(f"/Jobs/latest/{product_id}")
            job_id = job_data.get("Id") or job_data.get("jobId") or job_data.get("id")

            return {
                "client_id": client_id,
                "client_name": name,
                "product_id": product_id,
                "product_name": product_name,
                "job_id": job_id,
            }

    raise ValueError(f"Municipality '{municipality_name}' not found in {state_abbr}")


def browse_toc(job_id, product_id, node_id=None):
    """Browse table of contents. Returns list of child nodes."""
    if node_id:
        path = f"/codesToc/children?jobId={job_id}&productId={product_id}&nodeId={quote(node_id)}"
    else:
        path = f"/codesToc/children?jobId={job_id}&productId={product_id}"

    data = api_get(path)
    nodes = data if isinstance(data, list) else []
    return [
        {
            "id": n.get("Id") or n.get("id") or n.get("nodeId"),
            "heading": (n.get("Heading") or n.get("heading") or n.get("title") or "").strip(),
            "has_children": bool(n.get("HasChildren") or n.get("hasChildren") or n.get("numChildren", 0)),
        }
        for n in nodes
        if n.get("Id") or n.get("id") or n.get("nodeId")
    ]


def get_content(job_id, product_id, node_id):
    """Get the full text content of a section."""
    path = f"/CodesContent?jobId={job_id}&productId={product_id}&nodeId={quote(node_id)}"
    data = api_get(path)

    docs = data.get("Docs", [])
    # Try to find the exact doc matching this node
    target_doc = None
    for doc in docs:
        if doc.get("Id") == node_id:
            target_doc = doc
            break

    if target_doc:
        html = target_doc.get("Content", "")
        title = target_doc.get("Title", "")
        return strip_html(html), html, title

    # Fallback: concatenate all docs (less ideal)
    parts = []
    html_parts = []
    for doc in docs:
        content = doc.get("Content", "")
        if content:
            parts.append(strip_html(content))
            html_parts.append(content)

    return "\n\n".join(parts), "\n\n".join(html_parts), ""

# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

def crawl_toc_recursive(db, municipality_id, job_id, product_id, parent_node_id=None, depth=0):
    """Recursively crawl the table of contents and store in database."""
    time.sleep(RATE_LIMIT_SECONDS)

    try:
        nodes = browse_toc(job_id, product_id, parent_node_id)
    except Exception as e:
        log.error(f"Failed to browse TOC for node {parent_node_id}: {e}")
        return 0

    count = 0
    for node in nodes:
        node_id = node["id"]
        heading = node["heading"]
        has_children = node["has_children"]

        # Upsert the section
        db.execute("""
            INSERT INTO municode_sections (municipality_id, node_id, parent_node_id, heading, has_children, depth, toc_crawled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(municipality_id, node_id) DO UPDATE SET
                heading = excluded.heading,
                has_children = excluded.has_children,
                depth = excluded.depth,
                toc_crawled_at = excluded.toc_crawled_at,
                updated_at = datetime('now')
        """, (municipality_id, node_id, parent_node_id, heading, has_children, depth, datetime.now().isoformat()))
        db.commit()
        count += 1

        prefix = "  " * depth
        log.info(f"{prefix}{'📁' if has_children else '📄'} {heading}")

        if has_children:
            count += crawl_toc_recursive(db, municipality_id, job_id, product_id, node_id, depth + 1)

    return count


def crawl_content(db, municipality_id, job_id, product_id, limit=None):
    """Fetch full text for all leaf sections (no children) that haven't been crawled yet."""
    query = """
        SELECT id, node_id, heading FROM municode_sections
        WHERE municipality_id = ? AND has_children = 0 AND content_crawled_at IS NULL
        ORDER BY id
    """
    params = [municipality_id]
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    sections = db.execute(query, params).fetchall()
    total = len(sections)
    log.info(f"Fetching content for {total} sections...")

    for i, section in enumerate(sections):
        time.sleep(RATE_LIMIT_SECONDS)
        node_id = section["node_id"]
        log.info(f"[{i+1}/{total}] {section['heading']}")

        try:
            text, html, title = get_content(job_id, product_id, node_id)
            word_count = len(text.split()) if text else 0

            db.execute("""
                UPDATE municode_sections SET
                    content_text = ?,
                    content_html = ?,
                    content_length = ?,
                    word_count = ?,
                    content_crawled_at = ?,
                    crawl_error = NULL,
                    updated_at = datetime('now')
                WHERE id = ?
            """, (text, html, len(text) if text else 0, word_count, datetime.now().isoformat(), section["id"]))

            # Update FTS index
            db.execute("""
                INSERT INTO municode_fts(rowid, heading, content_text)
                VALUES (?, ?, ?)
            """, (section["id"], section["heading"], text))

            db.commit()

        except Exception as e:
            log.error(f"  Error fetching {node_id}: {e}")
            db.execute(
                "UPDATE municode_sections SET crawl_error = ? WHERE id = ?",
                (str(e), section["id"]),
            )
            db.commit()

    return total

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_codes(db, query, state_abbr=None, municipality_name=None):
    """Full-text search across all crawled municipal codes."""
    sql = """
        SELECT
            m.name AS municipality,
            m.state_abbr AS state,
            s.heading,
            s.node_id,
            s.depth,
            snippet(municode_fts, 1, '>>>', '<<<', '...', 40) AS snippet
        FROM municode_fts
        JOIN municode_sections s ON s.id = municode_fts.rowid
        JOIN municipalities m ON m.id = s.municipality_id
        WHERE municode_fts MATCH ?
    """
    params = [query]

    if state_abbr:
        sql += " AND m.state_abbr = ?"
        params.append(state_abbr.upper())

    if municipality_name:
        sql += " AND m.municode_name = ?"
        params.append(municipality_name)

    sql += " ORDER BY rank LIMIT 50"

    results = db.execute(sql, params).fetchall()
    return results

# ---------------------------------------------------------------------------
# Export (for LLM agent consumption)
# ---------------------------------------------------------------------------

def export_section(db, node_id=None, heading_search=None, state_abbr=None, municipality_name=None, format="text"):
    """
    Export a section and its children as a single text block ready for LLM consumption.
    Supports output as plain text, markdown, or JSON.
    """
    # Find the target section(s)
    if node_id:
        where = "s.node_id = ?"
        params = [node_id]
    elif heading_search:
        where = "s.heading LIKE ?"
        params = [f"%{heading_search}%"]
    else:
        raise ValueError("Provide --node-id or --heading to export")

    # Get the target section and all its descendants
    sql = f"""
        WITH RECURSIVE descendants AS (
            SELECT s.id, s.node_id, s.parent_node_id, s.heading, s.depth, s.content_text, s.has_children
            FROM municode_sections s
            JOIN municipalities m ON m.id = s.municipality_id
            WHERE {where}
            {"AND m.state_abbr = ?" if state_abbr else ""}
            {"AND m.municode_name = ?" if municipality_name else ""}

            UNION ALL

            SELECT s.id, s.node_id, s.parent_node_id, s.heading, s.depth, s.content_text, s.has_children
            FROM municode_sections s
            INNER JOIN descendants d ON s.parent_node_id = d.node_id
        )
        SELECT * FROM descendants ORDER BY id
    """
    if state_abbr:
        params.append(state_abbr.upper())
    if municipality_name:
        params.append(municipality_name)

    sections = db.execute(sql, params).fetchall()

    if not sections:
        return None, 0

    if format == "json":
        import json as json_mod
        items = []
        for s in sections:
            items.append({
                "node_id": s["node_id"],
                "heading": s["heading"],
                "depth": s["depth"],
                "content": s["content_text"] or "",
            })
        return json_mod.dumps(items, indent=2), len(sections)

    # Text or markdown format
    parts = []
    for s in sections:
        heading = s["heading"]
        content = s["content_text"] or ""
        depth = s["depth"]

        if format == "markdown":
            level = min(depth + 1, 6)
            prefix = "#" * level
            parts.append(f"{prefix} {heading}")
        else:
            indent = "  " * depth
            parts.append(f"{indent}{heading}")

        if content:
            parts.append(content)
        parts.append("")  # blank line between sections

    return "\n".join(parts), len(sections)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def print_stats(db, municipality_id=None):
    """Print crawl statistics."""
    if municipality_id:
        total = db.execute(
            "SELECT COUNT(*) FROM municode_sections WHERE municipality_id = ?",
            (municipality_id,)
        ).fetchone()[0]
        with_content = db.execute(
            "SELECT COUNT(*) FROM municode_sections WHERE municipality_id = ? AND content_crawled_at IS NOT NULL",
            (municipality_id,)
        ).fetchone()[0]
        total_words = db.execute(
            "SELECT COALESCE(SUM(word_count), 0) FROM municode_sections WHERE municipality_id = ?",
            (municipality_id,)
        ).fetchone()[0]
        print(f"\n  Sections in TOC: {total}")
        print(f"  Sections with content: {with_content}")
        print(f"  Total words: {total_words:,}")
    else:
        munis = db.execute("SELECT COUNT(DISTINCT municipality_id) FROM municode_sections").fetchone()[0]
        sections = db.execute("SELECT COUNT(*) FROM municode_sections").fetchone()[0]
        with_content = db.execute("SELECT COUNT(*) FROM municode_sections WHERE content_crawled_at IS NOT NULL").fetchone()[0]
        total_words = db.execute("SELECT COALESCE(SUM(word_count), 0) FROM municode_sections").fetchone()[0]
        print(f"\n  Municipalities crawled: {munis}")
        print(f"  Total sections: {sections}")
        print(f"  Sections with content: {with_content}")
        print(f"  Total words: {total_words:,}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CivicWork Municipal Code Crawler")
    parser.add_argument("--state", help="Two-letter state abbreviation (e.g., IL)")
    parser.add_argument("--municipality", help="Municipality name as it appears in Municode")
    parser.add_argument("--content", action="store_true", help="Also fetch section content (slower)")
    parser.add_argument("--content-limit", type=int, help="Max sections to fetch content for")
    parser.add_argument("--search", help="Search across crawled codes")
    parser.add_argument("--export", help="Export a section tree for LLM consumption (by heading keyword)")
    parser.add_argument("--node-id", help="Export a specific section by node ID")
    parser.add_argument("--format", choices=["text", "markdown", "json"], default="text", help="Export format (default: text)")
    parser.add_argument("--stats", action="store_true", help="Show crawl statistics")
    parser.add_argument("--rate-limit", type=float, default=1.0, help="Seconds between API calls (default: 1.0)")
    args = parser.parse_args()

    global RATE_LIMIT_SECONDS
    RATE_LIMIT_SECONDS = args.rate_limit

    db = get_db()

    # Search mode
    if args.search:
        results = search_codes(db, args.search, args.state, args.municipality)
        if not results:
            print(f"No results for '{args.search}'")
            return

        print(f"\nSearch results for '{args.search}' ({len(results)} matches):\n")
        for r in results:
            print(f"  {r['municipality']}, {r['state']} — {r['heading']}")
            print(f"    ...{r['snippet']}...")
            print()
        return

    # Export mode
    if args.export or args.node_id:
        output, count = export_section(
            db,
            node_id=args.node_id,
            heading_search=args.export,
            state_abbr=args.state,
            municipality_name=args.municipality,
            format=args.format,
        )
        if not output:
            print("No sections found matching that criteria.")
            return
        print(f"# Exported {count} sections\n")
        print(output)
        return

    # Stats mode
    if args.stats:
        print_stats(db)
        return

    # Crawl mode
    if not args.state or not args.municipality:
        parser.error("--state and --municipality are required for crawling")

    log.info(f"Resolving {args.municipality}, {args.state} on Municode...")
    client = resolve_client(args.state, args.municipality)
    log.info(f"Found: {client['client_name']} (client_id={client['client_id']}, product_id={client['product_id']})")

    municipality_id, _ = get_or_create_municipality(db, args.state, args.municipality)

    # Update municipality with Municode IDs
    db.execute(
        "UPDATE municipalities SET municode_client_id = ?, updated_at = datetime('now') WHERE id = ?",
        (client["client_id"], municipality_id),
    )
    db.commit()

    # Create crawl job
    db.execute(
        "INSERT INTO crawl_jobs (municipality_id, source, status, started_at) VALUES (?, 'municode', 'running', ?)",
        (municipality_id, datetime.now().isoformat()),
    )
    db.commit()
    job_row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Phase 1: Crawl table of contents
    log.info(f"Crawling table of contents for {args.municipality}, {args.state}...")
    toc_count = crawl_toc_recursive(
        db, municipality_id, client["job_id"], client["product_id"]
    )
    log.info(f"TOC crawl complete: {toc_count} sections found")

    # Phase 2: Fetch content (if requested)
    content_count = 0
    if args.content:
        log.info("Fetching section content...")
        content_count = crawl_content(
            db, municipality_id, client["job_id"], client["product_id"],
            limit=args.content_limit,
        )
        log.info(f"Content crawl complete: {content_count} sections fetched")

    # Update crawl job
    db.execute("""
        UPDATE crawl_jobs SET
            status = 'complete',
            total_nodes = ?,
            crawled_nodes = ?,
            completed_at = ?
        WHERE id = ?
    """, (toc_count, content_count, datetime.now().isoformat(), job_row_id))
    db.commit()

    print_stats(db, municipality_id)
    print(f"\nDatabase: {DB_PATH}")
    print(f"To search: python {__file__} --search 'liquor license'")


if __name__ == "__main__":
    main()
