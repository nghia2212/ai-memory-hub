"""V3.1 additive business profile schema."""
BUSINESS_SQL = """
CREATE TABLE IF NOT EXISTS v31_business_profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 description TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v31_business_projects (
 business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 PRIMARY KEY(business_id,project_id)
);
CREATE TABLE IF NOT EXISTS v31_business_tools (
 business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
 tool_id TEXT NOT NULL REFERENCES v3_mcp_tools(id),
 policy TEXT NOT NULL CHECK(policy IN ('allow','approval','deny')),
 PRIMARY KEY(business_id,tool_id)
);
CREATE TABLE IF NOT EXISTS v31_business_versions (
 business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
 revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(business_id,revision)
);
"""
