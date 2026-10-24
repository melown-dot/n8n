-- Synthetic fixture only; create in separate empty DB. Never production.
CREATE TABLE "data_table_user_iS5j9YidZPEzr8ie" (route_key text,status text,received_at timestamptz,delivered_at timestamptz,event_json text);
CREATE TABLE "data_table_user_LAS4LtqRulIeMPqr" (received_at timestamptz,event_count float8,body_json text);
CREATE TABLE insights_metadata ("metaId" int,"workflowId" text);
CREATE TABLE insights_by_period ("metaId" int,type int,value bigint,"periodUnit" int,"periodStart" timestamptz);
CREATE TABLE insights_raw ("metaId" int,type int,value bigint,timestamp timestamptz);
CREATE TABLE "data_table_user_CqoaW5GoiqDVCYPA" (key text,status text,checked_at timestamptz,payload_json text);
CREATE TABLE credentials_entity (data text);
INSERT INTO "data_table_user_iS5j9YidZPEzr8ie" VALUES
('invidia_goldstar_plex116','delivered',now()-interval '100 seconds',now()-interval '40 seconds','private-fixture'),
('invidia_goldstar_plex116','received',now()-interval '120 seconds',NULL,'private-fixture'),
('invidia_goldstar_plex116','manual_review',now()-interval '180 seconds',NULL,'private-fixture'),
('different-route','received',now()-interval '1 day',NULL,'private-fixture');
INSERT INTO "data_table_user_LAS4LtqRulIeMPqr" VALUES (now(),2,'private-fixture');
INSERT INTO insights_metadata VALUES (1,'Vj9kjR4kc03NsvBT');
INSERT INTO insights_by_period VALUES (1,3,2,0,date_trunc('hour',now())),(1,2,5,0,date_trunc('hour',now())),(1,3,999,1,date_trunc('day',now()));
INSERT INTO insights_raw VALUES (1,3,1,now());
INSERT INTO "data_table_user_CqoaW5GoiqDVCYPA" VALUES ('source:kOZzC5H0IVlQKB3c','success',now(),'private-fixture');
