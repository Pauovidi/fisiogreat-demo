create table if not exists clinics (
  id text primary key,
  name text not null,
  timezone text not null default 'Europe/Madrid',
  created_at timestamptz not null default now()
);

create table if not exists patients (
  id uuid primary key default gen_random_uuid(),
  clinic_id text not null references clinics(id),
  phone text,
  name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, phone)
);

create table if not exists appointments (
  id uuid primary key default gen_random_uuid(),
  clinic_id text not null references clinics(id),
  patient_id uuid references patients(id),
  service_type text not null,
  start_at timestamptz not null,
  end_at timestamptz not null,
  status text not null default 'pending',
  calendar_event_id text,
  channel text not null,
  external_user_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  cancelled_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists appointments_clinic_start_idx on appointments (clinic_id, start_at);
create index if not exists appointments_external_user_idx on appointments (external_user_id);

create table if not exists conversation_sessions (
  id text primary key,
  clinic_id text,
  channel text not null,
  external_user_id text,
  state jsonb not null default '{}'::jsonb,
  emergency_detected boolean not null default false,
  emergency_match text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists booking_locks (
  id uuid primary key default gen_random_uuid(),
  clinic_id text not null,
  resource_id text not null,
  start_at timestamptz not null,
  end_at timestamptz not null,
  status text not null default 'held',
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  unique (clinic_id, resource_id, start_at, end_at, status)
);

create table if not exists reminder_jobs (
  id uuid primary key default gen_random_uuid(),
  appointment_id uuid references appointments(id),
  due_at timestamptz not null,
  channel text not null,
  status text not null default 'pending',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);

create table if not exists audit_logs (
  id uuid primary key default gen_random_uuid(),
  clinic_id text,
  actor text,
  action text not null,
  target_type text,
  target_id text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists faq_items (
  id uuid primary key default gen_random_uuid(),
  clinic_id text not null references clinics(id),
  slug text not null,
  question text not null,
  answer text not null,
  channel text not null default 'all',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, slug, channel)
);

insert into clinics (id, name, timezone)
values ('fisiogreat-demo', 'FisioGreat Demo', 'Europe/Madrid')
on conflict (id) do nothing;
