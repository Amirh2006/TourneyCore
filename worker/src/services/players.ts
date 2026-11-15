import type { Env } from '../env';
import { ApiError, BotError } from '../lib/errors';
import { getSupabase } from '../lib/supabase';
import type { PlayerRow } from '../types';

/** Normalize an entry code the same way the Python model did. */
export function normalizeEntryCode(code: string): string {
  return code.trim().toUpperCase().replace(/-/g, '').replace(/\s+/g, '');
}

/** Return the player linked to a Telegram account, if any. */
export async function findPlayerByTelegramId(
  env: Env,
  telegramId: number,
): Promise<PlayerRow | null> {
  const { data, error } = await getSupabase(env)
    .from('players')
    .select('*')
    .eq('telegram_id', telegramId)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) {
    throw new ApiError(
      502,
      'database_error',
      `Could not read players: ${error.message}`,
    );
  }

  return (data as PlayerRow | null) ?? null;
}

/**
 * Redeem an entry code for a Telegram account.
 *
 * Mirrors `StorageManager.redeem_entry_code`: the code must exist, must not
 * already be linked, and the Telegram account must not be linked to a
 * different player. The final UPDATE is conditional on `telegram_id IS NULL`
 * so two people racing for the same code cannot both win.
 */
export async function redeemEntryCode(
  env: Env,
  rawCode: string,
  telegramId: number,
  username: string | null,
): Promise<PlayerRow> {
  const code = normalizeEntryCode(rawCode);
  if (!code) {
    throw new BotError('Usage: /register A1B2C3D4');
  }

  const supabase = getSupabase(env);

  const { data: found, error: findError } = await supabase
    .from('players')
    .select('*')
    .eq('entry_code', code)
    .maybeSingle();

  if (findError) {
    throw new ApiError(
      502,
      'database_error',
      `Could not read players: ${findError.message}`,
    );
  }

  const player = found as PlayerRow | null;
  if (!player) {
    throw new BotError(`Unknown entry code '${code}'.`);
  }
  if (player.telegram_id !== null) {
    throw new BotError(`Entry code '${code}' was already redeemed.`);
  }

  const { data: other, error: otherError } = await supabase
    .from('players')
    .select('id')
    .eq('telegram_id', telegramId)
    .neq('id', player.id)
    .limit(1)
    .maybeSingle();

  if (otherError) {
    throw new ApiError(
      502,
      'database_error',
      `Could not read players: ${otherError.message}`,
    );
  }
  if (other) {
    throw new BotError(
      `Telegram account ${telegramId} is already linked to another player.`,
    );
  }

  const normalizedUsername = username
    ? username.replace(/^@+/, '') || null
    : null;

  const { data: updated, error: updateError } = await supabase
    .from('players')
    .update({
      telegram_id: telegramId,
      telegram_username: normalizedUsername,
      status: 'registered',
      registered_at: new Date().toISOString(),
    })
    .eq('id', player.id)
    .is('telegram_id', null)
    .select('*')
    .maybeSingle();

  if (updateError) {
    throw new ApiError(
      502,
      'database_error',
      `Could not update the player: ${updateError.message}`,
    );
  }
  if (!updated) {
    // Somebody redeemed the same code between our read and our write.
    throw new BotError(`Entry code '${code}' was already redeemed.`);
  }

  return updated as PlayerRow;
}
