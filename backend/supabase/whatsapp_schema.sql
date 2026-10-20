-- Supabase dedicated to the Salomão WhatsApp service (main_hubspot:app).
-- Run once in the new project's SQL Editor. Safe to re-run: nothing is dropped.
--
-- Columns mirror what database.py, conversation_memory.py and
-- operational_health.py write. The service authenticates with the project's
-- service_role key, stored only in Railway (never in a frontend). RLS is on and
-- there are NO policies, so the public anon/publishable key cannot read or write.

create extension if not exists pgcrypto;

create table if not exists public.salomao_sessions (
    id uuid primary key default gen_random_uuid(),
    session_id text not null unique,          -- upsert target (on_conflict=session_id)
    user_identifier text,
    message_count integer not null default 0,
    topics_discussed jsonb not null default '[]'::jsonb,
    metadata jsonb not null default '{}'::jsonb,  -- memory checkpoint and health probe
    last_activity_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.salomao_messages (
    id uuid primary key default gen_random_uuid(),
    session_id text not null,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    has_image boolean not null default false,
    has_audio boolean not null default false,
    audio_transcription text,
    model_used text,
    transfer_requested boolean not null default false,
    created_at timestamptz not null default now()
);
create index if not exists salomao_messages_session_created on public.salomao_messages (session_id, created_at desc);

-- Analytics turns and conversation summaries (memory_type distinguishes them).
create table if not exists public.salomao_memories (
    id uuid primary key default gen_random_uuid(),
    session_id text not null,
    memory_type text not null,
    memory_content text not null,
    created_at timestamptz not null default now()
);
create index if not exists salomao_memories_session_type on public.salomao_memories (session_id, memory_type, created_at desc);

create table if not exists public.salomao_message_ratings (
    id uuid primary key default gen_random_uuid(),
    message_id text not null,
    session_id text not null,
    rating text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists salomao_message_ratings_message on public.salomao_message_ratings (message_id);

create table if not exists public.salomao_session_feedback (
    id uuid primary key default gen_random_uuid(),
    session_id text not null,
    rating text,
    comment text,
    transfer_requested boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists salomao_session_feedback_session on public.salomao_session_feedback (session_id);

-- Keep updated_at current on updates.
create or replace function public.salomao_touch_updated_at() returns trigger
language plpgsql as $$ begin new.updated_at = now(); return new; end $$;

do $$
declare t text;
begin
    foreach t in array array['salomao_sessions', 'salomao_message_ratings', 'salomao_session_feedback'] loop
        execute format('drop trigger if exists %I_touch on public.%I', t, t);
        execute format('create trigger %I_touch before update on public.%I for each row execute function public.salomao_touch_updated_at()', t, t);
    end loop;
    foreach t in array array['salomao_sessions', 'salomao_messages', 'salomao_memories',
                             'salomao_message_ratings', 'salomao_session_feedback'] loop
        execute format('alter table public.%I enable row level security', t);
        execute format('revoke all on public.%I from anon, authenticated', t);
    end loop;
end $$;
