-- Migration 4: keep the review queue private.
--
-- Supabase grants anon and authenticated SELECT on new views in public by default,
-- and a view normally reads its tables with its owner's rights, so row-level security
-- on `queries` did not apply through `review_queue`: any signed-in user could read
-- every logged question. Two independent fixes:
--   1. security_invoker: the view runs with the caller's rights, so RLS on `queries`
--      applies (clients have no policy there, so they see nothing).
--   2. revoke client access to the view altogether (service role only, like the table).
-- Requires PostgreSQL 15+ (Supabase runs 15 or later).

begin;

alter view review_queue set (security_invoker = true);
revoke all on review_queue from public, anon, authenticated;

commit;
