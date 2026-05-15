#!/usr/bin/env python3
"""
CV → Notion Student Talent Pipeline

Usage:
  python ingest_cv.py path/to/cv.pdf
  python ingest_cv.py path/to/cv.pdf --email student@example.com  # also pulls ZG engagement data

Requirements: source .env first (NOTION_API_KEY, ANTHROPIC_API_KEY, and optionally
METABASE_URL + METABASE_API_KEY for engagement enrichment).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic
from notion_client import Client as NotionClient

# ── Config ────────────────────────────────────────────────────────────────────

NOTION_DATABASE_ID = "dd82b6dcfbbe409bb06e36d00f0f1419"

UNIVERSITY_OPTIONS = [
    "Oxford", "Cambridge", "Imperial", "UCL", "LSE",
    "Durham", "Exeter", "Bristol", "Manchester", "Warwick", "Edinburgh",
    "Other Russell Group", "Other",
]

GRADE_OPTIONS = ["First (1st)", "2:1", "2:2", "Third", "Pass", "TBC"]

SECTOR_OPTIONS = ["Finance", "Law", "Consulting", "Tech", "Media", "Public Sector", "FMCG", "Other"]

EXTRACTION_PROMPT = f"""You are extracting structured information from a student CV for a talent pipeline database at Zero Gravity, a UK social mobility careers platform.

Extract the following fields and return ONLY valid JSON (no markdown, no commentary):

{{
  "name": "Full name",
  "university": "University name — must be one of: {', '.join(UNIVERSITY_OPTIONS)}",
  "degree_subject": "Degree title and subject(s), e.g. BSc Computer Science",
  "predicted_final_grade": "One of exactly: {', '.join(GRADE_OPTIONS)}",
  "a_level_grades": "A-level subjects and grades as a single string, e.g. Maths A*, Economics A, History B",
  "gcse_grades": "Notable GCSEs or overall summary, e.g. 9 GCSEs grades 7-9 including Maths and English",
  "school_context": "School type (state/independent), any visible disadvantage indicators, and academic performance relative to context",
  "interests": "Interests, hobbies, societies, volunteering, leadership roles as a string",
  "partner_fit": ["array of relevant sectors from: {', '.join(SECTOR_OPTIONS)}"],
  "notes": "2-3 sentence summary of candidate strengths and any areas to develop"
}}

If a field is not present in the CV, use an empty string (or empty array for partner_fit).
For university, if you can't match exactly, use 'Other Russell Group' for RG universities not listed, or 'Other' otherwise.
"""

# ── CV text extraction ─────────────────────────────────────────────────────────

def extract_cv_text(filepath: Path) -> str:
    suffix = filepath.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfplumber
            text = ""
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
            return text.strip()
        except ImportError:
            sys.exit("Missing dependency: pip install pdfplumber")
    elif suffix in (".docx",):
        try:
            from docx import Document
            doc = Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs).strip()
        except ImportError:
            sys.exit("Missing dependency: pip install python-docx")
    elif suffix in (".txt", ".md"):
        return filepath.read_text().strip()
    else:
        sys.exit(f"Unsupported file type: {suffix}. Use PDF, DOCX, or TXT.")


# ── Claude extraction ──────────────────────────────────────────────────────────

def parse_cv_with_claude(cv_text: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY not set — run: source .env")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": f"{EXTRACTION_PROMPT}\n\nCV TEXT:\n{cv_text}"}],
    )
    raw = message.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())


# ── Metabase engagement lookup ─────────────────────────────────────────────────

def fetch_zg_engagement(email: str) -> dict:
    """Look up a user's ZG platform stats by email via Metabase."""
    mb_url = os.environ.get("METABASE_URL")
    mb_key = os.environ.get("METABASE_API_KEY")
    if not mb_url or not mb_key:
        print("  (skipping Metabase lookup — METABASE_URL/API_KEY not set)")
        return {}

    query = f"""
    SELECT
      u.engagement_score,
      COALESCE(mt.successful_call_logs_count, 0) AS mentoring_calls,
      COALESCE(apps.job_count, 0)                AS jobs_applied
    FROM mentor_tracks mt
    JOIN users u ON u.id = mt.user_id
    LEFT JOIN (
      SELECT user_id, COUNT(*) AS job_count
      FROM partner_application_trackers
      GROUP BY user_id
    ) apps ON apps.user_id = u.id
    WHERE u.email = '{email}'
      AND mt.archived_at IS NULL
    LIMIT 1
    """

    result = subprocess.run(
        [
            "curl", "-s",
            "-H", f"x-api-key: {mb_key}",
            "-H", "Content-Type: application/json",
            f"{mb_url}/api/dataset",
            "-d", json.dumps({"database": 2, "type": "native", "native": {"query": query}}),
        ],
        capture_output=True, text=True,
    )

    try:
        data = json.loads(result.stdout)
        rows = data.get("data", {}).get("rows", [])
        if rows:
            row = rows[0]
            return {
                "engagement_score": row[0],
                "mentoring_calls": int(row[1] or 0),
                "jobs_applied": int(row[2] or 0),
            }
    except Exception as e:
        print(f"  (Metabase lookup failed: {e})")
    return {}


# ── Notion page creation ───────────────────────────────────────────────────────

def rt(text: str) -> list:
    """Notion rich_text block."""
    return [{"text": {"content": str(text)[:2000]}}]


def create_notion_record(data: dict, engagement: dict) -> str:
    notion_key = os.environ.get("NOTION_API_KEY")
    if not notion_key:
        sys.exit("NOTION_API_KEY not set — add it to .env")

    notion = NotionClient(auth=notion_key)

    university = data.get("university", "Other")
    if university not in UNIVERSITY_OPTIONS:
        university = "Other"

    grade = data.get("predicted_final_grade", "TBC")
    if grade not in GRADE_OPTIONS:
        grade = "TBC"

    partner_fit = [s for s in data.get("partner_fit", []) if s in SECTOR_OPTIONS]

    properties = {
        "Name":                        {"title": [{"text": {"content": data.get("name", "Unknown")}}]},
        "University":                  {"select": {"name": university}},
        "Degree Subject":              {"rich_text": rt(data.get("degree_subject", ""))},
        "Predicted / Final Grade":     {"select": {"name": grade}},
        "A-Level Grades":              {"rich_text": rt(data.get("a_level_grades", ""))},
        "GCSE Grades":                 {"rich_text": rt(data.get("gcse_grades", ""))},
        "School Context":              {"rich_text": rt(data.get("school_context", ""))},
        "Interests & Extracurriculars":{"rich_text": rt(data.get("interests", ""))},
        "Notes":                       {"rich_text": rt(data.get("notes", ""))},
        "Status":                      {"select": {"name": "New"}},
        "Partner Fit":                 {"multi_select": [{"name": s} for s in partner_fit]},
    }

    if engagement.get("engagement_score") is not None:
        properties["ZG Engagement Score"] = {"number": engagement["engagement_score"]}
    if engagement.get("mentoring_calls") is not None:
        properties["Mentoring Calls"] = {"number": engagement["mentoring_calls"]}
    if engagement.get("jobs_applied") is not None:
        properties["Jobs Applied"] = {"number": engagement["jobs_applied"]}

    page = notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=properties,
    )
    return page["url"]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parse a student CV and add them to the Notion talent pipeline."
    )
    parser.add_argument("cv_file", help="Path to CV (PDF, DOCX, or TXT)")
    parser.add_argument(
        "--email", "-e",
        help="Student's ZG account email — enables Metabase engagement enrichment",
        default=None,
    )
    args = parser.parse_args()

    filepath = Path(args.cv_file)
    if not filepath.exists():
        sys.exit(f"File not found: {filepath}")

    print(f"\n📄  Reading CV: {filepath.name}")
    cv_text = extract_cv_text(filepath)
    if not cv_text:
        sys.exit("Could not extract text from CV — is the file readable?")

    print("🤖  Extracting details with Claude...")
    data = parse_cv_with_claude(cv_text)

    print(f"\n    Name:       {data.get('name', '—')}")
    print(f"    University: {data.get('university', '—')}")
    print(f"    Degree:     {data.get('degree_subject', '—')}")
    print(f"    Grade:      {data.get('predicted_final_grade', '—')}")
    print(f"    Sectors:    {', '.join(data.get('partner_fit', [])) or '—'}")

    engagement = {}
    if args.email:
        print(f"\n📊  Looking up ZG engagement for {args.email}...")
        engagement = fetch_zg_engagement(args.email)
        if engagement:
            print(f"    Engagement score: {engagement.get('engagement_score', '—')}")
            print(f"    Mentoring calls:  {engagement.get('mentoring_calls', 0)}")
            print(f"    Jobs applied:     {engagement.get('jobs_applied', 0)}")

    print("\n📝  Creating Notion record...")
    url = create_notion_record(data, engagement)
    print(f"\n✅  Done! View in Notion:\n    {url}\n")


if __name__ == "__main__":
    main()
