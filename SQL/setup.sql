
-- Warehouse Shift Hub
-- Supabase database setup
-- Run in Supabase SQL Editor.
-- Existing tables are preserved where possible.

create extension if not exists pgcrypto;

-- 1. Shift records
create table if not exists public.shift_records (
    id uuid primary key default gen_random_uuid(),
    shift_date date not null,
    shift_name text not null,
    team text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (shift_date, shift_name)
);

-- 2. Questions shown on the shift form
create table if not exists public.shift_questions (
    id uuid primary key default gen_random_uuid(),
    question_key text not null unique,
    question_text text not null,
    question_type text not null default 'number',
    sort_order integer not null default 100,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

-- Add standard questions if they do not already exist.
insert into public.shift_questions
    (question_key, question_text, question_type, sort_order, is_active)
values
    ('breakdowns', 'How many breakdowns were recorded?', 'number', 95, true),
    ('ips_completed', 'How many IPS were completed?', 'number', 97, true)
on conflict (question_key) do nothing;

-- 3. Answers recorded for each shift
create table if not exists public.shift_answers (
    id uuid primary key default gen_random_uuid(),
    shift_record_id uuid not null
        references public.shift_records(id) on delete cascade,
    question_key text not null,
    answer_value jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (shift_record_id, question_key)
);

-- 4. Management-controlled sidebar links
create table if not exists public.navigation_links (
    id uuid primary key default gen_random_uuid(),
    label text not null,
    url text not null check (url ~* '^https?://'),
    sort_order integer not null default 100,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

-- Useful indexes
create index if not exists idx_shift_records_date
    on public.shift_records (shift_date desc);

create index if not exists idx_shift_answers_record
    on public.shift_answers (shift_record_id);

create index if not exists idx_navigation_links_order
    on public.navigation_links (sort_order);

-- Enable row-level security.
alter table public.shift_records enable row level security;
alter table public.shift_questions enable row level security;
alter table public.shift_answers enable row level security;
alter table public.navigation_links enable row level security;

-- The app should access these tables using the Supabase
-- service-role key stored in Streamlit Secrets.
-- Never expose that key in client-side code or GitHub.

revoke all on public.shift_records from anon, authenticated;
revoke all on public.shift_questions from anon, authenticated;
revoke all on public.shift_answers from anon, authenticated;
revoke all on public.navigation_links from anon, authenticated;

grant all on public.shift_records to service_role;
grant all on public.shift_questions to service_role;
grant all on public.shift_answers to service_role;
grant all on public.navigation_links to service_role;
