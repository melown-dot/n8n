-- Operator-only first install. Existing identity is a hard collision, never silently reused.
-- NULL password prevents non-loopback SCRAM login; existing local/loopback trust under Docker authority is unchanged.
BEGIN;
DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='n8n_dashboard_metrics') THEN
 RAISE EXCEPTION 'Metrics role already exists; inspect identity and grants before any reuse';
 END IF;
 CREATE ROLE n8n_dashboard_metrics LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION CONNECTION LIMIT 1 PASSWORD NULL;
 END $$;
ALTER ROLE n8n_dashboard_metrics SET default_transaction_read_only=on;
ALTER ROLE n8n_dashboard_metrics SET statement_timeout='2s';
ALTER ROLE n8n_dashboard_metrics SET lock_timeout='250ms';
ALTER ROLE n8n_dashboard_metrics SET idle_in_transaction_session_timeout='3s';
GRANT CONNECT ON DATABASE n8n TO n8n_dashboard_metrics;
GRANT USAGE ON SCHEMA public TO n8n_dashboard_metrics;
GRANT SELECT (route_key,status,received_at,delivered_at) ON public."data_table_user_iS5j9YidZPEzr8ie" TO n8n_dashboard_metrics;
GRANT SELECT (received_at,event_count) ON public."data_table_user_LAS4LtqRulIeMPqr" TO n8n_dashboard_metrics;
GRANT SELECT ("metaId","workflowId") ON public.insights_metadata TO n8n_dashboard_metrics;
GRANT SELECT ("metaId",type,value,"periodUnit","periodStart") ON public.insights_by_period TO n8n_dashboard_metrics;
GRANT SELECT ("metaId",type,value,timestamp) ON public.insights_raw TO n8n_dashboard_metrics;
GRANT SELECT (key,status,checked_at) ON public."data_table_user_CqoaW5GoiqDVCYPA" TO n8n_dashboard_metrics;
COMMIT;
