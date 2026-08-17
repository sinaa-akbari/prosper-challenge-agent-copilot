-- Runtime settings that outlive a deploy.
--
-- The phone allowlist started life in .env, which meant adding your own number
-- required ssh — and a login method you can't enrol yourself into is a login
-- method nobody uses. It lives here instead so the app can manage it, while
-- AUTH_ALLOWED_PHONES stays supported as a bootstrap for a fresh deployment.

create table if not exists app_settings (
  key         text primary key,
  value       jsonb not null,
  updated_at  timestamptz not null default now()
);

-- Deliberately no RLS and no org_id: this is deployment-level configuration,
-- read before anyone has a session and therefore before there's a tenant to
-- scope it to.
