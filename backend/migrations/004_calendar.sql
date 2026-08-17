-- Calendar connections.
--
-- One Google account per workspace, then each agent picks a calendar inside it.
-- That split matters: OAuth consent is something a person grants once, while
-- "which calendar does this agent book into" is a property of the agent and
-- changes as often as any other node setting.
--
-- Tokens are encrypted at the application layer as well as at rest. Supabase
-- already encrypts the disk, but a refresh token is a standing grant to read
-- and write someone's calendar — it should not be legible to anyone who can run
-- a select.

create table if not exists calendar_connections (
  org_id        uuid primary key references orgs(id) on delete cascade,
  provider      text not null default 'google',
  account_email text not null default '',
  -- Fernet ciphertext, not the tokens themselves. See integrations/secrets.py.
  access_token  text not null default '',
  refresh_token text not null default '',
  expires_at    timestamptz,
  scopes        text not null default '',
  connected_by  text not null default '',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

alter table calendar_connections enable row level security;
drop policy if exists org_isolation on calendar_connections;
create policy org_isolation on calendar_connections
  using (org_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'org_id', ''));

-- Short-lived state for the OAuth round trip. A row here is proof that *we*
-- started the flow, which is what stops a stranger replaying a callback.
create table if not exists oauth_states (
  state       text primary key,
  org_id      uuid not null references orgs(id) on delete cascade,
  agent_id    text,
  created_at  timestamptz not null default now()
);
