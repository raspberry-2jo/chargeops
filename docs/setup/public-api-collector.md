# 공공 API 수집기 설치 기록 (5분 주기 · 변경분 적재 · 원본 S3 보관)

- 담당: 강영수
- 관련 WBS: 3.6
- 버전: versions.md 참고 (이미지는 `python:3.13-slim` 기반, 패키지 버전은 `apps/collector/requirements.txt`에 고정)

## 1. 왜 / 무엇을

공공데이터포털 **한국환경공단_전기자동차 충전소 정보** API에서 제주(시도 코드 50) 충전기의 기본정보와 상태를 받아 운영 DB에 쌓는다.
가동률 · 장애 통계(3.8), 품질 점검(3.9), 이상치 감지(3.10), 대시보드(3.14)가 이 데이터를 쓴다.

| 모드 | 일정 (한국 시간) | 하는 일 | API 호출 |
|---|---|---|---|
| `status` | 5분마다 (`*/5 * * * *`) | 직전 성공 이후 **바뀐 상태만** 받아 이력에 적재 | 회차당 1회 |
| `info` | 매일 03:10 | 충전기 · 충전소 · 운영기관 기본정보 전체 (제주 약 8,800대, 1쪽) | 1회 |
| `check` | 손으로만 | 건수 · 호출 확인 (적재 없음) | 1회 |

하루 약 290회 호출 → 개발계정 한도(하루 1,000회) 안. **같은 인증키로 두 곳에서 수집하지 않는다** (한도를 나눠 쓰게 됨).

데이터 흐름 (한 회차):

```
공공 API ─► 원본 JSON 그대로 S3 보관   s3://chargeops-raw/<API 이름>/<년>/<월>/<일>/<시분초>_p<쪽>.json  (UTC)
        └─► 적재 대기 테이블(stg_charger_status)에 받은 그대로 COPY
              └─► load_charger_status(run_id)
                    ├─ status_history          새 변경만 INSERT (UNIQUE(charger_id, changed_at, status_code) + ON CONFLICT DO NOTHING)
                    └─ charger_status_current  더 늦은 변경일 때만 UPDATE
        collect_log 에 회차마다 한 줄 (호출 수 · 받은 건수 · 적재 건수 · 원본 위치 · 오류)
```

| 장치 | 왜 |
|---|---|
| 한 회차 = 한 트랜잭션 | 중간에 실패하면 그 회차 적재를 전부 되돌림 (반쯤 들어간 데이터 없음) |
| `pg_advisory_xact_lock` + CronJob `concurrencyPolicy: Forbid` | 같은 종류가 겹쳐 돌지 않게 |
| 빈 구간 메우기 | 직전 **성공** 이후 지난 시간만큼 조회 범위를 넓힘(최대 10분). 그보다 길게 끊기면 다음 기본정보 수집이 충전기마다 현재 상태를 맞춤 |
| `backoffLimit: 0` | 재시도는 수집기 안에서만 (3초 · 10초). K8s 가 또 재시도하면 호출 수만 늘어남 |
| DB 주소 두 개 + `PGTARGETSESSIONATTRS=read-write` | PgBouncer 2대 중 쓰기 되는 쪽(Primary)을 libpq 가 고름 → DB 장애 전환(4.4) 때 수집기 설정을 바꾸지 않음. `PGCONNECT_TIMEOUT=3` 으로 꺼진 쪽은 3초만 기다림 |

## 2. 설치 명령어

관리 서버(mgmt)에서. 저장소는 `~/chargeops`, 비밀값은 `~/.pgpass` · `~/.chargeops.env`(권한 600)에만 있다.

```bash
# ===== 실행할 명령어 =====
# 1) 이미지 만들고 사설 레지스트리에 올리기
cd ~/chargeops/apps/collector
docker build -t registry.chargeops.lab:5000/chargeops/collector:0.1 -f Containerfile .
docker push registry.chargeops.lab:5000/chargeops/collector:0.1

# 2) 매니페스트 적용 (양식의 <...> 를 채운 사본으로. CronJob 은 suspend 로 시작)
cp collector-cronjob.example.yaml ~/collector-cronjob.yaml && vi ~/collector-cronjob.yaml
kubectl apply -f ~/collector-cronjob.yaml
kubectl -n ops create configmap chargeops-ca --from-file=ca.crt=<사설 CA 인증서>

# 3) 비밀값 → Secret (값이 명령 줄 · 파일에 남지 않게 printf 로 넘김)
source ~/.chargeops.env
kubectl -n ops create secret generic collector-secret --from-env-file=<(printf 'PGPASSWORD=%s\nEV_SERVICE_KEY=%s\nAWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\n' \
  "$(awk -F: '$4=="app_writer" && $2=="6432" {print $5; exit}' ~/.pgpass)" "$EV_SERVICE_KEY" "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY")

# 4) 일정을 켜기 전에 손으로 한 번 → 로그에 "N건 중 M건 변경 … 원본=s3://chargeops-raw/…" 확인
kubectl -n ops create job status-manual-1 --from=cronjob/collector-status
kubectl -n ops logs -f job/status-manual-1

# 5) 일정 켜기 (apply 가 아니라 patch)
kubectl -n ops patch cronjob collector-status -p '{"spec":{"suspend":false}}'
kubectl -n ops patch cronjob collector-info -p '{"spec":{"suspend":false}}'
```

## 3. 설정 파일

| 파일 | 내용 |
|---|---|
| `apps/collector/collector_v1.py` | 수집기 (check · info · status) |
| `apps/collector/Containerfile` · `requirements.txt` | 이미지 (root 아닌 사용자 10001) |
| `apps/collector/collector-cronjob.example.yaml` | ConfigMap · PVC · CronJob 3개. 주소는 `<db-01 IP>,<db-02 IP>` · `<S3 주소>` 로 비워 둠 |
| `apps/collector/check_queries.sql` | 확인 쿼리 13개 (DBeaver · psql 공용, 전부 SELECT) |
| `apps/collector/test_collector_v1.py` | 가짜 API 로 하는 시험 (인증키 · 호출 0회) |

- 비밀값은 Secret `collector-secret` 에만: `PGPASSWORD`(app_writer) · `EV_SERVICE_KEY` · `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` = `<각자 설정>`
- 환경변수 이름: 저장소판은 `.env.example` 이름(`PUBLIC_API_KEY` · `DB_*` · `S3_BUCKET_RAW`)을 먼저 읽고, 비어 있으면 실습 때 이름(`EV_SERVICE_KEY` · `PG*` · `S3_BUCKET`)을 읽는다 → 위 Secret 그대로 동작
- 수집 범위는 ConfigMap 의 `EV_ZCODE: "50"`(제주, 2026-10-12 결정). 빈 값이면 전국(기본정보만 약 53회 호출 → 한도 주의)

## 4. 확인 방법

```bash
# ===== 실행할 명령어 =====
kubectl -n ops get cronjob -l app=collector          # collector-status · collector-info 의 SUSPEND 가 False
kubectl -n ops get pods -w | grep --line-buffered collector-status   # 5분마다 새 파드 → Completed
psql -h db-01 -U dba chargeops -f ~/chargeops/apps/collector/check_queries.sql   # 또는 DBeaver 에서 같은 파일
```
```
# ===== 실행 결과 =====
# check_queries.sql [3] 최근 24시간 실적 (2026-10-14 09:33 기준, DBeaver)
 종류     | 성공 | 실패 | 빠진 회차 | API 호출 | 받은 건수 | 적재 건수 | 이미 받은 변경(건너뜀)
 상태     |  268 |    2 |        18 |      268 |    61,323 |    61,076 |                    247
 기본정보 |    1 |    0 |           |        1 |     8,766 |     8,766 |

# [4] 빠진 구간 (기록 사이가 7분 넘게 벌어진 곳)
 10-13 10:30 → 11:05   35분   6회
 10-13 18:35 → 19:40   65분  12회

# [5] 실패 회차
 10-13 19:40:04  error  호출 0  API 연결 실패 (3번 시도): ConnectTimeout
 10-13 22:55:01  error  호출 0  API 연결 실패 (3번 시도): ConnectTimeout

# [2] 최근 회차 (10-14 08:30 ~ 09:30): 5분마다 ok, 조회 범위 5~6분, 걸린 시간 0.2초 안팎
# 수집 로그 예:  [status] 344건 중 344건 변경, API 1회 호출, 원본=s3://chargeops-raw/getChargerStatus/2026/10/13/090501_p1.json
```

판정 (완료 기준 "5분 주기 · 변경분만 적재 · 원본 S3 보관")

| 기준 | 결과 |
|---|---|
| 5분 주기 | ✅ 기록된 270회 중 268회 성공. 빠진 18회는 두 구간뿐이고 수집기가 아니라 실습 환경이 멈춰 있던 시간 (5장 1번) |
| 변경분만 적재 | ✅ 받은 61,323건 중 247건은 앞 회차에서 이미 받은 변경이라 건너뜀 (조회 범위 5~6분이 겹치는 부분) → 두 번째 호출부터 바뀐 것만 저장 |
| 원본 S3 보관 | ✅ 회차마다 `collect_log.raw_key` = `s3://chargeops-raw/…` (경로의 날짜 · 시각은 UTC) |
| 호출 한도 | ✅ 하루 약 270회 (한도 1,000회) |

## 5. 막혔던 점

1. **수집이 빠진 두 구간 (10-13 10:35~11:00 · 18:40~19:35)** — 수집기 문제가 아니라 실습 VM 이 멈춰 있던 시간(재시작 · PC 절전).
   그 회차들은 `collect_log` 에도, K8s 의 실패 Job 목록(`failedJobsHistoryLimit: 5`)에도 없다 → CronJob 이 Job 자체를 만들지 못했다는 뜻.
   `startingDeadlineSeconds: 120` 이라 2분 넘게 늦은 회차는 나중에 몰아서 돌지 않는다 (다시 켰을 때 호출이 한꺼번에 몰리지 않게 일부러 둔 값).
   10-13 11:00 회차는 Job 이 떴지만 DB 가 아직 안 올라와 `DB 접속 실패: connection timeout expired` 로 끝났다 — DB 에 못 붙으면 기록을 못 남기므로 `kubectl -n ops logs` 로만 보인다
2. **실패 2회 = 공공 API 접속 실패** (`ConnectTimeout`, 수집기 안에서 3번 시도 뒤 포기). 19:40 은 멈췄던 환경이 다시 돈 첫 회차, 22:55 는 일시적.
   둘 다 호출 0회 · 적재 0건으로 기록되고(한 회차 = 한 트랜잭션) 다음 회차는 정상
3. **빈 구간을 메울 수 있는 한도** — 상태 API 는 최근 10분 변경까지만 준다. 그래서 실패 1~2회(10분 안)는 다음 회차가 조회 범위를 넓혀 메우지만,
   35분 · 65분처럼 길게 멈추면 그 사이 상태 변화 이력 일부는 받을 수 없다. 충전기별 마지막 상태는 다음 기본정보 수집(매일 03:10)이 맞춘다.
   → 가동률 · 품질 계산(3.8 · 3.9)에서는 이 구간을 '수집 공백'으로 따로 본다 (`check_queries.sql` [4] 로 찾음)
4. **공공 API 가 느릴 때** — 평소 0.2초, 응답이 늦으면 수집기 안의 재시도(3초 · 10초 대기)로 13초 넘게 걸린다 (10-14 08:45 회차 13.4초).
   `activeDeadlineSeconds: 240` 안이라 문제없음. 이 회차는 성공이지만 조회 시점에 따라 '아직 안 들어옴'으로 보일 수 있다
5. **매니페스트를 다시 apply 하면 수집이 조용히 멈춤** — CronJob 이 `suspend: true` 로 돌아간다. 이미 켠 뒤 값만 바꿀 때는
   `kubectl -n ops patch configmap collector-config --type merge -p '{"data":{…}}'` (다음 회차 파드부터 적용)
6. **첫 수동 실행 때 `logs -f` 가 아무것도 안 보여 줌** — 이미지 받기 · PVC 만들기로 20초쯤 걸린다. `kubectl -n ops get jobs,pods` 가 `Complete` 인지 보고 다시 `logs`
7. **같은 인증키로 두 곳에서 수집 금지** — 다른 PC 에 같은 환경을 만들면 먼저 `kubectl -n ops get cronjob` 의 `SUSPEND` 가 `True` 인지 확인.
   `collect_log` 의 호출 수에는 다른 곳에서 부른 호출이 안 보인다
8. **DBeaver 에서 `check_queries.sql` 을 파일로 열면 한글이 깨짐** — 파일은 UTF-8, DBeaver 는 Windows 기본(MS949)으로 읽는다.
   창 → 설정 → 일반 → 작업 공간 → 텍스트 파일 인코딩 → 기타 `UTF-8` 뒤 파일을 다시 연다 (쿼리 글자까지 깨져서 결과의 한글 값도 `����` 로 나옴)
