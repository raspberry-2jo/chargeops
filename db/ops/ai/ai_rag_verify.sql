-- ============================================================================
-- db/ops/ai/ai_rag_verify.sql — AI 게이트웨이 · RAG 계정 권한 확인 (읽기만, 바꾸는 것 없음)
--   실행: db-01 에서 postgres 로, DB ragdb
--     ssh db-01 "sudo -u postgres psql -X -d ragdb" < db/ops/ai/ai_rag_verify.sql
--   16줄 모두 OK 여야 함. NG 가 있으면 멈추고 확인 (vector 버전은 서버에 깔린 값 0.8.7 기준)
--   되어야 할 것(접속 · 쓰기 · 읽기)과 막혀야 할 것(다른 DB 접속 · 테이블 생성 · 읽기 계정의 쓰기)을 같이 본다
-- ============================================================================
SELECT item, expected, actual, CASE WHEN expected = actual THEN 'OK' ELSE 'NG' END AS result
FROM (VALUES
  ('litellm owns DB litellm (can create its tables)',
     'litellm', (SELECT pg_get_userbyid(datdba)::text FROM pg_database WHERE datname = 'litellm')),
  ('litellm -> litellm connect',     'yes', CASE WHEN has_database_privilege('litellm',     'litellm', 'CONNECT') THEN 'yes' ELSE 'no' END),
  ('litellm -> ragdb connect',       'no',  CASE WHEN has_database_privilege('litellm',     'ragdb',   'CONNECT') THEN 'yes' ELSE 'no' END),
  ('rag_indexer -> ragdb connect',   'yes', CASE WHEN has_database_privilege('rag_indexer', 'ragdb',   'CONNECT') THEN 'yes' ELSE 'no' END),
  ('rag_reader -> ragdb connect',    'yes', CASE WHEN has_database_privilege('rag_reader',  'ragdb',   'CONNECT') THEN 'yes' ELSE 'no' END),
  ('rag_indexer -> litellm connect', 'no',  CASE WHEN has_database_privilege('rag_indexer', 'litellm', 'CONNECT') THEN 'yes' ELSE 'no' END),
  ('rag_reader -> litellm connect',  'no',  CASE WHEN has_database_privilege('rag_reader',  'litellm', 'CONNECT') THEN 'yes' ELSE 'no' END),
  ('app_writer -> ragdb connect',    'no',  CASE WHEN has_database_privilege('app_writer',  'ragdb',   'CONNECT') THEN 'yes' ELSE 'no' END),
  ('app_writer -> litellm connect',  'no',  CASE WHEN has_database_privilege('app_writer',  'litellm', 'CONNECT') THEN 'yes' ELSE 'no' END),
  ('rag_indexer write rows',         'yes', CASE WHEN has_table_privilege('rag_indexer', 'runbook_chunks', 'INSERT,UPDATE,DELETE') THEN 'yes' ELSE 'no' END),
  ('rag_indexer create table',       'no',  CASE WHEN has_schema_privilege('rag_indexer', 'public', 'CREATE') THEN 'yes' ELSE 'no' END),
  ('rag_reader read rows',           'yes', CASE WHEN has_table_privilege('rag_reader',  'runbook_chunks', 'SELECT') THEN 'yes' ELSE 'no' END),
  ('rag_reader write rows',          'no',  CASE WHEN has_any_column_privilege('rag_reader', 'runbook_chunks', 'INSERT,UPDATE') OR has_table_privilege('rag_reader', 'runbook_chunks', 'DELETE') THEN 'yes' ELSE 'no' END),
  ('connection limits',              '20/3/7',
     (SELECT string_agg(rolconnlimit::text, '/' ORDER BY array_position(ARRAY['litellm','rag_indexer','rag_reader'], rolname::text))
        FROM pg_roles WHERE rolname IN ('litellm', 'rag_indexer', 'rag_reader'))),
  ('runbook_chunks embedding type',  'vector(1024)',
     (SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = 'runbook_chunks'::regclass AND attname = 'embedding')),
  ('vector extension in ragdb',      '0.8.7', (SELECT extversion FROM pg_extension WHERE extname = 'vector'))
) AS t(item, expected, actual);
