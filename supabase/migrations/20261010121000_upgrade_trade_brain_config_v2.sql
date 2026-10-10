-- Existing V1 rows keep their validated values but are explicitly labelled V2.
update public.trade_brain_configs
set config_version = 'config-v2',
    payload = jsonb_set(payload, '{config_version}', '"config-v2"'::jsonb),
    updated_at = now()
where config_id = 'active' and config_version = 'config-v1';
