# AWS S3 원격 백업 설치 기록 (pgBackRest repo2 · 수집 원본 · 정기 백업)

- 담당: 강영수
- 관련 WBS: 2.8 후속(원격 보관) · 3.6 원본 S3 보관 · 4.5 DR 훈련 준비
- 버전: versions.md 참고 (pgBackRest 2.59, PostgreSQL 17)
- 상태: **개인 AWS 계정으로 시험 완료 (2026-10-14)**. 팀 계정은 2.2 의 IAM 을 받은 뒤 같은 절차로 버킷 이름 · 키 · 경로만 바꾼다 (6장)
- 앞 단계: [`pgbackrest-pitr.md`](pgbackrest-pitr.md) (repo1 = 실습 MinIO), [`public-api-collector.md`](public-api-collector.md)

## 1. 왜 / 무엇을

- 2.8 까지의 백업 저장소(repo1)는 실습망의 MinIO — **같은 PC 안**이라 PC 가 고장 나면 백업도 함께 사라진다. DR(4.5)을 하려면 **바깥(AWS S3 서울)** 에 사본이 있어야 한다.
- repo1 은 그대로 두고(빠른 복구) repo2 = AWS S3 를 **더한다**(원격 · DR). 수집기 원본 JSON 도 AWS 원본 버킷으로 보낸다.

```text
db-01 (Primary) ── archive_command: WAL 을 최대 60초마다 ──┬─▶ repo1  MinIO   /chargeops                 (빠른 복구)
                └─ 정기 백업 타이머 (매일 03:40)  ──────────┴─▶ repo2  AWS S3  <백업 버킷>/test-chargeops  (원격 · DR)
db-02 (Standby) ── 같은 설정 · 같은 타이머: 승격되면 이어서 올림 (Standby 인 동안은 타이머가 그냥 끝남)
수집기 CronJob  ── 공공 API 원본 JSON (5분마다) ───────────────▶ AWS S3  <원본 버킷>/getChargerStatus/<년>/<월>/<일>/<시분초 UTC>_p1.json
```

| 누가 | 무엇으로 들어가나 | 언제 |
|---|---|---|
| 사람 | AWS 콘솔 로그인(루트 · 관리자) | 처음 설정할 때 · 보고 싶을 때만 |
| 서버 프로그램(pgBackRest · 수집기) | 실습 VM 용 IAM 사용자의 **액세스 키** (콘솔 로그인 없음) | 매번 자동. 설정 파일 · Secret 에 한 번 넣으면 끝 |

**최소 권한** — 키가 새도 피해가 "우리 칸"에서 끝나게 ([`db/ops/pgbackrest/aws-s3-policy.example.json`](../../db/ops/pgbackrest/aws-s3-policy.example.json), pgBackRest 공식 샘플 정책 기준):

| 권한 | 범위 | 누가 · 왜 | 없으면 |
|---|---|---|---|
| `s3:ListBucket` | 백업 버킷의 `test-chargeops/` | pgBackRest: 백업 · WAL 이 끊김 없이 이어지는지 확인, 정리할 옛 백업 찾기 | check · backup · info 실패 |
| `s3:PutObject` | 〃 | WAL · 백업 파일 올리기 | 백업 자체가 안 됨 |
| `s3:GetObject` | 〃 | 복원(PITR) · 정보 파일 읽기 | **백업은 되는데 복원이 안 됨** (평소엔 티가 안 나 가장 위험) |
| `s3:DeleteObject` | 〃 | 보관 수(`retention-full=2`)를 넘은 옛 백업 자동 정리 | 계속 쌓여 비용 증가 |
| `s3:PutObjectTagging` · `GetObjectVersion` | 〃 | 태그 · 버전 기능을 쓸 때만 (공식 샘플에 있어 함께) | 지금 설정에선 영향 없음 |
| `s3:PutObject` | 원본 버킷 전체 | 수집기: 원본을 한 번에 올림(`put_object`) — 읽기 · 지우기는 일부러 없음 | 원본 보관 안 됨 |

- 버킷 만들기 · 지우기, 버킷 정책 변경, IAM 은 주지 않는다. `AmazonS3FullAccess` 같은 관리형 정책은 쓰지 않는다 (계정의 모든 버킷을 지우거나 공개로 바꿀 수 있음)

## 2. 설치 명령어

### 2-1. AWS 콘솔 (한 번 — 루트 또는 관리자)

1. **비용 알림 먼저**: 결제 및 비용 관리 → 예산 → 예산 생성 → 템플릿 "월별 비용 예산" → `5` USD · 이메일 → **메일함의 인증 링크를 누른다** (2026-09-30 이후 추가한 수신자는 인증해야 알림이 옴)
2. **버킷 2개** (서울 `ap-northeast-2`, 범용): `<백업 버킷>` · `<원본 버킷>` — 퍼블릭 액세스 차단 켬 · 기본 암호화 SSE-S3 · **버전 관리 끔**(켜 두면 pgBackRest 가 지운 옛 WAL 이 숨은 버전으로 남아 비용이 쌓임) · 객체 잠금 끔
   - ⚠ **백업 버킷에는 수명 주기 규칙을 넣지 않는다** — pgBackRest 모르게 WAL 이 지워지면 복원이 깨진다. 원본 버킷은 넣어도 됨(예: 30일 만료)
3. IAM → 정책 → 정책 생성 → JSON 에 `aws-s3-policy.example.json` (`<…>` 는 버킷 화면의 "ARN 복사"로) → 이름 `chargeops-lab-s3-policy`
4. IAM → 사용자 생성 `chargeops-lab-s3` — **콘솔 액세스 체크하지 않음** → 정책 직접 연결
5. 그 사용자 → 보안 자격 증명 → 액세스 키 만들기 → 사용 사례 "AWS 외부에서 실행되는 애플리케이션" → `.csv` 다운로드 (**저장소 폴더 밖**에 보관. 비밀 키는 이 화면에서 한 번만 보임)

루트로 들어갔다면 끝나고 로그아웃 — 루트 액세스 키는 만들지 않는다.

### 2-2. 키를 관리 서버에 (mgmt)

```bash
# ===== 실행할 명령어 =====
# 화면 · 셸 기록에 안 남게 입력 → ~/.chargeops.env(600) 한 곳에만. MinIO 값(AWS_ACCESS_KEY_ID 등)과 섞이지 않게 다른 이름
read -rsp 'Access key ID: ' K; echo; read -rsp 'Secret access key: ' S; echo
printf "export AWS_TEST_KEY_ID='%s'\nexport AWS_TEST_SECRET='%s'\nexport AWS_TEST_BUCKET_BACKUP=<백업 버킷>\nexport AWS_TEST_BUCKET_RAW=<원본 버킷>\n" "$K" "$S" >> ~/.chargeops.env && unset K S && chmod 600 ~/.chargeops.env
grep -c '^export AWS_TEST_' ~/.chargeops.env      # 값은 안 보이고 줄 수만 → 4
```

### 2-3. 권한 시험 — 되는 것 5 · 막혀야 하는 것 2

```bash
# ===== 실행할 명령어 =====
# ( ) 안에서만 AWS 키를 쓰고, MinIO 주소 · 사설 CA 변수는 지운다 (남아 있으면 MinIO 로 가거나 AWS 인증서를 사설 CA 로 검사하다 실패)
( source ~/.chargeops.env; unset S3_ENDPOINT_URL AWS_CA_BUNDLE; export AWS_ACCESS_KEY_ID="$AWS_TEST_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_TEST_SECRET"; python3 - <<'EOF'
import os, boto3, botocore
s3 = boto3.client("s3", region_name="ap-northeast-2")
B, R = os.environ["AWS_TEST_BUCKET_BACKUP"], os.environ["AWS_TEST_BUCKET_RAW"]
def t(name, fn, expect_ok=True):
    try:
        fn(); ok = True
    except botocore.exceptions.ClientError as e:
        ok = False; code = e.response["Error"]["Code"]
    print("✅" if ok == expect_ok else "❌", name, "→", "허용" if ok else f"거부({code})")
t("백업 칸 쓰기", lambda: s3.put_object(Bucket=B, Key="test-chargeops/_conn.txt", Body=b"ok"))
t("백업 칸 읽기", lambda: s3.get_object(Bucket=B, Key="test-chargeops/_conn.txt"))
t("백업 칸 목록", lambda: s3.list_objects_v2(Bucket=B, Prefix="test-chargeops/"))
t("백업 칸 지우기", lambda: s3.delete_object(Bucket=B, Key="test-chargeops/_conn.txt"))
t("원본 버킷 쓰기", lambda: s3.put_object(Bucket=R, Key="_conn.txt", Body=b"ok"))
t("백업 칸 밖 쓰기 (거부돼야 정상)", lambda: s3.put_object(Bucket=B, Key="other/_conn.txt", Body=b"x"), expect_ok=False)
t("원본 버킷 목록 (거부돼야 정상)", lambda: s3.list_objects_v2(Bucket=R), expect_ok=False)
EOF
)
```

### 2-4. 수집기 원본 → AWS (K8s) — **Secret 먼저, ConfigMap 나중**

```bash
# ===== 실행할 명령어 =====
# ① Secret: 키를 표준입력으로 (명령 줄 · 프로세스 목록에 안 보임). DB 비밀번호 · 인증키 항목은 그대로
source ~/.chargeops.env && printf '{"stringData":{"AWS_ACCESS_KEY_ID":"%s","AWS_SECRET_ACCESS_KEY":"%s"}}' "$AWS_TEST_KEY_ID" "$AWS_TEST_SECRET" | kubectl -n ops patch secret collector-secret --type merge --patch-file /dev/stdin
kubectl -n ops get secret collector-secret -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d | cut -c1-4; echo   # AKIA 면 AWS 키
# ② ConfigMap: MinIO 주소 · 사설 CA 는 키째 지우고(null) 리전을 넣는다 (apply 가 아니라 patch — apply 하면 suspend 로 돌아감)
kubectl -n ops patch configmap collector-config --type merge -p '{"data":{"S3_BUCKET":"<원본 버킷>","AWS_REGION":"ap-northeast-2","S3_ENDPOINT_URL":null,"AWS_CA_BUNDLE":null}}'
# ③ 다음 회차(5분 안) 로그 — 마지막 줄이 원본=s3://<원본 버킷>/… 면 성공
kubectl -n ops logs $(kubectl -n ops get pods -l mode=status --sort-by=.metadata.creationTimestamp -o name | tail -1)
```

되돌리기: ConfigMap 을 `{"S3_BUCKET":"chargeops-raw","S3_ENDPOINT_URL":"https://<MinIO 주소>","AWS_CA_BUNDLE":"/etc/chargeops-ca/ca.crt","AWS_REGION":null}` 로, Secret 을 `~/.chargeops.env` 의 MinIO 키(`$AWS_ACCESS_KEY_ID` · `$AWS_SECRET_ACCESS_KEY`)로 같은 방식 patch.

### 2-5. pgBackRest repo2 = AWS S3

기존 `/etc/pgbackrest/pgbackrest.conf`(repo1)는 건드리지 않고 **`conf.d/` 에 파일 하나만 더한다** — pgBackRest 는 `/etc/pgbackrest/conf.d/*.conf` 를 자동으로 합쳐 읽는다(같은 `[global]` 절은 합쳐짐). 떼려면 그 파일만 지우면 원래대로.

```bash
# ===== 실행할 명령어 =====
# ① 두 DB 서버에 repo2 설정 (키는 표준입력으로, 파일은 root:postgres 640 — 기존 설정 파일과 같은 권한)
source ~/.chargeops.env && for h in db-01 db-02; do printf '[global]\nrepo2-type=s3\nrepo2-s3-endpoint=s3.ap-northeast-2.amazonaws.com\nrepo2-s3-region=ap-northeast-2\nrepo2-s3-bucket=%s\nrepo2-s3-key=%s\nrepo2-s3-key-secret=%s\nrepo2-path=/test-chargeops\nrepo2-retention-full=2\n' "$AWS_TEST_BUCKET_BACKUP" "$AWS_TEST_KEY_ID" "$AWS_TEST_SECRET" | ssh $h 'sudo install -d -o root -g postgres -m 750 /etc/pgbackrest/conf.d && sudo sh -c "umask 027; cat > /etc/pgbackrest/conf.d/repo2-aws.conf" && sudo chgrp postgres /etc/pgbackrest/conf.d/repo2-aws.conf && echo "$(hostname -s) 놓음"'; done
ssh db-01 'sudo -u postgres psql -Atc "select pg_is_in_recovery()"'                 # f 인 서버가 Primary — 아래 ②~⑤ 는 Primary 에서
# ② 바로 이어서 저장소 자리 (repo1 은 "이미 있고 정상", repo2 만 새로) — ①과 ② 사이에는 WAL 보관이 잠깐 실패한다(5장)
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops stanza-create'
# ③ WAL 하나를 강제로 넘겨 repo1 · repo2 둘 다 도착하는지
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops check'
# ④ AWS 에 첫 전체 백업 (백업은 저장소마다 따로 — --repo=2)
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops --repo=2 --type=full backup'
# ⑤ AWS 의 백업 · WAL 을 실제로 내려받아 체크섬 대조 (GetObject = 복원 재료 확인, 운영 영향 없음)
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops --repo=2 verify'
```

이 뒤로 WAL 은 `archive_command` 를 바꾸지 않아도 **자동으로 두 저장소에** 올라간다.

### 2-6. 정기 백업 타이머 (두 DB 서버)

| 파일 | 내용 |
|---|---|
| `db/ops/pgbackrest/chargeops-backup.sh` | **Primary 일 때만** 백업(Standby 면 그냥 끝 → 장애 전환 뒤에도 설정 그대로). 일요일 전체 · 다른 날 차등. 설정된 저장소(`repoN-type`)마다 차례로 — repo2 가 없는 환경은 repo1 만. 한 저장소가 실패해도 나머지는 받고 종료 코드 1 |
| `chargeops-backup.service` | 한 번 실행(oneshot), 낮은 우선순위(Nice 10 · IO idle) |
| `chargeops-backup.timer` | 매일 03:40 (공공 기본정보 수집 03:10 뒤). `Persistent=true` — 그 시각에 꺼져 있었으면 다음에 켰을 때 한 번 따라잡음 |

```bash
# ===== 실행할 명령어 =====
# 관리 서버(mgmt), 이 저장소 루트에서
for h in db-01 db-02; do scp -q db/ops/pgbackrest/chargeops-backup.{sh,service,timer} $h:/tmp/ && ssh -n $h 'sudo install -m 755 /tmp/chargeops-backup.sh /usr/local/sbin/ && sudo install -m 644 /tmp/chargeops-backup.service /tmp/chargeops-backup.timer /etc/systemd/system/ && rm -f /tmp/chargeops-backup.* && sudo systemctl daemon-reload && sudo systemctl enable --now chargeops-backup.timer && echo "$(hostname -s) 타이머 켬"'; done
ssh -n db-01 'systemctl list-timers chargeops-backup.timer --no-pager'          # NEXT = 다음 03:40
# 지금 한 번 손으로 (Primary 는 백업, Standby 는 건너뜀)
ssh -n db-01 'sudo systemctl start chargeops-backup.service; sudo journalctl -u chargeops-backup.service -n 30 --no-pager -o cat | grep -E "== |backup size|command end|ERROR"'
ssh -n db-02 'sudo systemctl start chargeops-backup.service; sudo journalctl -u chargeops-backup.service -n 3 --no-pager -o cat'
```

## 3. 설정 파일

| 위치 | 내용 | 저장소에 |
|---|---|---|
| DB 서버 `/etc/pgbackrest/conf.d/repo2-aws.conf` | repo2 = AWS S3 (엔드포인트 · 리전 · 버킷 · 경로 `/test-chargeops` · `retention-full=2` · 키 2줄) | ✗ (키가 있음 — 2-5 ① 의 명령이 만든다) |
| DB 서버 `/usr/local/sbin/chargeops-backup.sh` · `/etc/systemd/system/chargeops-backup.{service,timer}` | 정기 백업 | ✓ `db/ops/pgbackrest/` |
| mgmt `~/.chargeops.env` | `AWS_TEST_KEY_ID` · `AWS_TEST_SECRET` · `AWS_TEST_BUCKET_BACKUP` · `AWS_TEST_BUCKET_RAW` = `<각자 설정>` | ✗ |
| K8s Secret `collector-secret` | `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` (AWS 키로 교체) | ✗ |
| K8s ConfigMap `collector-config` | `S3_BUCKET=<원본 버킷>` · `AWS_REGION=ap-northeast-2` (`S3_ENDPOINT_URL` · `AWS_CA_BUNDLE` 없음) | 양식 `apps/collector/collector-cronjob.example.yaml` 주석 |
| IAM 정책 | 1장 표 | ✓ `db/ops/pgbackrest/aws-s3-policy.example.json` |
| 복원 훈련 | `PITR_REPO=2` 를 주면 AWS 저장소에서 복원 (안 주면 pgBackRest 기본 = repo1 부터 찾음) | ✓ `db/ops/pgbackrest/pitr-drill.sh` |

## 4. 확인 방법

```bash
# ===== 실행할 명령어 =====
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops info'                    # status: ok, repo1 · repo2 백업 목록
psql -h db-01 -p 5432 -U dba chargeops -c "select archived_count, failed_count, last_archived_wal, last_failed_time from pg_stat_archiver"
#   몇 분 뒤 다시 쳐서 failed_count 는 그대로 · archived_count 만 늘면 두 저장소 모두 정상
#   (source ~/.chargeops.env 뒤에는 기본 포트가 6432(PgBouncer)라 dba 는 -p 5432 를 꼭 붙인다)
```
```
# ===== 실행 결과 (2026-10-14, 개인 계정 시험) =====
권한 시험 7가지       ✅ 7/7 — 허용 5 · 막혀야 하는 2개는 거부(AccessDenied)
수집기 11:00 회차     [status] 334건 중 334건 변경, API 1회 호출, 원본=s3://<원본 버킷>/getChargerStatus/2026/10/14/020001_p1.json
stanza-create        stanza 'chargeops' already exists on repo1 and is valid / stanza-create for stanza 'chargeops' on repo2
check                WAL segment 0000000900000014…6F successfully archived to … on repo1 / … on repo2
repo2 첫 전체 백업     new backup label = 20261014-110915F, full backup size = 497.4MB → repo2 backup set size 88.6MB, 33초
verify --repo=2      verify command end: completed successfully (25초)
pg_stat_archiver     archived_count 1401 → 1402, failed_count 8 그대로 (마지막 실패 11:08:47 = 2-5 ①과 ② 사이)
타이머               NEXT Thu 2026-10-15 03:40:00 KST
손으로 1회 (수요일)    repo1 diff backup size = 421.7MB (4.7초) · repo2 diff backup size = 361.1MB (6.5초) · expire 정상
db-02                db-02 는 Standby — 백업은 Primary 에서 받는다

# AWS 저장소 복원 훈련 (13:10, PITR_REPO=2 bash db/ops/pgbackrest/pitr-drill.sh → db-02 의 임시 폴더 · 5433)
복원                 repo2: restore backup set 20261014-110915F_20261014-122146D, recovery will start at 12:21:46
                     restore size = 498.5MB, file total = 1522 — 34.5초 (AWS 에서 내려받기)
WAL 받는 곳          restore_command = 'pgbackrest … --repo=2 --stanza=chargeops archive-get %f "%p"'  → WAL 도 AWS 에서
검증                 이번 PITR-BEFORE(13:10:18) 있음 · 이번 PITR-AFTER 없음 (그 아래 줄은 T 보다 앞선 지난 훈련 기록)
RTO                  54초 (복원 시작 → T 까지 재생 · 승격) — 내려받기 34.5초 + 12:21 차등 이후 약 49분치 WAL 재생
```

| 복원 훈련 | 저장소 | 복원(내려받기) | RTO |
|---|---|---|---|
| 2026-10-12 (2.8, [`pgbackrest-pitr.md`](pgbackrest-pitr.md)) | repo1 MinIO (같은 PC) | 10.5초 | 15초 |
| 2026-10-14 | **repo2 AWS S3 (서울)** | 34.5초 | **54초** |

- 차이는 거의 인터넷으로 내려받는 시간. 마지막 백업 이후 WAL 이 길수록 재생이 길어진다 → 매일 차등 백업(2-6)이 RTO 를 묶어 둔다
- 실제 DR(4.5)의 RTO 는 여기에 **새 서버 준비 시간**(EC2 생성 · PostgreSQL 설치)이 더해진다 — 목표 30분과 비교할 때 이 둘을 합산

AWS 콘솔에서 보이는 곳:

| 어디 | 보이는 것 |
|---|---|
| S3 → `<원본 버킷>` → `getChargerStatus/<년>/<월>/<일>/` | 5분마다 새 파일. 이름의 시각은 **UTC** (`030001` = 한국 12:00). "마지막 수정" 열은 한국 시간 |
| S3 → `<백업 버킷>` → `test-chargeops/archive/chargeops/17-1/…` | WAL 이 거의 1분마다. 파일 이름이 `pg_stat_archiver.last_archived_wal` 과 같아 DB ↔ AWS 대조 가능 |
| 같은 버킷 → `test-chargeops/backup/chargeops/` | `…F`(전체) · `…D`(차등) 폴더 |
| 버킷 → 지표 탭 · 결제 → Cost Explorer | 크기 · 객체 수 · 비용 (하루 늦게 반영) |

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 수집기 1회 `InvalidAccessKeyId` (10:55) | ConfigMap(AWS 버킷)은 바뀌었는데 Secret 은 아직 MinIO 키였던 순간에 그 회차가 시작됨 → **Secret 먼저, ConfigMap 나중**. 다음 회차가 조회 범위를 넓혀 빈 구간을 메움(평소 230건 → 334건) |
| (예방) `S3_ENDPOINT_URL` 을 빈 값으로 두면 안 됨 | boto3 가 빈 문자열을 주소로 받아 오류 → `null` 로 **키를 지운다**. `AWS_CA_BUNDLE` 이 남으면 AWS 의 공인 인증서를 사설 CA 로 검사하다 SSL 오류 |
| `pg_stat_archiver.failed_count` 가 8 늘어남 | repo2 설정 파일을 놓은 뒤 `stanza-create` 전 몇 초 동안 `archive-push` 가 repo2 에서 실패 → 설정을 놓으면 **바로** stanza-create. 그 뒤로는 증가 없음 |
| `psql: … port 6432 failed: FATAL: login rejected` | `source ~/.chargeops.env` 뒤 기본 포트가 6432(PgBouncer, 앱 전용)라 dba 가 거부됨 → `-p 5432` |
| AWS 백업으로 복원 훈련이 안 됨 (예방) | 저장소를 지정하지 않은 restore 는 repo1 부터 찾는다(공식 문서) → `PITR_REPO=2 bash db/ops/pgbackrest/pitr-drill.sh` |
| ⚠ AWS 에 못 올리면 MinIO 보관까지 멈춤 | 저장소가 여럿이면 `archive-push` 는 전부에 올려야 성공 — 비동기(`archive-async`)가 아니면 한 곳이 막힐 때 다른 곳도 한 칸 이상 못 나간다(공식 문서). WAL 이 DB 디스크(pg_wal)에 쌓임 → Grafana "WAL 보관" 감시 필수. 이 감시의 지표(postgres_exporter)가 실습 PC 에 없어 2026-10-14 에 설치함(팀 구축 가이드 STEP 19 ②) |
| 차등 백업이 생각보다 큼 (361MB, 전체 1시간 뒤) | pgBackRest 기본은 **파일 단위** — 수집으로 매 5분 바뀌는 큰 테이블 파일은 통째로 다시 올라간다. 압축 후 크기는 작지만, 줄이려면 블록 단위(`repo2-block=y` + `repo2-bundle=y`)를 검토 (6장) |
| 결제 화면에서 리전이 모두 자물쇠 | 결제는 리전 없는 서비스라 잠김(정상). 버킷 만들 때는 S3 화면에서 **서울**인지 확인 |

## 6. 팀에서 정할 것 · 다음

- **팀 계정으로 옮기기 (2.2 IAM 담당과)**: 팀 계정의 기존 DB 백업 버킷을 쓰고, `aws-s3-policy.example.json` 그대로 정책 · 프로그램용 사용자 · 키를 요청. 실습 VM 은 `test-chargeops`, 정식 환경은 다른 경로(예: `chargeops`) — 백업 저장소는 DB 클러스터마다 고유 번호에 묶여 새로 설치한 DB 가 같은 경로를 못 이어 쓴다
- ~~AWS 저장소 복원 훈련 · RTO~~ → **완료 (2026-10-14)**: RTO 54초 (4장). 다음은 EC2 DR(4.5, Terraform) — 새 서버에 PostgreSQL 17 + pgBackRest 를 놓고 같은 repo2 에서 복원
- RTO 를 더 줄이려면: WAL 미리 받기(`archive-async=y` + `archive-get-queue-max`) · 복원 병렬(`--process-max`) 검토
- `archive-async` 도입 여부 (AWS 장애 때 MinIO 보관까지 멈추지 않게) · 블록 단위 차등 여부
- 개인 계정 시험 정리: 팀 계정으로 옮긴 뒤 개인 버킷을 비우고 삭제 · `chargeops-lab-s3` 키 비활성화 → 삭제
