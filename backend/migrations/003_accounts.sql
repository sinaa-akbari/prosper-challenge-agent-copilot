-- Accounts: anyone can sign up with a phone number and get their own workspace.
--
-- One org per person to begin with. That looks like an over-engineered way to
-- say "user", and it would be if orgs were added later — but every table already
-- carries org_id, so routing a personal workspace through an org costs nothing
-- now and means inviting a colleague later is a row in org_members rather than
-- a migration across every table that holds a call transcript.

create table if not exists users (
  id          uuid primary key,                    -- the Supabase auth user id
  phone       text not null unique,
  org_id      uuid not null references orgs(id) on delete cascade,
  created_at  timestamptz not null default now(),
  last_seen   timestamptz not null default now()
);
create index if not exists users_org on users (org_id);

-- Separate from `users` because membership is many-to-many the moment a second
-- person joins a workspace, and retrofitting that is the painful version.
create table if not exists org_members (
  org_id      uuid not null references orgs(id) on delete cascade,
  user_id     uuid not null references users(id) on delete cascade,
  role        text not null default 'owner',       -- owner | member
  created_at  timestamptz not null default now(),
  primary key (org_id, user_id)
);

-- Rate limiting for the sign-in code endpoint. It's open to the internet by
-- design now, and an open endpoint that sends SMS is how accounts get drained
-- by SMS pumping: an attacker triggers thousands of sends to premium-rate
-- numbers they collect revenue on. Twilio Verify caps sends per number itself;
-- this caps them per caller, which is the half Twilio can't see.
create table if not exists otp_throttle (
  bucket      text primary key,                    -- "ip:1.2.3.4" or "phone:+34…"
  count       int not null default 0,
  window_start timestamptz not null default now()
);
