-- CivicWork Municipal Intelligence Pipeline
-- Database schema designed for SQLite (prototype) → Supabase PostgreSQL (production)
-- Supports: Municode codes, Legistar meetings, Census data, job postings, vendor contracts

-- ============================================================================
-- CORE: Municipalities (the universal join point for all data sources)
-- ============================================================================

CREATE TABLE IF NOT EXISTS municipalities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    state_abbr TEXT NOT NULL,
    county TEXT,
    population INTEGER,
    latitude REAL,
    longitude REAL,

    -- Vendor platform identifiers (populated as discovered)
    municode_client_id INTEGER,
    municode_name TEXT,          -- name as it appears in Municode (may differ from canonical name)
    legistar_subdomain TEXT,     -- e.g., 'countyofkane', 'chicago'

    -- Scoring (pipeline Agent 3: Briefer)
    composite_score REAL DEFAULT 0,
    tier INTEGER,                -- 1=grant target, 2=pilot, 3=pipeline
    last_scored_at TEXT,

    -- Signals (populated by crawlers and analysts)
    has_open_data_portal BOOLEAN DEFAULT 0,
    has_ai_policy BOOLEAN DEFAULT 0,
    has_innovation_officer BOOLEAN DEFAULT 0,
    has_sanctuary_policy BOOLEAN DEFAULT 0,
    has_dei_ordinance BOOLEAN DEFAULT 0,
    known_vendors TEXT,          -- JSON array of vendor names

    website TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),

    UNIQUE(name, state_abbr)
);

-- ============================================================================
-- MUNICODE: Code of Ordinances (recursive document tree)
-- ============================================================================

CREATE TABLE IF NOT EXISTS municode_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id),
    node_id TEXT NOT NULL,       -- Municode's internal ID (e.g., 'MUCO_TIT6BULIRE_CH6.06ALLIDE')
    parent_node_id TEXT,         -- parent's node_id (NULL for root-level sections)
    heading TEXT NOT NULL,       -- section heading as it appears in Municode
    has_children BOOLEAN DEFAULT 0,
    depth INTEGER DEFAULT 0,     -- 0=title, 1=chapter, 2=section, 3=subsection

    -- Content (populated by get_section_content)
    content_text TEXT,           -- plain text of the ordinance
    content_html TEXT,           -- original HTML (optional, for preservation)
    content_length INTEGER,      -- character count
    word_count INTEGER,

    -- Metadata
    ordinance_refs TEXT,         -- JSON array of ordinance numbers referenced
    effective_date TEXT,
    last_amended TEXT,

    -- Crawl tracking
    toc_crawled_at TEXT,         -- when we browsed this node's children
    content_crawled_at TEXT,     -- when we fetched this node's full text
    crawl_error TEXT,            -- last error if any

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),

    UNIQUE(municipality_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_municode_sections_municipality ON municode_sections(municipality_id);
CREATE INDEX IF NOT EXISTS idx_municode_sections_parent ON municode_sections(parent_node_id);
CREATE INDEX IF NOT EXISTS idx_municode_sections_heading ON municode_sections(heading);

-- Full-text search index (the key capability Municode blocks)
CREATE VIRTUAL TABLE IF NOT EXISTS municode_fts USING fts5(
    heading,
    content_text,
    content='municode_sections',
    content_rowid='id'
);

-- ============================================================================
-- LEGISTAR: Meetings and Agenda Items
-- ============================================================================

CREATE TABLE IF NOT EXISTS legistar_meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id),
    meeting_id TEXT NOT NULL,    -- Legistar's numeric ID
    meeting_guid TEXT,
    name TEXT NOT NULL,          -- committee/body name
    date TEXT,
    time TEXT,
    location TEXT,
    cancelled BOOLEAN DEFAULT 0,
    agenda_status TEXT,
    minutes_status TEXT,
    agenda_url TEXT,
    minutes_url TEXT,
    detail_url TEXT,
    crawled_at TEXT DEFAULT (datetime('now')),

    UNIQUE(municipality_id, meeting_id)
);

CREATE TABLE IF NOT EXISTS legistar_agenda_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES legistar_meetings(id),
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id),
    file_number TEXT,
    legislation_id TEXT,
    legislation_guid TEXT,
    type TEXT,                   -- Resolution, Ordinance, Report, etc.
    title TEXT,
    action TEXT,
    result TEXT,
    legislation_url TEXT,
    crawled_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agenda_items_meeting ON legistar_agenda_items(meeting_id);
CREATE INDEX IF NOT EXISTS idx_agenda_items_type ON legistar_agenda_items(type);

-- ============================================================================
-- EVENTS: Watcher output (unified event stream across all data sources)
-- ============================================================================

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id INTEGER REFERENCES municipalities(id),
    source TEXT NOT NULL,        -- 'municode', 'legistar', 'job_posting', 'website', 'procurement'
    event_type TEXT NOT NULL,    -- 'code_change', 'meeting_scheduled', 'job_posted', 'rfp_posted', etc.
    raw_content TEXT,
    url TEXT,
    timestamp TEXT DEFAULT (datetime('now')),
    analyzed BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_municipality ON events(municipality_id);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_analyzed ON events(analyzed);

-- ============================================================================
-- INTELLIGENCE: Analyst output (LLM-enriched event analysis)
-- ============================================================================

CREATE TABLE IF NOT EXISTS intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id),
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id),
    classification TEXT,         -- 'tech_procurement', 'ai_policy', 'vendor_contract', etc.
    vendors_mentioned TEXT,      -- JSON array
    dollar_amount REAL,
    sentiment TEXT,              -- 'positive', 'negative', 'neutral', 'frustration'
    department TEXT,
    summary TEXT,
    score_impact REAL,
    analyzed_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================================
-- CRAWL JOBS: Track crawl progress across municipalities
-- ============================================================================

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_id INTEGER NOT NULL REFERENCES municipalities(id),
    source TEXT NOT NULL,        -- 'municode', 'legistar', 'website'
    status TEXT DEFAULT 'pending', -- 'pending', 'running', 'complete', 'failed'
    total_nodes INTEGER DEFAULT 0,
    crawled_nodes INTEGER DEFAULT 0,
    failed_nodes INTEGER DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
