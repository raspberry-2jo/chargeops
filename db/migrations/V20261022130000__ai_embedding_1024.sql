-- ============================================================================
-- V20261022130000__ai_embedding_1024.sql  (WBS 3.16 · 3.17 DB 지원 — AI 게이트웨이 · RAG 연동)
--   장애 임베딩 차원 768 → 1024 — 10/22 회의 결정. 런북 검색(ragdb.runbook_chunks)과 같은 임베딩 모델(bge-m3) 사용
--   768 은 실습용 ai-gateway(nomic-embed-text) 기준 임시값이었음
--   표가 비어 있을 때만 바꾼다 (10/22 확인 0행). 행이 있으면 멈춤 → 그 행을 새 모델로 다시 임베딩한 뒤 적용
--   HNSW 인덱스(incident_embedding_hnsw)는 ALTER 할 때 자동으로 다시 만들어진다
--   Primary 에서 postgres(소유자)로, DB chargeops 에 적용. 운영 서버 적용 2026-10-22
-- ============================================================================
BEGIN;
SET LOCAL lock_timeout = '5s';           -- 다른 작업이 표를 잡고 있으면 5초만 기다리고 포기 (CSMS 를 오래 막지 않게)
LOCK TABLE incident_embedding IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM incident_embedding) THEN
    RAISE EXCEPTION 'incident_embedding has rows: stop (they must be re-embedded first)';
  END IF;
END $$;
ALTER TABLE incident_embedding ALTER COLUMN embedding TYPE vector(1024);
COMMENT ON TABLE incident_embedding IS '유사 장애 검색용 임베딩 (bge-m3 1024차원, HNSW 근사 검색)';
COMMIT;
