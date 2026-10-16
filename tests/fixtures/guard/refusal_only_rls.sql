-- pgTAP: content_items RLS
begin;
select plan(3);

set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-000000000003", "role": "authenticated"}';

select throws_ok(
  $$ insert into public.approvals (client_id, decision) values ('c1', 'approved') $$,
  '42501', null, 'viewer cannot approve');

select is_empty($$ select * from public.platform_tokens $$, 'viewer sees no tokens');

do $$ begin
  update public.approvals set feedback = 'tamper';
  raise exception 'FAIL: approvals row was updated';
exception when insufficient_privilege then
  raise notice 'PASS: approvals append-only';
end $$;

select * from finish();
rollback;
