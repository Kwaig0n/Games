-- DogWalk – Supabase schema
-- Run this in the Supabase SQL Editor: supabase.com → your project → SQL Editor

-- Shared routes
create table if not exists routes (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  polyline     text not null,
  distance_km  numeric not null,
  tags         text[] default '{}',
  notes        text default '',
  lat          numeric not null,
  lng          numeric not null,
  device_id    text,
  created_at   timestamptz default now()
);

-- Paw ratings on routes
create table if not exists route_ratings (
  id         uuid primary key default gen_random_uuid(),
  route_id   uuid references routes(id) on delete cascade,
  stars      integer check (stars between 1 and 5),
  tags       text[] default '{}',
  device_id  text,
  created_at timestamptz default now()
);

-- Row Level Security (anon key can read + insert, never delete others' data)
alter table routes        enable row level security;
alter table route_ratings enable row level security;

create policy "public read"   on routes        for select using (true);
create policy "public insert" on routes        for insert with check (true);
create policy "public read"   on route_ratings for select using (true);
create policy "public insert" on route_ratings for insert with check (true);

-- Photos attached to routes
create table if not exists route_photos (
  id            uuid primary key default gen_random_uuid(),
  route_id      uuid references routes(id) on delete cascade,
  storage_path  text not null,
  device_id     text,
  created_at    timestamptz default now()
);

alter table route_photos enable row level security;

create policy "public read"   on route_photos for select using (true);
create policy "public insert" on route_photos for insert with check (true);

-- Storage bucket (run once in Supabase dashboard → Storage)
-- insert into storage.buckets (id, name, public) values ('route-photos', 'route-photos', true)
--   on conflict (id) do nothing;
