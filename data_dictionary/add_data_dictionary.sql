-- =============================================================================
-- db/migrations/002_add_data_dictionary.sql
--
-- Data dictionary for the warehouse schema, as PostgreSQL native column
-- comments — the standard mechanism Text-to-SQL/RAG frameworks (LangChain's
-- SQLDatabase toolkit, LlamaIndex, and similar schema-introspection agents)
-- read automatically via `information_schema`/`pg_description`, giving the
-- LLM machine-discoverable semantics with zero reliance on prompt-engineered
-- documentation being remembered every single query.
--
-- Grounded against the actual current schema (verified directly against
-- pipelines/schema.py and real exported CSVs from both seasons as of this
-- migration, not assumed) — one correction worth noting explicitly: a prior
-- external "coverage audit" of this project claimed bridge_player_seasons'
-- key was "(player_id, team_id, season_slug)". That's incorrect on two
-- counts: there is no `season_slug` column anywhere in this schema (it's
-- only ever been a snapshot-folder-naming convention, e.g.
-- `data/snapshots/season=2025_26/`, never a database column), and the real
-- primary key is `(season_id, bridge_player_season_id)` — deliberately
-- designed that way because `transfer_sequence` (part of
-- `bridge_player_season_id`'s own value) resets every season, so the id
-- alone isn't unique across seasons. This file documents the real design.
--
-- Idempotent: every statement is a plain `COMMENT ON COLUMN`, which always
-- overwrites any existing comment rather than erroring if one is already
-- present — safe to re-run this whole file after any future column change.
--
-- Per-90 rate stats, GI/xGI: fact_player_season_stats.mins_played documents
-- the full per-90 convention used throughout this dictionary (the formula,
-- the qualifying-minutes threshold, and why that threshold is expressed as
-- a fraction of the player's team's minutes played so far rather than a
-- fixed number) — read that column's comment first if a per-90 question
-- comes up. Per-90 formulas are attached to the ~20 columns most commonly
-- expressed that way in football analysis (attacking output, progression,
-- core defensive actions, goalkeeping) — deliberately not applied
-- mechanically to every counting column in this table; see mins_played for
-- the full reasoning. Team-level tables (fact_team_match_stats,
-- fact_team_season_stats) do NOT get per-90 treatment: team stats are
-- naturally per-match already (a team always plays a full match, barring
-- abandonment), so "per 90" adds nothing there that per-match/per-season
-- totals don't already give directly.
-- =============================================================================


-- =============================================================================
-- Table catalog — one-line purpose per table. This block exists so the RAG
-- engine's schema context can start compact (just these 12 lines) rather
-- than dumping every column of every table on every question, then pull
-- full column-level detail only for the specific table(s) a given question
-- actually needs (see rag/schema_context.py's describe_table). Add a line
-- here whenever a new warehouse table is created.
-- =============================================================================

COMMENT ON TABLE warehouse.dim_seasons IS
'One row per Premier League season (e.g. 2025/26). Reference dimension for season-scoped queries.';

COMMENT ON TABLE warehouse.dim_teams IS
'One row per Premier League club. Reference dimension for team-scoped queries and name/alias resolution.';

COMMENT ON TABLE warehouse.dim_players IS
'One row per player. Reference dimension for player-scoped queries and name resolution.';

COMMENT ON TABLE warehouse.dim_fixtures IS
'One row per scheduled match (fixture), including kickoff time and status. Central table for any date/match-scoped question.';

COMMENT ON TABLE warehouse.bridge_player_seasons IS
'One row per player per club spell within a season - resolves which team a player belonged to at a given point, including mid-season transfers.';

COMMENT ON TABLE warehouse.fact_match_events IS
'One row per goal, card, or substitution event within a match, with the exact minute and the player(s)/team involved. Use for event-level questions (who scored, who was carded, when).';

COMMENT ON TABLE warehouse.fact_shot_events IS
'One row per shot attempt (goal, save, block, miss) within a match, including shot origin (open play/set piece/penalty), body part, and location. Use for shot-quality/shot-origin questions.';

COMMENT ON TABLE warehouse.fact_match_lineup IS
'One row per player per fixture they were named in, including starter/substitute status and minutes played in that specific match.';

COMMENT ON TABLE warehouse.fact_player_season_stats IS
'One row per player per season: season-aggregate stats (goals, assists, shots, tackles, per-90 rates, xG/xGI). The main table for "how good was player X this season" questions.';

COMMENT ON TABLE warehouse.fact_team_match_stats IS
'One row per team per fixture: that match''s result, goals scored/conceded, and detailed match stats (possession, shots, etc.) for that team in that game.';

COMMENT ON TABLE warehouse.fact_team_season_stats IS
'One row per team per season: season-aggregate team stats, the team-level equivalent of fact_player_season_stats.';

COMMENT ON TABLE warehouse.fact_premier_league_table IS
'One row per team per season - the current standings snapshot, overwritten after every gameweek (primary key is team_id + season_id, so there is no per-gameweek history here; this table cannot answer "where were they after gameweek N", only the position as of the most recent completed gameweek).
For a plain "show me the [Premier League] table/standings" request with no other qualifiers, return exactly these columns per team, in this order: team_name, matches_played AS "MP", wins AS "W", draws AS "D", losses AS "L", goals_for AS "GF", goals_against AS "GA", goals_difference AS "GD", points AS "Pts" - ordered by points DESC, goals_difference DESC, goals_for DESC (see the points column for why this is the tiebreak order). Do not include the home_/away_ split or xg/xga/xgd columns unless the question specifically asks about home form, away form, or expected goals.';
-- =============================================================================


-- =============================================================================
-- dim_seasons
-- =============================================================================

COMMENT ON COLUMN warehouse.dim_seasons.season_id IS
'Primary key. Pulse/PulseLive competition-season identifier (integer, e.g. 777 for 2025/26, 841 for 2026/27) — NOT the same numbering as season_name.';

COMMENT ON COLUMN warehouse.dim_seasons.season_name IS
'Human-readable season label, normalized to "YYYY/YY" (e.g. "2025/26", "2026/27"). Synonyms in natural language: "last season" usually means the most recently completed season (lowest season_id with fixture_status = ''C'' for all fixtures); "this season" / "current season" means the season with unplayed (''U'') or live (''L'') fixtures still remaining.';

COMMENT ON COLUMN warehouse.dim_seasons.competition_name IS
'Competition name. Always "Premier League" in this warehouse — present for schema completeness/future multi-competition support, not because multiple competitions are currently stored.';

COMMENT ON COLUMN warehouse.dim_seasons.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert, regardless of how many times the row is later upserted with changed data — use updated_at for "last changed", not this column.';

COMMENT ON COLUMN warehouse.dim_seasons.updated_at IS
'Audit timestamp: when this row''s data last actually changed. Does NOT advance on a no-op re-run where nothing changed — only when at least one non-key column''s value genuinely differs from what was already stored.';


-- =============================================================================
-- dim_teams
-- =============================================================================

COMMENT ON COLUMN warehouse.dim_teams.team_id IS
'Primary key. Pulse/PulseLive team identifier (integer, stable across seasons for a given club — does not change on promotion/relegation/re-entry).';

COMMENT ON COLUMN warehouse.dim_teams.team_name IS
'Official full club name, exactly as used elsewhere in this warehouse for joins (e.g. "Manchester United", "Tottenham Hotspur"). Common vernacular, abbreviations, and nicknames used in natural-language questions do NOT literally match this column and must be resolved to the correct team_name/team_id first. Known aliases for the 20 teams currently in this warehouse:
  Arsenal -> "The Gunners", "Arsenal FC", "AFC"
  Aston Villa -> "Villa", "The Villans"
  Bournemouth -> "AFC Bournemouth", "The Cherries"
  Brentford -> "The Bees"
  Brighton & Hove Albion -> "Brighton", "The Seagulls", "BHA"
  Chelsea -> "The Blues", "CFC"
  Coventry City -> "Coventry", "The Sky Blues"
  Crystal Palace -> "Palace", "The Eagles", "CPFC"
  Everton -> "The Toffees"
  Fulham -> "The Cottagers"
  Hull City -> "Hull", "The Tigers"
  Ipswich Town -> "Ipswich", "Town", "The Tractor Boys"
  Leeds United -> "Leeds", "The Whites", "LUFC"
  Liverpool -> "The Reds", "LFC" (NOTE: "The Reds" is ambiguous with Nottingham Forest below — Liverpool is the far more common referent, but if context suggests a lower-table/relegation-fight team, confirm against Nottingham Forest instead)
  Manchester City -> "Man City", "City", "MCFC", "The Citizens"
  Manchester United -> "Man Utd", "Man United", "United", "The Red Devils", "MUFC"
  Newcastle United -> "Newcastle", "The Magpies", "NUFC"
  Nottingham Forest -> "Forest", "Nottm Forest", "NFFC", "The Reds" (see Liverpool note above — this ambiguity is real, not a data error)
  Tottenham Hotspur -> "Spurs", "THFC"
  Sunderland -> "The Black Cats"
  Burnley -> "The Clarets"
  West Ham United -> "West Ham", "The Hammers", "WHUFC"
  Wolverhampton Wanderers -> "Wolves", "The Wolves", "WWFC", "The Wanderers"
Note: Any other team name/alias not listed above is either a misnomer, a non-Premier-League club, or a historical/defunct club that is not present in this warehouse. Do not assume that a query naming them should return any rows — it should resolve to zero rows, not an error or a guessed substitute.';

COMMENT ON COLUMN warehouse.dim_teams.short_name IS
'Official 3-letter competition code (e.g. "MUN", "ARS", "TOT") — distinct from the informal abbreviations listed under team_name (e.g. "Man Utd" is a common alias, not this column''s value).';

COMMENT ON COLUMN warehouse.dim_teams.team_logo_url IS
'URL to the club crest/logo image, as served by PulseLive. Not typically relevant to natural-language stat queries.';

COMMENT ON COLUMN warehouse.dim_teams.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.dim_teams.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- dim_players
-- =============================================================================

COMMENT ON COLUMN warehouse.dim_players.player_id IS
'Primary key. Pulse/PulseLive player identifier (integer, stable across seasons and clubs for a given individual).';

COMMENT ON COLUMN warehouse.dim_players.player_name IS
'Player''s full display name, as used elsewhere in this warehouse for joins. Natural-language questions often use only a surname or a well-known short form (e.g. "Saka" for "Bukayo Saka") — resolve via partial/fuzzy match against this column, not exact match, when the question does not give a full name.';

COMMENT ON COLUMN warehouse.dim_players.date_of_birth IS
'Player date of birth (real DATE type). Use directly for age-based queries, e.g. age in years as of today = DATE_PART(''year'', AGE(CURRENT_DATE, date_of_birth)) — substitute any other reference date for CURRENT_DATE as needed. Nullable for players with an incomplete Pulse profile.';

COMMENT ON COLUMN warehouse.dim_players.country IS
'Player''s nationality, as a country name string (not an ISO code). Nullable for players with an incomplete Pulse profile.';

COMMENT ON COLUMN warehouse.dim_players.player_photo_url IS
'URL to the player headshot image, as served by PulseLive. Not typically relevant to natural-language stat queries.';

COMMENT ON COLUMN warehouse.dim_players.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.dim_players.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- dim_fixtures
-- =============================================================================

COMMENT ON COLUMN warehouse.dim_fixtures.fixture_id IS
'Primary key. Pulse/PulseLive match identifier — a single global, monotonically-assigned pool, never reused or reset per season (verified: zero overlap between any two seasons'' fixture_id ranges in this warehouse). Every fact table keyed by fixture_id relies on this global uniqueness.';

COMMENT ON COLUMN warehouse.dim_fixtures.season_id IS
'Foreign key to dim_seasons.season_id — the season this fixture belongs to.';

COMMENT ON COLUMN warehouse.dim_fixtures.gameweek IS
'Premier League matchweek/round number (1-38 for a standard 20-team season).';

COMMENT ON COLUMN warehouse.dim_fixtures.kickoff_datetime IS
'Scheduled kickoff date and time (timestamp). For an unplayed/rearranged fixture this may not reflect the final actual kickoff time until fixture_status is updated to ''C''.';

COMMENT ON COLUMN warehouse.dim_fixtures.stadium IS
'Name of the venue hosting the fixture (normally the home team''s home ground).';

COMMENT ON COLUMN warehouse.dim_fixtures.attendance IS
'Reported crowd attendance for the fixture. NULL for fixtures that have not yet been played, or where Pulse has not reported a figure.';

COMMENT ON COLUMN warehouse.dim_fixtures.home_team_id IS
'Foreign key to dim_teams.team_id — the home team for this fixture.';

COMMENT ON COLUMN warehouse.dim_fixtures.away_team_id IS
'Foreign key to dim_teams.team_id — the away team for this fixture.';

COMMENT ON COLUMN warehouse.dim_fixtures.fixture_status IS
'Match status code: ''U'' = Unplayed/Scheduled (not yet kicked off), ''L'' = Live/In-Progress, ''C'' = Completed/Full-Time. Only ''C'' fixtures have reliable, final fact_match_events/fact_shot_events/fact_team_match_stats data — treat ''U''/''L'' fixtures'' stats as provisional or absent.';

COMMENT ON COLUMN warehouse.dim_fixtures.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.dim_fixtures.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values) — e.g. advances when a fixture moves from ''U''/''L'' to ''C'', or is rearranged to a new kickoff_datetime.';


-- =============================================================================
-- bridge_player_seasons
-- (tracks which team(s) a player was registered with in a given season —
-- supports mid-season transfers via transfer_sequence)
-- =============================================================================

COMMENT ON COLUMN warehouse.bridge_player_seasons.bridge_player_season_id IS
'Part of the composite primary key (with season_id). Format "{player_id}_{transfer_sequence}" — deterministic, not a row-position counter. NOT unique on its own across seasons: transfer_sequence resets to 1 every season, so e.g. player 10''s "10_1" recurs in every season they have exactly one spell — season_id (see that column) is what actually disambiguates.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.player_id IS
'Foreign key to dim_players.player_id.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.season_id IS
'Part of the composite primary key (with bridge_player_season_id). Foreign key to dim_seasons.season_id. Required in the key because bridge_player_season_id alone is only unique within one season (see that column''s comment) — this is real, load-bearing disambiguation, not defensive redundancy.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.team_id IS
'Foreign key to dim_teams.team_id — the team this spell/registration was with.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.position IS
'Single-letter position code: ''G'' = Goalkeeper, ''D'' = Defender, ''M'' = Midfielder, ''F'' = Forward. See position_info for the specific natural-language role (e.g. "Right Full Back", "Attacking Midfielder") within that broad category.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.position_info IS
'Specific natural-language playing role (e.g. "Centre Central Midfielder", "Right Full Back", "Centre Striker") — more granular than, and always consistent with, the broad position code.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.shirt_number IS
'Squad number worn during this spell. Nullable, and can differ across a player''s different seasons or spells.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.age IS
'Player''s age (in years) as of this spell/season — a point-in-time snapshot, not derived live from dim_players.date_of_birth at query time. For a precise as-of-any-date age, compute from dim_players.date_of_birth instead.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.transfer_sequence IS
'1-based sequence number disambiguating multiple spells by the same player within the SAME season (e.g. a mid-season transfer produces transfer_sequence 1 for the first club, 2 for the second). Resets to 1 every season — see bridge_player_season_id''s comment for why this makes season_id necessary in the primary key.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.bridge_player_seasons.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- fact_match_lineup
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_match_lineup.player_id IS
'Part of the composite primary key (with fixture_id). Foreign key to dim_players.player_id.';

COMMENT ON COLUMN warehouse.fact_match_lineup.fixture_id IS
'Part of the composite primary key (with player_id). Foreign key to dim_fixtures.fixture_id.';

COMMENT ON COLUMN warehouse.fact_match_lineup.season_id IS
'Foreign key to dim_seasons.season_id — denormalized from dim_fixtures for convenient season-scoped filtering without a join.';

COMMENT ON COLUMN warehouse.fact_match_lineup.team_id IS
'Foreign key to dim_teams.team_id — the team this player appeared for in this specific fixture (handles the rare case of a mid-fixture-window transfer more reliably than assuming their season-long team).';

COMMENT ON COLUMN warehouse.fact_match_lineup.minutes_played IS
'Total minutes played by this player in this fixture (integer, 0-130 plausible range including stoppage time). 0 for a named substitute who did not come on.';

COMMENT ON COLUMN warehouse.fact_match_lineup.starter_flag IS
'TRUE if the player started the match in the starting XI; FALSE if they were an unused or introduced substitute.';

COMMENT ON COLUMN warehouse.fact_match_lineup.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_match_lineup.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- fact_match_events
-- Discrete in-match events (goals, cards, substitutions). One row per event.
-- Player attribution uses explicit, non-overlapping role columns instead of
-- a positional player1/player2 pair — exactly one of the five role columns
-- below is populated per row, determined by event_type; the rest are NULL.
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_match_events.match_event_id IS
'Primary key. Format "{fixture_id}_{local_index}" — deterministic and fully unique on its own (fixture_id is a single global, never-reused pool — see dim_fixtures.fixture_id), so no season_id is needed alongside it in the key. Local index is a 1-based counter of the event''s ordinal position within the fixture, as reported by Pulse. It starts at 0 for the first event in the fixture and increments by 1 for each subsequent event, regardless of type.';

COMMENT ON COLUMN warehouse.fact_match_events.fixture_id IS
'Foreign key to dim_fixtures.fixture_id — the match this event occurred in.';

COMMENT ON COLUMN warehouse.fact_match_events.season_id IS
'Foreign key to dim_seasons.season_id — denormalized from dim_fixtures for convenient season-scoped filtering without a join.';

COMMENT ON COLUMN warehouse.fact_match_events.team_id IS
'Foreign key to dim_teams.team_id. For event_type IN (''goal'',''penalty goal'',''own goal''): the team CREDITED with the goal on the scoreboard (for an own goal, this is the beneficiary team, NOT the team of the player who put it in their own net — see own_goal_player_id). For event_type IN (''yellow'',''red''): the carded player''s own team. For event_type = ''substitution'': the team making the substitution.
To find the team that actually CONCEDED an own goal (the opposite of this column''s beneficiary team for that row), join to dim_fixtures on fixture_id and take whichever of home_team_id/away_team_id is NOT this row''s team_id.';

COMMENT ON COLUMN warehouse.fact_match_events.event_type IS
'One of: ''goal'', ''penalty goal'', ''own goal'', ''yellow'', ''red'', ''substitution''. Determines which single role column below is populated (see each role column''s comment for the exact mapping).
Counting a player''s goals at the event level: ''goal'' and ''penalty goal'' are both genuine goals scored by that player — "how many goals did X score" must include both. The simplest correct filter is WHERE scorer_player_id = <id> with NO event_type filter at all, since scorer_player_id is only ever populated for these two types (see that column''s comment). Adding event_type = ''goal'' on top silently drops penalties and undercounts — do not do this.';

COMMENT ON COLUMN warehouse.fact_match_events.scorer_player_id IS
'Foreign key to dim_players.player_id. Populated ONLY when event_type IN (''goal'', ''penalty goal'') — the player who scored. NULL for every other event_type, including ''own goal'' (an own goal is not a genuine scoring credit — see own_goal_player_id instead).';

COMMENT ON COLUMN warehouse.fact_match_events.assist_player_id IS
'Foreign key to dim_players.player_id. Populated ONLY when event_type = ''goal'' AND an assist was recorded (nullable even then — a meaningful fraction of open-play goals have no recorded assister, e.g. a solo effort). Always NULL for ''penalty goal'' (Pulse never records an assist for a penalty) and for every non-goal event_type.';

COMMENT ON COLUMN warehouse.fact_match_events.own_goal_player_id IS
'Foreign key to dim_players.player_id. Populated ONLY when event_type = ''own goal'' — the player who put the ball into their own net. This player''s own team is the OPPOSITE of this row''s team_id (team_id here is the beneficiary — join to dim_players/bridge_player_seasons if the conceding player''s own team is needed).';

COMMENT ON COLUMN warehouse.fact_match_events.carded_player_id IS
'Foreign key to dim_players.player_id. Populated ONLY when event_type IN (''yellow'', ''red'') — the carded player. A second-yellow-card dismissal is recorded as a ''red'' event, not as two separate ''yellow'' rows.';

COMMENT ON COLUMN warehouse.fact_match_events.player_on_id IS
'Foreign key to dim_players.player_id. Populated ONLY when event_type = ''substitution'' — the player entering the pitch.';

COMMENT ON COLUMN warehouse.fact_match_events.player_off_id IS
'Foreign key to dim_players.player_id. Populated ONLY when event_type = ''substitution'' — the player leaving the pitch.';

COMMENT ON COLUMN warehouse.fact_match_events.minute IS
'Total elapsed match minute as a plain integer, including stoppage time folded into the count (e.g. 91 for a goal scored in the 1st minute of second-half stoppage time, displayed as "90+1''"). Safe for direct numeric comparisons (WHERE minute > 70) — use this column, not minute_display, for any range/threshold query.';

COMMENT ON COLUMN warehouse.fact_match_events.minute_display IS
'Human-readable original minute text exactly as shown in match commentary (e.g. "23", "90+1''"). For display/citation purposes only — NOT numerically comparable (it is a text column that mixes plain minutes with "+" stoppage-time notation). Use the minute column for any numeric filtering.';

COMMENT ON COLUMN warehouse.fact_match_events.is_stoppage_time IS
'Flag for stoppage time. Stored as "t" (True) if event occurred during first-half or second-half stoppage/injury time (i.e. minute_display contains a "+"), or "f" (False) otherwise.';

COMMENT ON COLUMN warehouse.fact_match_events.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_match_events.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- fact_shot_events
-- Every shot attempt (goals, saves, blocks, misses). team_id here means
-- "team that took the shot" — a deliberately different convention from
-- fact_match_events.team_id ("team credited with the outcome") — see the
-- team_id comment below and the own goal handling it calls out.
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_shot_events.shot_event_id IS
'Primary key. Format "{fixture_id}_{local_index}" — same stability reasoning as fact_match_events.match_event_id (fixture_id alone is globally unique). Local index is a 1-based counter of the event''s ordinal position within the fixture, as reported by Pulse. It starts at 0 for the first shot in the fixture and increments by 1 for each subsequent shot, regardless of outcome.';

COMMENT ON COLUMN warehouse.fact_shot_events.fixture_id IS
'Foreign key to dim_fixtures.fixture_id — the match this shot occurred in.';

COMMENT ON COLUMN warehouse.fact_shot_events.season_id IS
'Foreign key to dim_seasons.season_id — denormalized from dim_fixtures for convenient season-scoped filtering without a join.';

COMMENT ON COLUMN warehouse.fact_shot_events.team_id IS
'Foreign key to dim_teams.team_id — the team that TOOK the shot (player1_id''s own team), consistent with how shot-attempt statistics are conventionally attributed. For outcome = ''Own Goal'', this is deliberately the CONCEDING team (the shooter''s/header''s own team), NOT the beneficiary — the opposite convention from fact_match_events.team_id for the same real-world event. An opponent''s own goal is never counted toward a team''s own shot-attempt tally.';

COMMENT ON COLUMN warehouse.fact_shot_events.player1_id IS
'Foreign key to dim_players.player_id — the player who took the shot.';

COMMENT ON COLUMN warehouse.fact_shot_events.player2_id IS
'Foreign key to dim_players.player_id — the player who assisted the shot (nullable; some shots, including some goals, have no recorded assister).';

COMMENT ON COLUMN warehouse.fact_shot_events.minute IS
'Total elapsed match minute as a plain integer, including stoppage time folded into the count (e.g. 91 for "90+1''"). Safe for direct numeric comparisons (WHERE minute > 70) — use this column, not minute_display, for any range/threshold query.';

COMMENT ON COLUMN warehouse.fact_shot_events.minute_display IS
'Human-readable original minute text exactly as shown in match commentary (e.g. "23", "90+1''"). Display/citation only — not numerically comparable.';

COMMENT ON COLUMN warehouse.fact_shot_events.is_stoppage_time IS
'Flag for stoppage time. Stored as "t" (True) if event occurred during first-half or second-half stoppage/injury time (i.e. minute_display contains a "+"), or "f" (False) otherwise.';

COMMENT ON COLUMN warehouse.fact_shot_events.shot_type IS
'Shot origin category. Exact closed set of values, no others occur: ''Own Goal'', ''Penalty'', ''Set Piece'', ''Open Play''. ''Set Piece'' covers both corners and free kicks combined - this warehouse does not distinguish which of the two for a given shot, so a question asking specifically about corners alone or free kicks alone cannot be answered from this column, only "set piece" as a whole. To compare a team''s goals conceded by set piece vs open play: filter fact_shot_events to that team''s fixtures, outcome = ''Goal'' AND team_id = the OPPONENT (a normal goal against them), UNION with outcome = ''Own Goal'' AND team_id = the team itself (see this table''s team_id comment for why the own-goal convention differs from fact_match_events), then GROUP BY shot_type.';

COMMENT ON COLUMN warehouse.fact_shot_events.body_part IS
'Body part used to take the shot. Exact closed set of values, no others occur: ''Right Foot'', ''Left Foot'', ''Header'', ''Volley''. NULL when Pulse''s shot commentary text does not clearly indicate one of these (a real, fairly common case - do not assume NULL means an error).';

COMMENT ON COLUMN warehouse.fact_shot_events.distance IS
'Qualitative shot distance indicator. Exact closed set of values, no others occur: ''Inside Box'', ''Outside Box''. NOT a numeric distance measurement - there is no exact-yardage distance column in this warehouse. NULL when Pulse''s shot commentary text does not clearly indicate one of these.';

COMMENT ON COLUMN warehouse.fact_shot_events.outcome IS
'Result of the shot attempt. Exact closed set of values, no others occur: ''Goal'', ''Saved'', ''Missed'', ''Blocked'', ''Own Goal'', ''Hit the Woodwork''. A shot attempts stat query (e.g. "how many shots did Team X have") should count ALL rows regardless of outcome, matching the standard football-analytics meaning of "shot attempts" / "total_scoring_att" (see fact_team_match_stats.total_scoring_att) - not just outcome = ''Goal''.';

COMMENT ON COLUMN warehouse.fact_shot_events.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_shot_events.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- fact_player_season_stats
-- Season-aggregate Opta player statistics. Most columns below are Opta's own
-- internal stat codes, carried through verbatim from the raw feed — several
-- are non-obvious abbreviations; synonyms are included specifically so a
-- natural-language question phrased in plain English (e.g. "shots", "shot
-- attempts") resolves to the correct column without the exact Opta term.
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_player_season_stats.player_id IS
'Part of the composite primary key (with season_id). Foreign key to dim_players.player_id.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.season_id IS
'Part of the composite primary key (with player_id). Foreign key to dim_seasons.season_id.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.appearances IS
'Total number of matches this player appeared in (started or came on as a substitute) this season.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.game_started IS
'Number of matches this player started (was in the starting XI) this season.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_sub_on IS
'Number of times this player was introduced as a substitute this season.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_sub_off IS
'Number of times this player was substituted off this season.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.mins_played IS
'Total minutes played across all appearances this season. This is the denominator for every "per 90" rate stat in this warehouse (see the columns below that reference it) — the general formula is:
  metric_per_90 = metric * 90.0 / NULLIF(mins_played, 0)
"Per 90" and "/90" are the same thing in natural-language questions (e.g. "xGI per 90" and "xGI/90" refer to the identical stat) — treat them as synonyms.

Small-sample caveat, and the qualifying-minutes convention: a per-90 rate computed on very few minutes is unreliable (one goal in a 10-minute cameo produces an absurd per-90 rate) and should not be reported without a qualifying-minutes filter first. Rather than a fixed minutes cutoff (which would exclude every single player early in a season and need manual raising as the season progresses), the qualifying threshold in this warehouse is expressed as a percentage of the player''s team''s total possible minutes so far this season:
  a player qualifies for per-90 comparisons if mins_played >= 0.30 * (team''s completed fixtures this season * 90)
"Team''s completed fixtures this season" for a given player_id/season_id is computed via bridge_player_seasons (to resolve team_id) joined to dim_fixtures:
  SELECT COUNT(*) FROM warehouse.dim_fixtures f
  WHERE f.season_id = <season_id> AND f.fixture_status = ''C''
    AND (f.home_team_id = <team_id> OR f.away_team_id = <team_id>)
This single formula needs no special-casing for season status: early in a live season it naturally yields a small, proportionate qualifying threshold that grows automatically as more fixtures complete, and once every fixture in a season is complete (e.g. season_id 777) it automatically becomes a fixed, full-season qualifying threshold — the same rule, unchanged, applies to both a live and a completed season. 30% is a reasonable default to exclude fringe/cameo appearances while still including squad-rotation regulars; it is a documented convention in this warehouse, not a rigid industry rule — adjust it for a specific analysis if a stricter or looser cutoff is called for, but state the percentage used when doing so.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.goals IS
'Total goals scored this season, all types combined (open play + penalties). Synonyms: "goals scored", "goal tally".
GI (Goal Involvements): GI = goals + goal_assist. Synonyms: "goal involvements", "G+A", "G/A", "goals and assists", "GA".
Per 90: goals_per_90 = goals * 90.0 / NULLIF(mins_played, 0); GI_per_90 = (goals + goal_assist) * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting either rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.goals_openplay IS
'Goals scored from open play specifically (excludes penalties; own goals are never credited to a scorer, so also excluded by definition).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.goal_assist IS
'Total assists — passes that directly led to a goal. Synonyms: "assists".
GI (Goal Involvements): GI = goals + goal_assist. See the goals column for the full GI/per-90 formula and synonyms.
Per 90: assists_per_90 = goal_assist * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.goal_assist_deadball IS
'Assists specifically from a dead-ball situation (e.g. a corner or free kick delivery), a subset already included in goal_assist. Synonym: "assists from set pieces" - a set-piece assist is one where the delivery itself was a corner, free kick, or other qualifying dead-ball event, excluding penalties. This is the season-aggregate column for that definition; do not derive "set-piece assists" from fact_shot_events.shot_type = ''Set Piece'' instead, which classifies the resulting shot''s own origin, not the assisting delivery, and is a different (and generally smaller) count.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_att_assist IS
'Total "attempted assists" — passes that led to a shot attempt, whether or not that shot resulted in a goal (a superset of goal_assist).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.big_chance_created IS
'Number of clear goalscoring opportunities this player created for a teammate (Opta-defined "big chance": a situation where the receiving player would reasonably be expected to score).
Per 90: big_chances_created_per_90 = big_chance_created * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.big_chance_missed IS
'Number of clear goalscoring opportunities this player received but failed to score from.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_scoring_att IS
'Total shot attempts (shots on target + shots off target + blocked shots). Synonyms: "shots", "shot attempts", "total shots", "shots taken". This is the correct column for "how many shots did X take" — NOT goals or ontarget_scoring_att.
Per 90: shots_per_90 = total_scoring_att * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.ontarget_scoring_att IS
'Shot attempts that were on target (would have gone in without a save or blocking touch, including goals). Synonyms: "shots on target", "SoT".
Per 90: shots_on_target_per_90 = ontarget_scoring_att * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_pass IS
'Total passes attempted (completed + incomplete).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_pass IS
'Total passes completed successfully. Pass accuracy % = accurate_pass / NULLIF(total_pass, 0).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_final_third_passes IS
'Total passes attempted into the attacking (final) third of the pitch.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.successful_final_third_passes IS
'Passes into the attacking final third that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.blocked_pass IS
'Passes that were blocked by an opponent before reaching their intended target.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_long_balls IS
'Total long passes (>25-32 yards, Opta-defined threshold) attempted.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_long_balls IS
'Long passes that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_cross IS
'Total crosses attempted (passes delivered from a wide area into the box).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_cross IS
'Crosses that successfully reached a teammate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.corner_taken IS
'Number of corner kicks taken by this player.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.final_third_entries IS
'Number of times this player carried or passed the ball into the attacking final third.
Per 90: final_third_entries_per_90 = final_third_entries * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.pen_area_entries IS
'Number of times this player carried or passed the ball into the opposition penalty area.
Per 90: pen_area_entries_per_90 = pen_area_entries * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.carries IS
'Total number of times this player carried (ran with) the ball.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.progressive_carries IS
'Ball carries that moved the ball significantly closer to the opposition goal (Opta-defined progressive-distance threshold).
Per 90: progressive_carries_per_90 = progressive_carries * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.touches IS
'Total number of times this player touched the ball.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.touches_in_final_third IS
'Touches that occurred in the attacking final third of the pitch.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.touches_in_opp_box IS
'Touches that occurred inside the opposition penalty area.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_through_ball IS
'Through balls attempted (a pass played between/behind defenders into space for a teammate to run onto).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_tackle IS
'Total tackles attempted (successful + unsuccessful).
Per 90: tackles_per_90 = total_tackle * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.won_tackle IS
'Tackles that successfully won possession of the ball.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.times_tackled IS
'Number of times this player was tackled by an opponent (regardless of whether the opponent''s tackle won the ball).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.duel_won IS
'Total 1-on-1 duels (ground or aerial) won.
Per 90: duels_won_per_90 = duel_won * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.duel_lost IS
'Total 1-on-1 duels lost.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.interception_won IS
'Number of successful interceptions (reading and cutting out an opponent''s pass).
Per 90: interceptions_per_90 = interception_won * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.challenge_lost IS
'Number of unsuccessful defensive challenges (the opponent retained or won the ball).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.ball_recovery IS
'Number of loose-ball recoveries (regaining possession that was not clearly won via a tackle or interception).
Per 90: ball_recoveries_per_90 = ball_recovery * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.dispossessed IS
'Number of times this player lost the ball to an opponent''s direct challenge while in possession.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.outfielder_block IS
'Number of shots blocked by this outfield player (not a goalkeeper save).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_clearance IS
'Total defensive clearances (kicking/heading the ball away from danger).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.aerial_won IS
'Aerial (headed) duels won.
Per 90: aerials_won_per_90 = aerial_won * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.aerial_lost IS
'Aerial (headed) duels lost.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.fouls IS
'Fouls committed by this player.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.was_fouled IS
'Number of times this player was fouled by an opponent.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.yellow_card IS
'Number of yellow cards received this season (does not include the yellow half of a second-yellow dismissal — see second_yellow).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.second_yellow IS
'Number of dismissals via two bookable offenses in the same match (a "second yellow" red card).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.red_card IS
'Number of straight (direct) red cards received — does not include second-yellow dismissals (see second_yellow). For total dismissals, sum both columns.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_offside IS
'Number of times this player was flagged offside.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.penalty_conceded IS
'Number of penalty kicks conceded by this player''s foul/handball.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.penalty_won IS
'Number of penalty kicks won for their team by this player being fouled.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.clean_sheet IS
'Number of matches this player finished on the pitch without their team conceding a goal (goalkeepers and defenders primarily; only meaningful if the player played a substantial portion of the match — Opta-defined threshold).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_keeper_sweeper IS
'Goalkeeper defensive actions taken outside the penalty area (i.e. "sweeper-keeper" actions). Zero/NULL-equivalent for outfield players.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_keeper_sweeper IS
'Successful sweeper-keeper actions outside the box.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.poss_won_att_3rd IS
'Possession won (via tackle, interception, or recovery) in the attacking third of the pitch.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_launches IS
'Accurate long goalkeeper kicks/launches (kicks over roughly 40+ yards that successfully found a teammate). NOTE: the underlying Opta feed uses the misspelled key "accurate_lauches" (missing the "n") — this warehouse column is spelled correctly; be aware if cross-referencing raw Opta exports or third-party tools that still use the original typo.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_keeper_throws IS
'Goalkeeper throws (as opposed to kicks) that successfully found a teammate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.keeper_throws IS
'Total goalkeeper throws attempted (accurate + inaccurate).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.total_high_claim IS
'Goalkeeper high (aerial) catches made under pressure, e.g. claiming a cross.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.goal_kicks IS
'Total goal kicks taken.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.accurate_goal_kicks IS
'Goal kicks that successfully found a teammate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.poss_won_def_3rd IS
'Possession won in the defensive third of the pitch.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.poss_won_mid_3rd IS
'Possession won in the middle third of the pitch.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.penalty_faced IS
'Number of penalty kicks this player (a goalkeeper) faced.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.pen_goals_conceded IS
'Penalty kicks that resulted in a goal against this player (a goalkeeper).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.good_high_claim IS
'High claims made cleanly/successfully, a subset of total_high_claim.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.goals_conceded IS
'Goals conceded while this player (a goalkeeper) was on the pitch.
Per 90: goals_conceded_per_90 = goals_conceded * 90.0 / NULLIF(mins_played, 0) — a standard goalkeeper rate stat. See mins_played for the qualifying-minutes convention before reporting this rate (for goalkeepers specifically, qualify against mins_played as goalkeeper minutes, not outfield minutes, since a keeper''s "team" in the qualifying formula is the same team_id either way).';

COMMENT ON COLUMN warehouse.fact_player_season_stats.non_penalty_goals IS
'Goals scored excluding penalty conversions (equivalent to goals_openplay in practice for most players, kept as a separate Opta-native column).
npGI (Non-Penalty Goal Involvements): npGI = non_penalty_goals + goal_assist. Synonyms: "npGI", "non-penalty goal involvements". The non-penalty counterpart to GI (see the goals column).
Per 90: non_penalty_goals_per_90 = non_penalty_goals * 90.0 / NULLIF(mins_played, 0); npGI_per_90 = (non_penalty_goals + goal_assist) * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting either rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.penalties_missed IS
'Penalty kicks taken and missed (saved or off target) by this player.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.penalties_scored IS
'Penalty kicks successfully converted by this player.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.saves IS
'Total saves made (goalkeepers).
Per 90: saves_per_90 = saves * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.own_goals IS
'Own goals scored by this player this season. Cross-reference: individual own-goal events (with fixture and minute) are in fact_match_events.own_goal_player_id, not here — this column is only a season total.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.defensive_contribution IS
'Opta''s composite defensive-actions metric (combines tackles, interceptions, and other defensive actions into a single count) — introduced as an official defender/midfielder involvement stat.
Per 90: defensive_contribution_per_90 = defensive_contribution * 90.0 / NULLIF(mins_played, 0) — this is the exact form of "defensive contribution" used for the FPL defensive-contribution scoring threshold, so this rate is a particularly common one to be asked about. See mins_played for the qualifying-minutes convention before reporting it.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.xg IS
'Expected Goals (xG) — the probability, summed across all this player''s shots this season, that each shot results in a goal, based on historical shot-quality factors (location, angle, body part, defensive pressure, assist type). Synonyms: "xG", "expected goals", "chance quality". Higher than actual goals scored implies under-performance/bad luck relative to shot quality; lower implies over-performance.
xGI (Expected Goal Involvements): xGI = xg + xa. Synonyms: "xGI", "expected goal involvements", "xG+xA". The expected-stats equivalent of GI (goals + goal_assist, documented on the goals column) — compares a player''s actual GI against xGI to gauge over/under-performance across both scoring and creating.
Per 90: xg_per_90 = xg * 90.0 / NULLIF(mins_played, 0); xGI_per_90 = (xg + xa) * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting either rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.npxg IS
'Non-Penalty Expected Goals (npxG) — xg accumulated strictly from open-play and set-piece shots, excluding penalty kicks (penalties have a near-constant, very high conversion probability that would otherwise skew a player''s underlying shot-quality profile).
npxGI (Non-Penalty Expected Goal Involvements): npxGI = npxg + xa. Synonyms: "npxGI", "non-penalty expected goal involvements". The non-penalty counterpart to xGI (see the xg column) — pairs with npGI = non_penalty_goals + goal_assist (see non_penalty_goals) the same way xGI pairs with GI.
Per 90: npxg_per_90 = npxg * 90.0 / NULLIF(mins_played, 0); npxGI_per_90 = (npxg + xa) * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting either rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.xa IS
'Expected Assists (xA) — the probability, summed across all this player''s completed key passes this season, that each pass becomes a goal assist, based on the resulting shot''s quality. Synonyms: "xA", "expected assists".
Used in both xGI (xg + xa, see the xg column) and npxGI (npxg + xa, see the npxg column).
Per 90: xa_per_90 = xa * 90.0 / NULLIF(mins_played, 0). See mins_played for the qualifying-minutes convention before reporting this rate.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_player_season_stats.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values) — typically advances after every gameweek this player featured in.';


-- =============================================================================
-- fact_team_match_stats
-- Per-fixture, per-team Opta team statistics. Same Opta-native column
-- semantics as fact_player_season_stats' equivalent columns (total_pass,
-- total_scoring_att, etc.) but aggregated at the team-per-match level
-- instead of player-per-season — comments below cover only the columns not
-- already documented above, plus this table's own additions (formation,
-- possession, result, points, xga/npxg/xg team-level).
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_team_match_stats.fixture_id IS
'Part of the composite primary key (with team_id). Foreign key to dim_fixtures.fixture_id.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.season_id IS
'Foreign key to dim_seasons.season_id — denormalized from dim_fixtures for convenient season-scoped filtering without a join.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.team_id IS
'Part of the composite primary key (with fixture_id). Foreign key to dim_teams.team_id.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.is_home IS
'TRUE if this team was the home side for this fixture, FALSE if away. Equivalent to (team_id = dim_fixtures.home_team_id) but denormalized here to avoid the join.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.formation_used IS
'Starting formation, as a string (e.g. "4-3-3", "4-2-3-1").';

COMMENT ON COLUMN warehouse.fact_team_match_stats.possession_percentage IS
'Percentage of total match possession held by this team (0-100). The two teams'' values for the same fixture should sum to approximately 100.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_back_zone_pass IS
'Passes attempted from the defensive third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.accurate_back_zone_pass IS
'Passes from the defensive third that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_fwd_zone_pass IS
'Passes attempted from the attacking third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.accurate_fwd_zone_pass IS
'Passes from the attacking third that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.long_pass_own_to_opp IS
'Long passes played from this team''s own half into the opposition half.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.long_pass_own_to_opp_success IS
'Own-half-to-opposition-half long passes that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_corners_intobox IS
'Corner kicks delivered directly into the penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.accurate_corners_intobox IS
'Into-the-box corner deliveries that successfully reached a teammate.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_throws IS
'Total throw-ins taken by this team.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.attempts_obox IS
'Shot attempts taken from outside the penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.attempts_ibox IS
'Shot attempts taken from inside the penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.attempts_conceded_ibox IS
'Shot attempts conceded (by the opponent) from inside this team''s own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.attempts_conceded_obox IS
'Shot attempts conceded from outside this team''s own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.shot_off_target IS
'Shot attempts that missed the target entirely (excludes blocked and saved shots).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.first_half_goals IS
'Goals scored by this team in the first half only.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.subs_made IS
'Number of substitutions made by this team in this fixture.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.subs_goals IS
'Goals scored by substitute players introduced by this team in this fixture.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.goals_conceded_ibox IS
'Goals conceded from inside this team''s own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.goals_conceded_obox IS
'Goals conceded from outside this team''s own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.big_chance_scored IS
'Clear goalscoring opportunities (Opta-defined "big chances") that this team successfully converted.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.interception IS
'Total successful interceptions by this team in this fixture.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.defensive_actions IS
'Opta''s composite count of defensive actions (tackles, interceptions, clearances, blocks combined) by this team in this fixture.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.blocked_scoring_att IS
'Opponent shot attempts blocked by this team.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.won_contest IS
'Take-ons/dribble attempts won by this team''s players against a defender.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_contest IS
'Total take-on/dribble attempts by this team (won + lost).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.error_lead_to_goal IS
'Number of individual errors by this team''s players that directly led to an opposition goal.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.big_chance_saves IS
'Clear goalscoring opportunities (Opta-defined "big chances") that this team''s goalkeeper saved.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.fk_foul_lost IS
'Free kicks conceded by this team (fouls committed that resulted in a direct/indirect free kick for the opponent).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.ppda IS
'Passes Allowed Per Defensive Action (PPDA) — the number of opposition passes this team allowed, on average, in their own defensive two-thirds of the pitch before making a defensive action (tackle, interception, foul, challenge). Lower PPDA indicates more aggressive, higher pressing intensity; higher PPDA indicates a deeper, more passive defensive block. Synonyms: "PPDA", "pressing intensity".';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_yel_card IS
'Total yellow cards received by this team in this fixture (does not include the yellow half of a second-yellow dismissal).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_red_card IS
'Total red cards received by this team in this fixture, including second-yellow dismissals.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.pts_gained_losing_pos IS
'Points recovered by this team after having been in a losing position at some point during the fixture (e.g. 1 point for a draw after trailing, 3 for a win after trailing).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.pts_dropped_winning_pos IS
'Points lost by this team after having been in a winning position at some point during the fixture (e.g. 2 points dropped for a draw after leading, 3 for a loss after leading).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.goals_scored IS
'Final goals scored by this team in this fixture (includes own goals awarded in their favor — see fact_match_events.event_type = ''own goal'' for the individual event and which opponent conceded it).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.goals_conceded IS
'Final goals conceded by this team in this fixture.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.result IS
'Match result from this team''s perspective: ''W'' = Win, ''D'' = Draw, ''L'' = Loss.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.points IS
'Points earned from this specific fixture: 3 for a win, 1 for a draw, 0 for a loss.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.xga IS
'Expected Goals Against (xGA) — the sum of the opposition''s shot-quality-based scoring probability across all their shots faced by this team in this fixture. A team conceding fewer actual goals than xGA implies goalkeeping/defensive over-performance (or luck) relative to the chances allowed.';

-- Additional columns for fact_team_match_stats (shared Opta semantics with other tables --
-- Postgres comments are scoped per exact table+column, so these need their own statements)

COMMENT ON COLUMN warehouse.fact_team_match_stats.touches IS
'Total number of times the ball was touched.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.touches_in_final_third IS
'Touches that occurred in the attacking final third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.touches_in_opp_box IS
'Touches that occurred inside the opposition penalty area.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_pass IS
'Total passes attempted (completed + incomplete).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.accurate_pass IS
'Total passes completed successfully. Pass accuracy % = accurate_pass / NULLIF(total_pass, 0).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_final_third_passes IS
'Total passes attempted into the attacking (final) third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_long_balls IS
'Total long passes (>25-32 yards, Opta-defined threshold) attempted.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.accurate_long_balls IS
'Long passes that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_cross IS
'Total crosses attempted (passes delivered from a wide area into the box).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.accurate_cross IS
'Crosses that successfully reached a teammate.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.corner_taken IS
'Number of corner kicks taken.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.carries IS
'Total number of times the ball was carried (run with) forward.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.progressive_carries IS
'Ball carries that moved the ball significantly closer to the opposition goal (Opta-defined progressive-distance threshold).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_scoring_att IS
'Total shot attempts (shots on target + shots off target + blocked shots). Synonyms: "shots", "shot attempts", "total shots", "shots taken". This is the correct column for "how many shots did X have" -- NOT goals or ontarget_scoring_att.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.ontarget_scoring_att IS
'Shot attempts that were on target (would have gone in without a save or blocking touch, including goals). Synonyms: "shots on target", "SoT".';

COMMENT ON COLUMN warehouse.fact_team_match_stats.big_chance_missed IS
'Number of clear goalscoring opportunities (Opta-defined "big chances") received but not converted.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.big_chance_created IS
'Number of clear goalscoring opportunities (Opta-defined "big chances") created for a teammate.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.goal_assist IS
'Total assists -- passes that directly led to a goal. Synonym: "assists".';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_tackle IS
'Total tackles attempted (successful + unsuccessful).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.won_tackle IS
'Tackles that successfully won possession of the ball.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.ball_recovery IS
'Number of loose-ball recoveries (regaining possession that was not clearly won via a tackle or interception).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.dispossessed IS
'Number of times possession was lost to an opponents direct challenge.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.duel_won IS
'Total 1-on-1 duels (ground or aerial) won.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.duel_lost IS
'Total 1-on-1 duels lost.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.aerial_won IS
'Aerial (headed) duels won.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.aerial_lost IS
'Aerial (headed) duels lost.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_clearance IS
'Total defensive clearances (kicking/heading the ball away from danger).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.outfielder_block IS
'Number of shots blocked by an outfield player (not a goalkeeper save).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.penalty_faced IS
'Number of penalty kicks faced (goalkeeper-level stat, aggregated to team level here).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.pen_goals_conceded IS
'Penalty kicks that resulted in a goal against.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.poss_won_att_3rd IS
'Possession won (via tackle, interception, or recovery) in the attacking third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.poss_won_mid_3rd IS
'Possession won in the middle third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.poss_won_def_3rd IS
'Possession won in the defensive third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.total_offside IS
'Number of times flagged offside.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.non_penalty_goals IS
'Goals scored excluding penalty conversions.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.penalties_scored IS
'Penalty kicks successfully converted.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.penalties_missed IS
'Penalty kicks taken and missed (saved or off target).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.own_goals IS
'Own goals scored. Cross-reference: individual own-goal events (with fixture and minute) are in fact_match_events.own_goal_player_id, not here -- this column is only an aggregate total.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.saves IS
'Total goalkeeper saves made.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.defensive_contribution IS
'Optas composite defensive-actions metric (combines tackles, interceptions, and other defensive actions into a single count).';

COMMENT ON COLUMN warehouse.fact_team_match_stats.xg IS
'Expected Goals (xG) -- the sum of scoring probability across all shots taken, based on historical shot-quality factors (location, angle, body part, defensive pressure, assist type). Synonyms: "xG", "expected goals", "chance quality".';

COMMENT ON COLUMN warehouse.fact_team_match_stats.xa IS
'Expected Assists (xA) -- the sum of probability across all completed key passes that each becomes a goal assist, based on the resulting shots quality. Synonyms: "xA", "expected assists".';

COMMENT ON COLUMN warehouse.fact_team_match_stats.npxg IS
'Non-Penalty Expected Goals (npxG) -- xg accumulated strictly from open-play and set-piece shots, excluding penalty kicks.';


COMMENT ON COLUMN warehouse.fact_team_match_stats.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_team_match_stats.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values).';


-- =============================================================================
-- fact_team_season_stats
-- Season-aggregate version of fact_team_match_stats (summed/averaged across
-- all of a team's fixtures in the season). Column semantics are identical to
-- the matching column names in fact_team_match_stats (see that table's
-- comments above) — comments below cover only this table's structural keys.
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_team_season_stats.team_id IS
'Part of the composite primary key (with season_id). Foreign key to dim_teams.team_id.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.season_id IS
'Part of the composite primary key (with team_id). Foreign key to dim_seasons.season_id.';

-- Additional columns for fact_team_season_stats (shared Opta semantics with other tables --
-- Postgres comments are scoped per exact table+column, so these need their own statements)

COMMENT ON COLUMN warehouse.fact_team_season_stats.formation_used IS
'Starting formation, as a string (e.g. "4-3-3", "4-2-3-1").';

COMMENT ON COLUMN warehouse.fact_team_season_stats.possession_percentage IS
'Percentage of total match possession held (0-100).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.ppda IS
'Passes Allowed Per Defensive Action (PPDA) -- the number of opposition passes allowed, on average, in this teams own defensive two-thirds of the pitch before making a defensive action. Lower PPDA indicates more aggressive, higher pressing intensity. Synonyms: "PPDA", "pressing intensity".';

COMMENT ON COLUMN warehouse.fact_team_season_stats.touches IS
'Total number of times the ball was touched.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.touches_in_final_third IS
'Touches that occurred in the attacking final third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.touches_in_opp_box IS
'Touches that occurred inside the opposition penalty area.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_pass IS
'Total passes attempted (completed + incomplete).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.accurate_pass IS
'Total passes completed successfully. Pass accuracy % = accurate_pass / NULLIF(total_pass, 0).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_back_zone_pass IS
'Passes attempted from the defensive third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.accurate_back_zone_pass IS
'Passes from the defensive third that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_fwd_zone_pass IS
'Passes attempted from the attacking third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.accurate_fwd_zone_pass IS
'Passes from the attacking third that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_final_third_passes IS
'Total passes attempted into the attacking (final) third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_long_balls IS
'Total long passes (>25-32 yards, Opta-defined threshold) attempted.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.accurate_long_balls IS
'Long passes that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.long_pass_own_to_opp IS
'Long passes played from this teams own half into the opposition half.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.long_pass_own_to_opp_success IS
'Own-half-to-opposition-half long passes that were completed successfully.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_cross IS
'Total crosses attempted (passes delivered from a wide area into the box).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.accurate_cross IS
'Crosses that successfully reached a teammate.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.corner_taken IS
'Number of corner kicks taken.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_corners_intobox IS
'Corner kicks delivered directly into the penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.accurate_corners_intobox IS
'Into-the-box corner deliveries that successfully reached a teammate.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_throws IS
'Total throw-ins taken.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.carries IS
'Total number of times the ball was carried (run with) forward.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.progressive_carries IS
'Ball carries that moved the ball significantly closer to the opposition goal (Opta-defined progressive-distance threshold).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_scoring_att IS
'Total shot attempts (shots on target + shots off target + blocked shots). Synonyms: "shots", "shot attempts", "total shots", "shots taken". This is the correct column for "how many shots did X have" -- NOT goals or ontarget_scoring_att.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.ontarget_scoring_att IS
'Shot attempts that were on target (would have gone in without a save or blocking touch, including goals). Synonyms: "shots on target", "SoT".';

COMMENT ON COLUMN warehouse.fact_team_season_stats.attempts_obox IS
'Shot attempts taken from outside the penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.attempts_ibox IS
'Shot attempts taken from inside the penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.attempts_conceded_ibox IS
'Shot attempts conceded (by the opponent) from inside this teams own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.attempts_conceded_obox IS
'Shot attempts conceded from outside this teams own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.shot_off_target IS
'Shot attempts that missed the target entirely (excludes blocked and saved shots).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.first_half_goals IS
'Goals scored in the first half only.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.subs_made IS
'Number of substitutions made.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.subs_goals IS
'Goals scored by substitute players after being introduced.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.goals_conceded_ibox IS
'Goals conceded from inside this teams own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.goals_conceded_obox IS
'Goals conceded from outside this teams own penalty box.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.big_chance_missed IS
'Number of clear goalscoring opportunities (Opta-defined "big chances") received but not converted.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.big_chance_created IS
'Number of clear goalscoring opportunities (Opta-defined "big chances") created for a teammate.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.big_chance_scored IS
'Clear goalscoring opportunities (Opta-defined "big chances") that were successfully converted.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.goal_assist IS
'Total assists -- passes that directly led to a goal. Synonym: "assists".';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_tackle IS
'Total tackles attempted (successful + unsuccessful).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.won_tackle IS
'Tackles that successfully won possession of the ball.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.interception IS
'Total successful interceptions (reading and cutting out an opponents pass).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.ball_recovery IS
'Number of loose-ball recoveries (regaining possession that was not clearly won via a tackle or interception).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.dispossessed IS
'Number of times possession was lost to an opponents direct challenge.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.duel_won IS
'Total 1-on-1 duels (ground or aerial) won.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.duel_lost IS
'Total 1-on-1 duels lost.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.aerial_won IS
'Aerial (headed) duels won.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.aerial_lost IS
'Aerial (headed) duels lost.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_clearance IS
'Total defensive clearances (kicking/heading the ball away from danger).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.defensive_actions IS
'Optas composite count of defensive actions (tackles, interceptions, clearances, blocks combined).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.blocked_scoring_att IS
'Opponent shot attempts blocked.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.outfielder_block IS
'Number of shots blocked by an outfield player (not a goalkeeper save).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.won_contest IS
'Take-ons/dribble attempts won against a defender.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_contest IS
'Total take-on/dribble attempts (won + lost).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.error_lead_to_goal IS
'Number of individual errors that directly led to an opposition goal.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.big_chance_saves IS
'Clear goalscoring opportunities (Opta-defined "big chances") saved by the goalkeeper.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.fk_foul_lost IS
'Free kicks conceded (fouls committed that resulted in a direct/indirect free kick for the opponent).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.penalty_faced IS
'Number of penalty kicks faced (goalkeeper-level stat, aggregated to team level here).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.pen_goals_conceded IS
'Penalty kicks that resulted in a goal against.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.poss_won_att_3rd IS
'Possession won (via tackle, interception, or recovery) in the attacking third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.poss_won_mid_3rd IS
'Possession won in the middle third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.poss_won_def_3rd IS
'Possession won in the defensive third of the pitch.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_yel_card IS
'Total yellow cards received (does not include the yellow half of a second-yellow dismissal).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_red_card IS
'Total red cards received, including second-yellow dismissals.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.total_offside IS
'Number of times flagged offside.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.pts_gained_losing_pos IS
'Points recovered after having been in a losing position at some point during the fixture (e.g. 1 point for a draw after trailing, 3 for a win after trailing).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.pts_dropped_winning_pos IS
'Points lost after having been in a winning position at some point during the fixture (e.g. 2 points dropped for a draw after leading, 3 for a loss after leading).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.goals_scored IS
'Total goals scored (includes own goals awarded in this teams favor).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.goals_conceded IS
'Total goals conceded.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.non_penalty_goals IS
'Goals scored excluding penalty conversions.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.penalties_scored IS
'Penalty kicks successfully converted.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.penalties_missed IS
'Penalty kicks taken and missed (saved or off target).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.own_goals IS
'Own goals scored. Cross-reference: individual own-goal events (with fixture and minute) are in fact_match_events.own_goal_player_id, not here -- this column is only an aggregate total.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.saves IS
'Total goalkeeper saves made.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.defensive_contribution IS
'Optas composite defensive-actions metric (combines tackles, interceptions, and other defensive actions into a single count).';

COMMENT ON COLUMN warehouse.fact_team_season_stats.xa IS
'Expected Assists (xA) -- the sum of probability across all completed key passes that each becomes a goal assist, based on the resulting shots quality. Synonyms: "xA", "expected assists".';

COMMENT ON COLUMN warehouse.fact_team_season_stats.npxg IS
'Non-Penalty Expected Goals (npxG) -- xg accumulated strictly from open-play and set-piece shots, excluding penalty kicks.';


COMMENT ON COLUMN warehouse.fact_team_season_stats.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_team_season_stats.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values) — typically advances after every gameweek this team played.';


-- =============================================================================
-- fact_premier_league_table
-- Current league standings, with a home/away split. One row per team per
-- season; recomputed from fact_team_match_stats after every gameweek.
-- =============================================================================

COMMENT ON COLUMN warehouse.fact_premier_league_table.team_id IS
'Part of the composite primary key (with season_id). Foreign key to dim_teams.team_id.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.season_id IS
'Part of the composite primary key (with team_id). Foreign key to dim_seasons.season_id.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.matches_played IS
'Total matches played this season (Premier League fixtures only).';

COMMENT ON COLUMN warehouse.fact_premier_league_table.wins IS
'Total wins this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.draws IS
'Total draws this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.losses IS
'Total losses this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.goals_for IS
'Total goals scored this season. Synonym: "goals scored", "GF".';

COMMENT ON COLUMN warehouse.fact_premier_league_table.goals_against IS
'Total goals conceded this season. Synonym: "goals conceded", "GA".';

COMMENT ON COLUMN warehouse.fact_premier_league_table.goals_difference IS
'Goal difference (goals_for - goals_against). Synonym: "GD", "goal difference". The standard secondary sort key for league position after points.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.points IS
'Total league points this season (3 per win, 1 per draw, 0 per loss). The primary sort key for league position — ORDER BY points DESC, goals_difference DESC, goals_for DESC is the standard official tiebreak order.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_matches_played IS
'Matches played at home this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_wins IS
'Home wins this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_draws IS
'Home draws this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_losses IS
'Home losses this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_goals_for IS
'Goals scored in home matches this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_goals_against IS
'Goals conceded in home matches this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_goals_difference IS
'Goal difference from home matches only (home_goals_for - home_goals_against).';

COMMENT ON COLUMN warehouse.fact_premier_league_table.home_points IS
'Points earned from home matches only.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_matches_played IS
'Matches played away this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_wins IS
'Away wins this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_draws IS
'Away draws this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_losses IS
'Away losses this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_goals_for IS
'Goals scored in away matches this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_goals_against IS
'Goals conceded in away matches this season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_goals_difference IS
'Goal difference from away matches only (away_goals_for - away_goals_against).';

COMMENT ON COLUMN warehouse.fact_premier_league_table.away_points IS
'Points earned from away matches only.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.xg IS
'Season-total Expected Goals (xG) for this team — see fact_player_season_stats.xg for the underlying definition, summed here at team level across the whole season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.xga IS
'Season-total Expected Goals Against (xGA) for this team — see fact_team_match_stats.xga for the underlying definition, summed here across the whole season.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.xgd IS
'Expected Goal Difference (xGD) — xg minus xga for the season. The "underlying performance" analogue of goals_difference: a team with goals_difference well below xgd may be under-performing their chance quality (and vice versa).';

COMMENT ON COLUMN warehouse.fact_premier_league_table.ingested_at IS
'Audit timestamp: when this row was first written to the warehouse. Never updated after initial insert.';

COMMENT ON COLUMN warehouse.fact_premier_league_table.updated_at IS
'Audit timestamp: when this row''s data last actually changed (not merely re-upserted with identical values) — advances after every gameweek this team played.';
