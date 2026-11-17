/**
 * Database row shapes and public API payloads.
 *
 * The row interfaces mirror the tested Supabase schema exactly. They are
 * intentionally permissive about enum-like columns (`string` instead of a
 * union) so an unexpected value cannot crash the read-only dashboard; the
 * bracket engine only branches on the well-known values.
 */

/** Bumped whenever a public payload changes in a breaking way. */
export const API_SCHEMA_VERSION = 1;

export interface SystemRow {
  id: string;
  name: string;
  description: string | null;
  max_players: number;
  status: string;
  registration_open: boolean;
  player_ids: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface PlayerRow {
  id: string;
  system_id: string;
  name: string;
  entry_code: string;
  telegram_id: number | null;
  telegram_username: string | null;
  status: string;
  seed: number | null;
  wins: number;
  losses: number;
  created_at: string;
  registered_at: string | null;
  eliminated_at: string | null;
  role: string;
}

export interface MatchRow {
  id: string;
  system_id: string;
  bracket: string;
  round: number;
  position: number;
  player1_id: string | null;
  player2_id: string | null;
  winner_id: string | null;
  loser_id: string | null;
  status: string;
  win_method: string | null;
  score_player1: number | null;
  score_player2: number | null;
  next_winner_match_id: string | null;
  next_loser_match_id: string | null;
  is_bye: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

// ---------------------------------------------------------------------------
// Public payloads (kept byte-for-byte compatible with the old Python API)
// ---------------------------------------------------------------------------

export interface PlayerRef {
  id: string;
  name: string;
  seed: number | null;
}

export interface SystemSummary {
  id: string;
  name: string;
  description: string;
  status: string;
  registration_open: boolean;
  max_players: number;
  player_count: number;
  match_count: number;
  ready_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface SystemListPayload {
  schema: number;
  systems: SystemSummary[];
}

export interface MatchPayload {
  id: string;
  bracket: string;
  round: number;
  position: number;
  status: string;
  player1: PlayerRef | null;
  player2: PlayerRef | null;
  winner: PlayerRef | null;
  loser: PlayerRef | null;
  score: {
    player1: number | null;
    player2: number | null;
  };
  is_bye: boolean;
  is_dead: boolean;
  started_at: string | null;
  completed_at: string | null;
}

export interface PlayerSummary {
  id: string;
  name: string;
  seed: number | null;
  status: string;
  wins: number;
  losses: number;
  eliminated: boolean;
  withdrawn: boolean;
}

export interface DashboardPayload {
  schema: number;
  generated_at: string;
  system: {
    id: string;
    name: string;
    description: string;
    status: string;
    registration_open: boolean;
    max_players: number;
    player_count: number;
    created_at: string | null;
    updated_at: string | null;
  };
  champion: PlayerRef | null;
  players: PlayerSummary[];
  brackets: {
    winners: MatchPayload[];
    losers: MatchPayload[];
    grand_final: MatchPayload[];
  };
  counts: {
    players: number;
    matches: number;
    completed: number;
    ready: number;
    active: number;
    byes: number;
    remaining: number;
  };
}
