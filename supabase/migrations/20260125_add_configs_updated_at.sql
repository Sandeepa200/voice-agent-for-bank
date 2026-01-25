alter table if exists configs
add column if not exists updated_at double precision;

update configs
set updated_at = extract(epoch from now())
where updated_at is null;

