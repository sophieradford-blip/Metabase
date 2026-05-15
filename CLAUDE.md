# Metabase — Zero Gravity Production Database (DB 2)

Read-only SQL access to the **Zero Gravity careers/mentorship platform** production database via Metabase API. This is the *main* Zero Gravity product (mentorship matching, jobs board, partner hubs, community) — **NOT** the Orbit AI Tutor. For the Orbit DB see `metabase.md` (DB 38).

---

## Connection

- **Base URL:** `$METABASE_URL` (from `.env`)
- **API Key:** `$METABASE_API_KEY` (from `.env`)
- **Database ID:** `2` (Zero Gravity main platform — Postgres)
- **Auth header:** `x-api-key: $METABASE_API_KEY`

Must `source .env` before running queries.

> **PII warning.** This DB holds personal data for ~193k users: real names, emails, phone numbers, encrypted password tokens, IP addresses, geo data, intercom IDs. The auto-mode classifier will block reads from the `users` table even for non-PII columns. Prefer aggregates (`COUNT`, `GROUP BY`) and join-only queries. Never `SELECT *` from `users`, `login_activities`, `partner_users`, `telephone_verification_requests`, or `connections`. If you need an individual row, ask the user first.

---

## Running a Query

```bash
source /Users/danielstpaul/Projects/orbit/.env && curl -s \
  -H "x-api-key: $METABASE_API_KEY" \
  -H "Content-Type: application/json" \
  "$METABASE_URL/api/dataset" \
  -d '{
    "database": 2,
    "type": "native",
    "native": { "query": "SELECT COUNT(*) FROM users" }
  }'
```

Response shape: `{ "status": "completed", "data": { "cols": [...], "rows": [...] } }`

---

## Domain Model

The product is built around four overlapping concerns:

| Concern | Core tables |
|---------|-------------|
| **Identity & auth** | `users`, `admins`, `partner_users`, `login_activities`, `connections` |
| **Mentorship** | `mentor_tracks`, `mentee_tracks`, `mentoring_match_requests`, `mentoring_matches`, `feedback_records`, `call_logs`, `connections` |
| **Careers & jobs** | `jobs`, `companies`, `partner_companies`, `job_levels`, `job_locations`, `apprenticeship_types`, `partner_application_trackers` |
| **Community & content** | `community_entries`, `community_spaces`, `community_reactions`, `mentoring_resources`, `learning_content_pathways` |
| **Partner hubs** | `partner_hubs` (polymorphic `hubable_type`), `partner_schools`, `partner_universities`, `partner_companies`, `partner_mats`, `partner_local_authorities` |
| **In-product AI** | `ai_chats`, `ai_conversations`, `ai_messages`, `ai_models`, `ai_entry_points`, `ai_tool_calls` (internal ZG AI feature — separate from Orbit) |

Many state-bearing tables use AASM (`aasm_state` string column) — `mentor_tracks`, `mentee_tracks`, `mentoring_match_requests`. Soft-delete is via `archived_at` / `deleted_at` rather than row deletion.

---

## Key Tables & Columns

### users
The central identity table. Devise-backed. `role` distinguishes mentees/mentors/etc.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| email | varchar | **PII** — confirmed user email |
| unconfirmed_email | varchar | **PII** — pending change |
| first_name, last_name, preferred_name | varchar | **PII** |
| telephone_prefix_country_code, telephone_number | varchar | **PII** |
| encrypted_password, reset_password_token, confirmation_token, otp_secret | varchar | **Secrets — never select** |
| role | varchar | mentor/mentee/etc. |
| confirmed_at, telephone_verified_at | timestamp | Verification gates |
| archived_at | timestamp | Soft delete |
| banned, banned_at, banned_reason | bool/ts/varchar | Moderation state |
| zero_gravity_background | bool | Eligibility flag for the social-mobility programme |
| scholar, scholarship_year | bool/varchar | Scholarship cohort |
| apprenticeships | bool | Interested in apprenticeships |
| mentor_loop | bool | Mentor enrolment flag |
| profile_information, profile_urls, notification_preferences, flipper_groups | jsonb | |
| engagement_score | integer | Computed engagement |
| sign_in_count, current_sign_in_at, last_sign_in_at, last_seen_at | int/ts | Activity |
| current_sign_in_ip, last_sign_in_ip | varchar | **PII** |
| invited_by_type, invited_by_id, invited_by_entity_type, invited_by_entity_id | polymorphic | Invitation lineage |
| welcome_checklist_completed_at, uni_transition_state, uni_transition_completed_at | | Lifecycle stages |
| created_at, updated_at | timestamp | |

### mentor_tracks / mentee_tracks
Per-user mentorship participation state. One row per (user, role) — a user can be both mentor and mentee.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| user_id | bigint | FK → users |
| aasm_state | varchar | Current state (e.g. `onboarding`, `awaiting_match`, `matched`, `archived`) |
| eligible, eligible_at, ineligible_at | bool/ts | Eligibility lifecycle |
| accepting_matches, accept_multiple_mentees | bool | Matching preferences |
| accepted_mentoring_agreement | bool | Legal gate |
| archived_at | timestamp | Soft delete |
| created_at, updated_at | timestamp | |

### mentoring_match_requests
Pending match proposals between a mentor and a mentee. Becomes a `mentoring_matches` row on acceptance.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| type | varchar | STI (e.g. career vs general mentorship) |
| mentee_id, mentor_id | bigint | FKs → users |
| aasm_state | varchar | `pending`, `accepted`, `rejected`, `expired` |
| score, score_criteria, recommendation_information | numeric/jsonb | Matching algorithm output |
| accepted_at, rejected_at, expired_at, expiring_at | timestamp | State transitions |
| rejected_reason, expired_reason | varchar | |
| requested_by_type, requested_by_id | polymorphic | Who initiated the request |
| cycle_key, legacy_match_id | uuid | |
| created_at, updated_at | timestamp | |

### mentoring_matches
An accepted, active (or formerly active) mentor–mentee pairing.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| type | varchar | STI |
| connection_id | bigint | FK → connections (DM channel) |
| mentee_id, mentor_id | bigint | FKs → users |
| mentoring_match_request_id | bigint | FK |
| active | bool | True until ended |
| ended_at, ended_reason, ended_by, ended_context | ts/varchar | Termination metadata |
| successful_call_logs_count | integer | Counter cache over `call_logs` |
| acknowledged_at, sent_match_feedback_at | timestamp | |
| created_at, updated_at | timestamp | |

### connections
Persistent DM channel between two users. Created on match acceptance; `messages_count` counter cache.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| user_one_id, user_two_id | bigint | FKs → users (unordered pair) |
| blocked | bool | |
| messages_count | integer | Counter cache |
| legacy_match_id | uuid | |
| created_at, updated_at | timestamp | |

### feedback_records
Polymorphic feedback bag — STI via `type`. Used for call feedback, match feedback, etc.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| type | varchar | STI |
| rating | integer | 1-5 typically |
| body | varchar | Free text |
| call_log_id, mentoring_match_id | bigint | FKs |
| mentee_match_entity_id, mentor_match_entity_id | bigint | FKs → match_entities |
| user_id | bigint | Author |
| created_at, updated_at | timestamp | |

### jobs
Jobs / opportunities board.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| title, description | varchar | |
| company_id | bigint | FK → companies |
| apply_by | timestamp | Deadline |
| min_salary, max_salary, salary_frequency | numeric/varchar | |
| min_annual_bonus, max_annual_bonus | numeric | |
| application_url, application_reference, how_to_apply | varchar/text | |
| zero_gravity_take, week_description, skills_description, perks | text | Editorial copy |
| duration | varchar | |
| job_level_id, apprenticeship_type_id | bigint | FKs |
| year_group_requirements | jsonb | |
| school_student_year_requirement | integer | |
| featured, featured_message, featured_start_date, featured_end_date | bool/text/ts | Featuring |
| state, draft_at, active_at, closed_at, published_at | varchar/ts | Lifecycle |
| active | bool | Computed-ish |
| zg_exclusive | bool | Exclusive listing |
| user_submitted, submitted_by_type, submitted_by_id | bool/polymorphic | UGC jobs |
| closed_by_type, closed_by_id | polymorphic | |
| created_at, updated_at | timestamp | |

### companies
Employer profiles for jobs + mentorship.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| name, country, company_url, one_liner | varchar | |
| zero_gravity_take, get_to_know, sets_them_apart | text | Editorial copy |
| logo_data, icon_data, promotional_image_data | jsonb | Shrine attachments |
| verified, partner | bool | Verification + partnership flags |
| employee_count, active_employees_count | integer | |
| can_mentor_school_students, featured_in_mentoring_directory, has_legacy_matches | bool | |
| created_at, updated_at | timestamp | |

### schools
UK schools master list. Sourced from gov.uk + manually augmented. Used for `partner_schools.hubable_id` and on user profiles.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| urn | varchar | Unique reference number from gov.uk |
| name, display_name, town, county, region, local_authority | varchar | |
| nation | varchar | England/Scotland/Wales/NI |
| school_type, gender, trust_name | varchar | |
| ofsted_rating | varchar | |
| a_level_point_score, gcse_average, attainment8_score, progress8_score | numeric | Performance |
| pupil_premium, free_school_meal, idaci, english_as_an_additional_language | double | Demographic indicators |
| sixth_form, secondary_school | bool | |
| capacity | integer | |
| closed_at | timestamp | Soft state |
| can_access_career_mentoring | bool | Programme eligibility |
| force_eligibility, force_eligibility_changed_at, force_eligibility_reason, force_eligibility_changed_by_id | bool/ts/text/bigint | Manual override |
| created_at, updated_at | timestamp | |

### universities
| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| name | varchar | |
| oxbridge, russell_group, top_6 | bool | Tier flags |
| higher_education_institution_id | bigint | FK |
| created_at, updated_at | timestamp | |

### partner_hubs
Polymorphic parent for partner organisations (schools, universities, MATs, companies, local authorities). `hubable_type` + `hubable_id` point to the concrete partner.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| name, display_name | varchar | |
| hubable_type, hubable_id | polymorphic | Concrete partner row |
| location_id | bigint | FK |
| verified, verified_at | bool/ts | |
| mentoring_milestone_reached_at | timestamp | |
| daily_aggregated_mentoring_total_hours | integer | |
| insight_reminder_sent_at | timestamp | |
| two_factor_auth_required | bool | |
| created_at, updated_at | timestamp | |

### partner_users
Separate identity table for partner-hub staff (school admins, mentoring leads). Devise-backed but distinct from `users`.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| email, first_name, last_name | varchar | **PII** |
| sign_in_count, current_sign_in_at, last_sign_in_at, current_sign_in_ip, last_sign_in_ip | int/ts/varchar | |
| last_seen_at | timestamp | |
| otp_secret, consumed_timestep, otp_required_for_login | varchar/int/bool | 2FA |
| deleted_at | timestamp | Soft delete |
| created_at, updated_at | timestamp | |

### login_activities
Per-attempt auth audit log. Heavy table.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| scope, strategy, identity | varchar | |
| success, failure_reason | bool/varchar | |
| user_type, user_id | polymorphic | |
| context | varchar | |
| ip, user_agent, referrer | varchar/text | **PII** |
| city, region, country, latitude, longitude | varchar/double | **PII** geo |
| masquerading, masqueraded_by | bool/varchar | Admin masquerade audit |
| created_at | timestamp | |

### ai_chats / ai_conversations / ai_messages
In-product AI feature inside ZG (career-mentoring AI). **Different schema from Orbit's `chats`/`messages`.**

`ai_chats` — chat thread, optionally scoped to a `mentoring_goal_id`.

| Column | Type | Notes |
|--------|------|-------|
| id, user_id, mentoring_goal_id | bigint | |
| last_activity_date | timestamp | |
| created_at, updated_at | timestamp | |

`ai_conversations` — superseding the above (polymorphic over `user_type`/`subject_type`).

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| user_type, user_id | polymorphic | |
| subject_type, subject_id | polymorphic | What the convo is "about" |
| ai_model_id | bigint | FK → ai_models |
| ai_entry_point_version_id | bigint | FK → ai_entry_point_versions |
| title | varchar | |
| last_activity_at, deleted_at | timestamp | |
| created_at, updated_at | timestamp | |

`ai_messages` — individual messages with moderation tracking.

| Column | Type | Notes |
|--------|------|-------|
| id, ai_conversation_id, ai_model_id, ai_tool_call_id | bigint | |
| role | varchar | `user`, `assistant`, etc. |
| content | text | Message body |
| input_tokens, output_tokens | integer | Token usage |
| feedback | bool | Thumbs up/down |
| moderation_status, moderation_flagged_categories, moderation_response, moderated_at | varchar/jsonb/ts | OpenAI moderation results |
| hidden_at | timestamp | Soft delete |
| created_at, updated_at | timestamp | |

### community_entries
Posts + replies in community spaces. Polymorphic `parent_type/parent_id` (a space, or a parent entry for replies). STI via `type`.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| parent_type, parent_id | polymorphic | |
| user_id | bigint | Author |
| type | varchar | STI |
| title | varchar | |
| pinned | bool | |
| reactions_count, comments_count, comment_replies_count | integer | Counter caches |
| latest_activity_at | timestamp | |
| created_at, updated_at | timestamp | |

### community_spaces
| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| name, description, purpose | varchar | |
| visibility | varchar | `public`, `private`, etc. |
| granted_users_count, denied_users_count, requested_users_count | integer | |
| audience, boosted_in_sections | jsonb | |
| created_at, updated_at | timestamp | |

---

## Common Query Patterns

### Active mentorship matches
```sql
SELECT COUNT(*) AS active_matches,
       AVG(successful_call_logs_count)::numeric(10,2) AS avg_calls
FROM mentoring_matches
WHERE active = true AND ended_at IS NULL
```

### Match request funnel (last 30 days)
```sql
SELECT aasm_state, COUNT(*) AS n
FROM mentoring_match_requests
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY aasm_state
ORDER BY n DESC
```

### Mentor track state distribution
```sql
SELECT aasm_state, COUNT(*) AS n
FROM mentor_tracks
WHERE archived_at IS NULL
GROUP BY aasm_state
ORDER BY n DESC
```

### Live jobs by company
```sql
SELECT c.name, COUNT(*) AS live_jobs
FROM jobs j
JOIN companies c ON c.id = j.company_id
WHERE j.state = 'active' AND j.active = true
  AND (j.apply_by IS NULL OR j.apply_by > NOW())
GROUP BY c.name
ORDER BY live_jobs DESC
LIMIT 20
```

### Partner hubs by type
```sql
SELECT hubable_type, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE verified = true) AS verified
FROM partner_hubs
GROUP BY hubable_type
ORDER BY n DESC
```

### Community engagement (last 7 days)
```sql
SELECT cs.name, COUNT(ce.id) AS posts,
       SUM(ce.reactions_count) AS reactions,
       SUM(ce.comments_count) AS comments
FROM community_entries ce
JOIN community_spaces cs ON cs.id = ce.parent_id AND ce.parent_type = 'CommunitySpace'
WHERE ce.created_at >= NOW() - INTERVAL '7 days'
GROUP BY cs.name
ORDER BY posts DESC
```

### Login failure rate (last 24h)
```sql
SELECT scope,
       COUNT(*) FILTER (WHERE success) AS ok,
       COUNT(*) FILTER (WHERE NOT success) AS failed,
       ROUND(100.0 * COUNT(*) FILTER (WHERE NOT success) / COUNT(*), 2) AS pct_failed
FROM login_activities
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY scope
ORDER BY failed DESC
```

### AI-message moderation flagging
```sql
SELECT moderation_status, COUNT(*) AS n
FROM ai_messages
WHERE created_at >= NOW() - INTERVAL '7 days'
  AND moderation_status IS NOT NULL
GROUP BY moderation_status
ORDER BY n DESC
```

---

## Parsing Results

Same Python helper as the Orbit doc — pipe curl output through:

```bash
| python3 -c "
import sys,json
d = json.load(sys.stdin)
cols = [c['name'] for c in d.get('data',{}).get('cols',[])]
rows = d.get('data',{}).get('rows',[])
print(' | '.join(cols))
for r in rows:
    print(' | '.join([str(x or '')[:50] for x in r]))
"
```

For batched/paginated work see the helper in `metabase.md` — same `database` parameter pattern, just pass `2` instead of `38`.

---

## Gotchas

### 1. Default 2000-row limit on native queries

Same as Orbit. Metabase silently caps native query results at **2000 rows**. Paginate by ID, not row count, for any per-row sweep over `users`, `ai_messages`, `community_entries`, or `login_activities` (all > 100k rows).

### 2. `urllib` gets 403; shell out to `curl`

Same Metabase instance — same quirk. Use `subprocess.run(["curl", …])` from Python, not `urllib`.

### 3. PII guardrail: the `users` table is auto-blocked

The harness's auto-mode classifier will deny reads from `users` (and similar PII-rich tables — `partner_users`, `login_activities`, `telephone_verification_requests`) even when you select only innocent columns like `id, created_at`. The classifier treats the *table* as sensitive, not specific columns. Options:

- **Aggregate first** — `COUNT`, `GROUP BY`, `AVG`. Aggregates pass without prompts.
- **Join via FK without exposing PII** — e.g. `SELECT mt.aasm_state, COUNT(*) FROM mentor_tracks mt JOIN users u ON u.id = mt.user_id` works because `users` columns aren't in the projection.
- **Per-row reads** — ask the user; they may need to add a Bash permission rule or run the query themselves.

### 4. AASM `aasm_state` values are not enumerated in the DB

States are defined in Ruby (`mentor_track.rb`, `mentee_track.rb`, etc.) — Postgres sees them as plain varchars. If you see an unexpected state value, check the model source; don't assume the set is fixed.

### 5. Polymorphic columns require both `_type` and `_id`

Tables like `partner_hubs` (`hubable_type/id`), `ai_conversations` (`user_type/id`, `subject_type/id`), `community_entries` (`parent_type/id`), `feedback_records.requested_by_*`, and `jobs.submitted_by_*` are polymorphic. Joining requires filtering by `*_type` first:

```sql
-- WRONG — assumes all parents are CommunitySpace
SELECT cs.name, ce.title
FROM community_entries ce JOIN community_spaces cs ON cs.id = ce.parent_id

-- RIGHT
SELECT cs.name, ce.title
FROM community_entries ce
JOIN community_spaces cs
  ON cs.id = ce.parent_id AND ce.parent_type = 'CommunitySpace'
```

### 6. Soft-delete is inconsistent across tables

Some tables use `archived_at` (`users`, `mentor_tracks`, `mentee_tracks`, `partner_hubs.verified_at` is presence-based), some use `deleted_at` (`ai_conversations`, `partner_users`), some use `hidden_at` (`ai_messages`), and some use `closed_at` (`jobs`, `schools`). Always filter for "live" rows explicitly — there is no app-wide default scope assumed.

### 7. Two separate identity tables: `users` vs `partner_users`

A school's mentoring lead is a `partner_users` row, not a `users` row — they cannot mentor/mentee, only administer their hub. Don't join `partner_hubs` to `users`; use `partner_hub_users` (or the dedicated `partner_users` table). The two namespaces also have separate Devise sessions.

### 8. Mentorship doubles: a user can be both mentor and mentee

A single `users.id` can appear in both `mentor_tracks.user_id` and `mentee_tracks.user_id` (e.g. a uni student mentoring sixth-formers while being mentored by an alum). Don't assume role exclusivity. Filter by `aasm_state` and `archived_at` to find the active role.

---

**Version:** 1.0 | **Created:** 2026-05-15
