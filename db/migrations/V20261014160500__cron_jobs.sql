-- ============================================================================
-- V20261014160500__cron_jobs.sql  (WBS 3.8 후속 — DB 안 예약 작업 (pg_cron))
--   출처: 실습 스키마 15_cron.sql 의 상시 작업 6개
--   전제: postgresql 설정에 shared_preload_libraries 의 pg_cron · cron.database_name · cron.timezone = 'Asia/Seoul'
--         (db/ops/postgresql/chargeops.conf 에 있음). 없으면 CREATE EXTENSION 에서 멈춘다
--   실습판과 다른 점: sim- 작업 2개(이상치 1분 · 품질 매일)는 뺌 — K8s CronJob(anomaly-job · dq-job)이 같은 일을 한다
--                     K8s 없이 DB 만으로 돌릴 때의 예시는 db/migrations/README.md
--   같은 이름이면 덮어쓰므로 다시 실행해도 된다
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS pg_cron;
SELECT cron.schedule('refresh-uptime', '*/10 * * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_uptime');
SELECT cron.schedule('stat-daily',     '20 0 * * *',   'SELECT fn_refresh_stat_operator_daily()');
SELECT cron.schedule('partitions',     '0 3 25 * *',   'SELECT fn_ensure_partitions(2)');      -- 매달 25일: 다음 달 · 다다음 달
SELECT cron.schedule('long-fault',     '0 9 * * *',    'SELECT fn_detect_long_fault()');
SELECT cron.schedule('cron-history-cleanup', '30 4 * * *',
       $$DELETE FROM cron.job_run_details WHERE end_time < now() - interval '7 days'$$);   -- 1분 잡 기록이 쌓이지 않게
SELECT cron.schedule('analyze-parents', '30 3 * * *', 'ANALYZE status_history, meter_sample');   -- 분할 표의 상위 통계 (자동 VACUUM 이 안 해 줌, 2026-10-15 튜닝)
