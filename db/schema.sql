create table if not exists events (
  id bigserial primary key,
  session_id uuid not null,
  type text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists events_created_at_idx on events (created_at);
create index if not exists events_session_id_idx on events (session_id);
