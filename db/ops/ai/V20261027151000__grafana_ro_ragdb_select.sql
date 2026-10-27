-- 대상 DB: ragdb (chargeops 아님) · primary(db-01)에서 실행
-- 목적: 운영 Grafana 데이터 소스(grafana_ro)가 RAG 런북 조각을 읽기 전용으로 조회
-- 2026-10-27 적용. pg_hba(db-01·db-02)에 hostssl ragdb grafana_ro 10.10.20.11/21/22 3줄 함께 추가함
-- 쓰기 권한은 주지 않는다(SELECT만)

GRANT CONNECT ON DATABASE ragdb TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON public.runbook_chunks TO grafana_ro;
