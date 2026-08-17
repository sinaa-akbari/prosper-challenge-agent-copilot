-- Agent Composer — initial schema.
--
-- This is the file store's shape, moved to Postgres with the parts that were
-- implicit on disk made explicit. Three things drove the design:
--
--   * Version history was already append-only (data/agents/*/versions/NNNN.json).
--     It stays append-only here, because "the Copilot edited my production
--     agent" is only acceptable when every edit is revertible.
--   * Everything carries org_id from day one. Retrofitting tenancy onto a table
--     that already holds real calls is a migration nobody enjoys, and these
--     tables hold names, dates of birth and reasons for visit.
--   * Payloads that are read whole and never queried by field stay jsonb
--     (personas, transcripts, verdicts). Anything we filter, sort or join on is
--     a real column.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- tenancy ---
create table if not exists orgs (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

-- Everything belongs to this until real auth lands; see NOTES-FOR-DANI.md.
insert into orgs (id, name)
values ('00000000-0000-0000-0000-000000000001', 'Default')
on conflict (id) do nothing;

-- ----------------------------------------------------------------- agents ---
create table if not exists agents (
  id               text primary key,
  org_id           uuid not null references orgs(id) on delete cascade
                     default '00000000-0000-0000-0000-000000000001',
  name             text not null default 'Untitled agent',
  config           jsonb not null,
  version          int  not null default 1,
  label            text not null default '',
  source           text not null default 'manual',
  ops              jsonb not null default '[]'::jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists agents_org_updated on agents (org_id, updated_at desc);

-- Append-only. A revert writes a new row rather than deleting one, so undoing a
-- revert is the same operation as making one.
create table if not exists agent_versions (
  agent_id    text not null references agents(id) on delete cascade,
  version     int  not null,
  config      jsonb not null,
  label       text not null default '',
  source      text not null default 'manual',
  ops         jsonb not null default '[]'::jsonb,
  created_at  timestamptz not null default now(),
  primary key (agent_id, version)
);

-- ------------------------------------------------------------- test cases ---
create table if not exists test_cases (
  id            text primary key,
  agent_id      text not null references agents(id) on delete cascade,
  org_id        uuid not null default '00000000-0000-0000-0000-000000000001',
  name          text not null,
  persona       jsonb not null default '{}'::jsonb,
  assertions    jsonb not null default '[]'::jsonb,
  max_turns     int  not null default 14,
  origin        text not null default 'manual',
  source_issue  text not null default '',
  -- Set when the case was replayed from a real call. This is the column that
  -- makes the suite grow from production instead of from a model's imagination.
  source_call   text not null default '',
  ordinal       bigint not null default 0,
  created_at    timestamptz not null default now()
);
create index if not exists test_cases_agent on test_cases (agent_id, ordinal, created_at);

create table if not exists test_runs (
  id          uuid primary key default gen_random_uuid(),
  agent_id    text not null references agents(id) on delete cascade,
  org_id      uuid not null default '00000000-0000-0000-0000-000000000001',
  total       int not null default 0,
  passed      int not null default 0,
  failed      int not null default 0,
  duration_s  numeric not null default 0,
  started_at  double precision,
  results     jsonb not null default '[]'::jsonb,
  created_at  timestamptz not null default now()
);
-- "The last run" is the single hottest read in the app.
create index if not exists test_runs_latest on test_runs (agent_id, created_at desc);

-- ------------------------------------------------------------------ calls ---
-- Both simulated seed data and real Twilio calls, distinguished by `source`.
-- Keeping them in one table is deliberate: issue mining and call-to-test replay
-- shouldn't care where a transcript came from.
create table if not exists calls (
  id            text primary key,
  agent_id      text not null references agents(id) on delete cascade,
  org_id        uuid not null default '00000000-0000-0000-0000-000000000001',
  source        text not null default 'seed',       -- seed | twilio | webrtc
  outcome       text not null default 'unknown',
  duration_s    numeric not null default 0,
  flagged_by    text not null default '',
  turns         jsonb not null default '[]'::jsonb,
  path          jsonb not null default '[]'::jsonb,
  collected     jsonb not null default '{}'::jsonb,
  agent_version int,
  from_number   text not null default '',
  to_number     text not null default '',
  provider_sid  text not null default '',
  recording_url text not null default '',
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index if not exists calls_agent_created on calls (agent_id, created_at desc);
create index if not exists calls_provider_sid on calls (provider_sid) where provider_sid <> '';

-- ----------------------------------------------------------------- issues ---
create table if not exists issues (
  id              text primary key,
  agent_id        text not null references agents(id) on delete cascade,
  org_id          uuid not null default '00000000-0000-0000-0000-000000000001',
  title           text not null,
  description     text not null default '',
  severity        text not null default 'medium',
  status          text not null default 'open',
  call_count      int  not null default 0,
  affected_nodes  jsonb not null default '[]'::jsonb,
  evidence        jsonb not null default '[]'::jsonb,
  suggested_fix   text not null default '',
  created_at      timestamptz not null default now()
);
create index if not exists issues_agent on issues (agent_id, created_at desc);

-- -------------------------------------------------------------- decisions ---
-- Why a change was accepted, replayed into later Copilot prompts so it stops
-- re-opening settled arguments.
create table if not exists decisions (
  id          bigserial primary key,
  agent_id    text not null references agents(id) on delete cascade,
  org_id      uuid not null default '00000000-0000-0000-0000-000000000001',
  kind        text not null,                        -- change | retired_test | added_test
  summary     text not null,
  reason      text not null default '',
  created_at  timestamptz not null default now()
);
create index if not exists decisions_agent on decisions (agent_id, created_at);

-- ------------------------------------------------------------------- jobs ---
-- Was an in-process dict, which quietly limited the whole app to one worker.
create table if not exists jobs (
  id           text primary key,
  agent_id     text,
  kind         text not null,
  status       text not null default 'running',     -- running | done | error
  progress     jsonb not null default '{"done":0,"total":0}'::jsonb,
  partial      jsonb not null default '[]'::jsonb,
  status_text  text not null default '',
  result       jsonb,
  error        text not null default '',
  started_at   double precision,
  created_at   timestamptz not null default now()
);
create index if not exists jobs_created on jobs (created_at desc);

-- --------------------------------------------------------------------- rls ---
-- Enabled now so nothing can be written that assumes its absence. The backend
-- connects with the service role and bypasses these; they take effect the
-- moment a browser or anon key talks to PostgREST directly.
do $$
declare t text;
begin
  foreach t in array array['agents','agent_versions','test_cases','test_runs',
                           'calls','issues','decisions']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists org_isolation on %I', t);
  end loop;

  foreach t in array array['agents','test_cases','test_runs','calls','issues','decisions']
  loop
    execute format($f$
      create policy org_isolation on %I
        using (org_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'org_id', ''))
        with check (org_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'org_id', ''))
    $f$, t);
  end loop;

  -- agent_versions has no org_id of its own; it inherits from its agent.
  execute $f$
    create policy org_isolation on agent_versions
      using (exists (
        select 1 from agents a
        where a.id = agent_versions.agent_id
          and a.org_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'org_id', '')
      ))
  $f$;
end $$;
