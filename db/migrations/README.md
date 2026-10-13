# db/migrations — DDL (WBS 1.4 · 2.6)

운영 DB(PostgreSQL 17)의 테이블 · 코드 · 적재 함수 · 계정을 **순서대로 쌓는 SQL**입니다.
표 하나하나의 뜻은 [`docs/db/table-definitions.md`](../../docs/db/table-definitions.md), 그림은 [`docs/db/erd/`](../../docs/db/erd/)에 있습니다.

## 파일 (이름 순서 = 적용 순서)

| 파일 | 내용 | 실습 스키마 v1.1 |
|---|---|---|
| `V20261013113000__core_tables.sql` | 코드표 · 운영기관 · 충전소 · 충전기 · 현재 상태 · 상태 이력(월 파티션) · 수집 기록 · 적재 대기 | `01_tables.sql` |
| `V20261013113100__load_functions.sql` | 적재 대기(`stg_*`) → 본 테이블 적재 함수. `apps/collector`가 부름 | `02_load_functions.sql` |
| `V20261013113200__seed_codes.sql` | 공공 API 활용가이드 공통 코드 (기준 데이터) | `03_seed_codes.sql` |
| `V20261013113300__arch_codes.sql` | 공공 API 상태 코드 ↔ OCPP 상태 대응, 고장 코드, `is_down` | `10_arch_codes.sql` |
| `V20261013113400__arch_tables.sql` | 계측 · 장애 · 감사 · 승인 · 권한 · 알림 · AI · 이상치 · 품질 · 통계 테이블, 파티션 함수 | `11_arch_tables.sql` |
| `V20261013113500__roles_and_grants.sql` | 계정 6개와 최소 권한, 감사 기록 추가만 허용 트리거. **비밀번호 없음** | `14_security.sql` |

## 규칙

- 이름은 `V날짜시간__설명.sql` (README K5). 번호(`V003`)로 지으면 두 사람이 같은 번호를 만듭니다.
- **이미 적용한 파일은 고치지 않습니다.** 바꿀 것이 생기면 새 파일(`ALTER …`)을 더합니다.
- 공용 폴더라 시작 전에 Slack에 알리고, 그 변경만 작은 PR로 올립니다 (K3).
- 파일 안에 비밀번호 · 키를 쓰지 않습니다 (S1 · S6).
- psql 전용 명령(`\echo`, `\set` 등)을 쓰지 않아서, psql이든 마이그레이션 도구든 그대로 실행됩니다.

## 적용 (빈 DB에 한 번)

Primary DB 서버에서 **postgres**(테이블 소유자) 권한으로 실행합니다.

```bash
# ===== 실행할 명령어 =====
sudo -u postgres createdb chargeops
for f in db/migrations/V*.sql; do echo "== $f"; sudo -u postgres psql -X -q -d chargeops -v ON_ERROR_STOP=1 < "$f" || break; done
sudo -u postgres psql -d chargeops -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename NOT LIKE '%\_20%' AND tablename NOT LIKE '%default'"
```

```
# ===== 실행 결과 =====
== db/migrations/V20261013113000__core_tables.sql
…
27
```

- 파일을 표준입력(`<`)으로 넘겨서, postgres 계정이 내 홈 폴더를 읽을 권한이 없어도 됩니다.
- 마지막 숫자 27 = 테이블 수입니다 (월 파티션 · default 파티션 제외).

## 비밀번호 설정 (적용한 뒤 사람이 직접)

역할은 만들어지지만 비밀번호가 없어서 아직 접속할 수 없습니다. 값은 저장소 · 명령 줄 · 셸 기록에 남기지 않습니다.

```bash
# ===== 실행할 명령어 =====
sudo -u postgres psql -d chargeops
```

```sql
-- psql 안에서 역할마다 (입력이 화면에 보이지 않고, SCRAM 해시로 저장됨)
\password app_writer
\password app_reader
\password report_ro
\password dba
\password replicator
\password postgres_exporter
```

앱 · 도구 쪽은 각자 `.env`(루트 `.env.example` 양식)나 `~/.pgpass`(권한 600)에 넣습니다.

## 아직 이 폴더에 없는 것

| 무엇 | 어디로 | 담당 (WBS) |
|---|---|---|
| 가동률 · MTTR · 운영기관 통계 뷰와 구체화 뷰 (실습 `12_stats.sql`) | `db/views/` | 성주원 · 윤하빈 (3.8) |
| 이상치 점수 뷰 · 감지 함수 (실습 `13_anomaly_dq.sql` 앞부분) | `db/views/` | 조인성 · 강영수 (3.10) |
| 데이터 품질 점검 함수 (실습 `13_anomaly_dq.sql` 90줄부터) | `db/quality/` | 조인성 (3.9) |
| 위 뷰 · 함수의 권한 (`report_ro` 조회, 감지 · 품질 함수 실행) | 각 파일 끝에서 GRANT | 각 담당 |
| DB 안 예약 작업 (pg_cron: 가동률 10분, 일 통계, 파티션, 장기 방치) | 위 뷰가 들어온 뒤 새 마이그레이션 | 데이터팀 |

## 팀에서 정한 뒤 새 파일로 바꿀 것

결정 ① 이력 보관 범위 · 같은 상태 반복 적재, ② 10분 갱신 지역(`stat_scope`, 지금 제주 `50`), ③ 고장 정의(`code_status.is_down`), ④ MTTR 두 지표, ⑦ 임베딩 차원(`incident_embedding`, 지금 768), WBS 1.5 권한 매트릭스(`ops_role`). 자세한 내용은 테이블 정의서 0장.
