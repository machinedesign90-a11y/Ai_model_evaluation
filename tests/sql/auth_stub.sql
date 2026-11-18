-- Minimal stand-in for the parts of Supabase's auth schema the migration uses.
-- Roles are cluster-wide, so they are created by the harness, not here.
create schema if not exists auth;

create table auth.users (
  id    uuid primary key,
  email text
);

-- Supabase reads the JWT subject from request settings; tests set
-- request.jwt.claim.sub to act as a user.
create or replace function auth.uid() returns uuid
language sql stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

grant usage on schema auth to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;

-- Supabase's default privileges: anon and authenticated get ALL on new tables and
-- EXECUTE on new functions in public. Reproducing them here means the tests
-- exercise row-level security and the explicit REVOKEs, not just missing grants.
grant usage on schema public to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
alter default privileges in schema public grant all on sequences to anon, authenticated;
alter default privileges in schema public grant execute on functions to anon, authenticated;
