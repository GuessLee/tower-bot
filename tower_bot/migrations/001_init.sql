create table games (
  id bigserial primary key,
  name text not null,
  name_norm text not null unique,
  suggested_by bigint,
  created_at timestamptz not null default now(),
  last_played timestamptz,
  times_played int not null default 0,
  active boolean not null default true,
  source text not null default 'discord' check (source in ('yamtrack', 'seed', 'discord'))
);

create table nights (
  id bigserial primary key,
  guild_id bigint not null,
  channel_id bigint not null,
  message_id bigint unique,
  event_id bigint,
  starts_at timestamptz not null,
  note text,
  status text not null default 'open' check (status in ('open', 'locked', 'cancelled')),
  poster_id bigint not null,
  chosen_game_id bigint references games (id),
  chosen_override boolean not null default false,
  reminded_24h boolean not null default false,
  reminded_1h boolean not null default false,
  created_at timestamptz not null default now()
);
create index nights_open_starts on nights (starts_at) where status = 'open';

create table night_candidates (
  night_id bigint not null references nights (id) on delete cascade,
  position int not null,
  game_id bigint not null references games (id),
  emoji text not null,
  primary key (night_id, position)
);

create table polls (
  id bigserial primary key,
  guild_id bigint not null,
  channel_id bigint not null,
  message_id bigint unique,
  poster_id bigint not null,
  status text not null default 'open' check (status in ('open', 'picked', 'cancelled')),
  night_id bigint references nights (id),
  created_at timestamptz not null default now()
);

create table poll_options (
  poll_id bigint not null references polls (id) on delete cascade,
  position int not null,
  emoji text not null,
  starts_at timestamptz not null,
  primary key (poll_id, position)
);

create table pins (
  kind text primary key check (kind in ('next_up', 'library')),
  channel_id bigint not null,
  message_id bigint not null
);

create table audit_log (
  id bigserial primary key,
  at timestamptz not null default now(),
  actor_id bigint,
  action text not null,
  target text,
  detail jsonb not null default '{}'
);

create table feedback_outbox (
  id bigserial primary key,
  text text not null,
  author text not null,
  channel text not null,
  labels text[] not null,
  created_at timestamptz not null default now(),
  attempts int not null default 0,
  last_attempt_at timestamptz,
  status text not null default 'pending' check (status in ('pending', 'filed', 'failed')),
  issue_url text
);
