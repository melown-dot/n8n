BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '250ms';
WITH bounds AS (
 SELECT date_trunc('day', now() AT TIME ZONE 'Europe/Moscow') AT TIME ZONE 'Europe/Moscow' AS day_start
), ledger AS MATERIALIZED (
 SELECT status, received_at, delivered_at FROM public."data_table_user_iS5j9YidZPEzr8ie"
 WHERE route_key = 'invidia_goldstar_plex116'
), statuses(status) AS (VALUES ('received'),('retrying'),('sending'),('manual_review'),('failed'),('historical_review'),('delivered')),
queue AS (SELECT s.status, count(l.status) AS count FROM statuses s LEFT JOIN ledger l ON l.status=s.status GROUP BY s.status),
delays AS (
 SELECT extract(epoch FROM delivered_at-received_at)::double precision AS seconds FROM ledger,bounds
 WHERE status='delivered' AND delivered_at>=day_start AND delivered_at<=now() AND received_at IS NOT NULL AND delivered_at>=received_at
), flows(workflow_id) AS (VALUES ('Vj9kjR4kc03NsvBT'),('1rSvXMz8Jtjz4sCf'),('hTFtmC4KCEHRILS1'),('kOZzC5H0IVlQKB3c')),
outcomes(status,type) AS (VALUES ('success',2),('failure',3)),
insight_counts AS (
 SELECT m."workflowId" AS workflow_id,p.type,sum(p.value) AS count
 FROM public.insights_by_period p JOIN public.insights_metadata m ON m."metaId"=p."metaId",bounds
 WHERE p."periodUnit"=0 AND p.type IN (2,3) AND p."periodStart">=day_start AND p."periodStart"<=now()
 AND m."workflowId" IN (SELECT workflow_id FROM flows) GROUP BY m."workflowId",p.type
 UNION ALL
 SELECT m."workflowId",r.type,sum(r.value) FROM public.insights_raw r JOIN public.insights_metadata m ON m."metaId"=r."metaId",bounds
 WHERE r.type IN (2,3) AND r.timestamp>=day_start AND r.timestamp<=now() AND m."workflowId" IN (SELECT workflow_id FROM flows)
 GROUP BY m."workflowId",r.type
), workflow AS (
 SELECT f.workflow_id,o.status,coalesce(sum(i.count),0) AS count FROM flows f CROSS JOIN outcomes o
 LEFT JOIN insight_counts i ON i.workflow_id=f.workflow_id AND i.type=o.type GROUP BY f.workflow_id,o.status
), source_scope(scope,key) AS (VALUES ('6h','source:kOZzC5H0IVlQKB3c'),('day','source:lsaBLVQ2lrFshe9L'),('14d','source:BNfEhwt4oMRpLTCk')),
source AS (
 SELECT s.scope,CASE WHEN h.status='success' THEN 1 ELSE 0 END AS success,extract(epoch FROM h.checked_at) AS checked_timestamp_seconds
 FROM source_scope s LEFT JOIN public."data_table_user_CqoaW5GoiqDVCYPA" h ON h.key=s.key
)
SELECT json_build_object(
 'queue',(SELECT json_agg(queue) FROM queue),
 'oldest_pending_age_seconds',coalesce((SELECT greatest(0,extract(epoch FROM now()-min(received_at))) FROM ledger WHERE status IN ('received','retrying','sending')),0),
 'last_delivery_timestamp_seconds',(SELECT extract(epoch FROM max(delivered_at)) FROM ledger WHERE status='delivered'),
 'delivered_today',(SELECT count(*) FROM ledger,bounds WHERE status='delivered' AND delivered_at>=day_start AND delivered_at<=now()),
 'delivery_delay_seconds',(SELECT json_build_object('p50',percentile_cont(.5) WITHIN GROUP(ORDER BY seconds),'p95',percentile_cont(.95) WITHIN GROUP(ORDER BY seconds),'max',max(seconds),'samples',count(*)) FROM delays),
 'events_today',(SELECT coalesce(sum(event_count),0) FROM public."data_table_user_LAS4LtqRulIeMPqr",bounds WHERE received_at>=day_start AND received_at<=now()),
 'last_webhook_timestamp_seconds',(SELECT extract(epoch FROM max(received_at)) FROM public."data_table_user_LAS4LtqRulIeMPqr"),
 'workflow',(SELECT json_agg(workflow) FROM workflow),
 'source',(SELECT json_agg(source) FROM source)
);
COMMIT;
