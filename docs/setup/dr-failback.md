# DR 복귀 런북 — 클라우드에서 살린 DB 를 온프렘으로 되돌리기 (failback)

- 담당: 강영수
- 관련 WBS: 4.5 DR 훈련 (DB 부분)
- 버전: versions.md 참고 (PostgreSQL 17 · pgBackRest 2.59 · Patroni)
- 상태: **실습 왕복 훈련 완료 (2026-10-23)** — 온프렘 → 클라우드 복원 → 클라우드에서 다시 백업 → 온프렘 Patroni 클러스터로 복귀, 잃은 데이터 0건
- 앞 단계: [`dr-restore.md`](dr-restore.md) (빈 서버에서 S3 백업으로 살리기 — `dr-restore.sh`)

## 1. 왜 / 무엇을

- `dr-restore.sh` 는 온프렘이 사라졌을 때 **클라우드에서 DB 를 살리는** 데까지다. 온프렘이 돌아오면 클라우드에서 쌓인 데이터를 가지고 **다시 온프렘으로** 와야 한다
- 온프렘과 클라우드가 만나는 곳은 **S3 저장소(repo2) 하나**. 서로 직접 복제 연결을 하지 않는다 → 방화벽 · VPN 없이 같은 방법(pgBackRest 복원)으로 양쪽을 오간다

```text
 온프렘 Primary ──WAL·백업──▶ S3 repo2 ──복원──▶ 클라우드 DB (새 Primary, 타임라인 +1)
   (사라짐)                     ▲   │
                                │   └──복원(최신 타임라인)──▶ 온프렘 db-01 (다시 Primary, 타임라인 +1)
       클라우드 DB ──WAL·전체 백업─┘                              └─ db-02 는 pg_rewind 로 Replica 복귀
```

| 단계 | 어디서 | 핵심 |
|---|---|---|
| ① 클라우드 DB 가 새 타임라인으로 백업 | 클라우드 | `dr-restore.sh` 의 `DR_MODE=real` ⑧ 이 자동으로 함 (`--start-fast` 전체 백업) |
| ② 쓰기 멈추고 마지막 WAL 까지 올리기 | 클라우드 | 앱 끊기 → `pg_switch_wal()` → `check` → 정지. **두 Primary 가 동시에 쓰지 않게** |
| ③ 온프렘 db-01 을 S3 에서 다시 만들기 | 온프렘 | Patroni 의 bootstrap 방식을 `pgbackrest` 로 바꿔서 → 클러스터 기록 지우고 → 시작 |
| ④ db-02 복귀 | 온프렘 | Patroni 가 `pg_rewind` 로 자동 (`use_pg_rewind: true`) |
| ⑤ 확인 · 정리 | 양쪽 | 표시 데이터 비교(RPO) → 전체 백업 → 앱 복귀 → 클라우드 자원 끄기 |

## 2. 실행 명령어

> 모두 **승인 뒤에만**. ③ 의 `patronictl remove` 와 데이터 폴더 비우기는 되돌릴 수 없는 작업이다.

### 2-1. 클라우드 DB — 쓰기 멈추고 마지막 WAL 까지 올리기

```bash
# ===== 실행할 명령어 =====
# 클라우드 DB 서버(EC2 · 훈련에서는 빈 VM)에서. 앱 연결을 먼저 끊은 뒤
# ① 마지막 적재 시각 · 건수를 적어 둔다 (복귀 뒤 같은지 비교 = RPO)
sudo -u postgres psql -XAt -d chargeops -c "select count(*), to_char(max(received_at),'MM-DD HH24:MI:SS') from status_history"
# ② 지금 WAL 파일을 닫아 S3 로 올리고, 올라갔는지 확인
sudo -u postgres psql -XAtc "select pg_switch_wal()" && sudo -u postgres pgbackrest --stanza=chargeops check
# ③ 정지 — 이 뒤로 클라우드 DB 에 쓰면 그 데이터는 온프렘으로 오지 않는다
sudo systemctl stop postgresql-17 && sudo systemctl disable -q postgresql-17
```

### 2-2. 온프렘 Patroni 클러스터 — db-01 을 S3 에서 다시 만들기

```bash
# ===== 실행할 명령어 =====
# 관리 서버(mgmt)에서. Replica 먼저 멈춘다
ssh db-02 "sudo systemctl stop patroni" && ssh db-01 "sudo systemctl stop patroni"
# db-01 의 옛 데이터 폴더를 옆으로 치운다 (지우지 않음 — 확인 끝나면 사람이 판단해 지움)
ssh db-01 "sudo mv /var/lib/pgsql/17/data /var/lib/pgsql/17/data.before-failback && sudo install -d -o postgres -g postgres -m 700 /var/lib/pgsql/17/data"
# db-01 의 /etc/patroni/patroni.yml 에 3장 bootstrap 블록을 넣는다 (600 postgres 파일 — sudo 편집)
ssh -t db-01 "sudo -u postgres vi /etc/patroni/patroni.yml"
# 클러스터 기록(etcd)을 지워야 Patroni 가 '처음 만들기(bootstrap)' 를 한다 — 클러스터 이름 chargeops-db 를 두 번 입력
ssh -t db-01 "sudo -u postgres patronictl -c /etc/patroni/patroni.yml remove chargeops-db"
# db-01 시작 → pgBackRest 로 repo2 에서 복원 → 최신 타임라인 끝까지 재생 → 승격 → Leader
ssh db-01 "sudo systemctl start patroni"
ssh db-01 "sudo -u postgres patronictl -c /etc/patroni/patroni.yml list"      # db-01 Leader · running 이 될 때까지 몇 번
# db-02 시작 → 타임라인이 갈라졌으므로 Patroni 가 pg_rewind 로 맞추고 Replica 로 붙는다
ssh db-02 "sudo systemctl start patroni"
```

### 2-3. 확인 · 정리

```bash
# ===== 실행할 명령어 =====
# ① 2-1 ① 과 같은 값이면 RPO 0
psql -h db-01 -U dba -d chargeops -XAtc "select count(*), to_char(max(received_at),'MM-DD HH24:MI:SS') from status_history"
# ② 클러스터 · 타임라인 · 복제
ssh db-01 "sudo -u postgres patronictl -c /etc/patroni/patroni.yml list"
# ③ 온프렘 Primary 의 WAL 이 두 저장소(repo1 MinIO · repo2 S3)로 다시 올라가는지 + 새 타임라인의 첫 전체 백업
ssh db-01 "sudo -u postgres pgbackrest --stanza=chargeops check && sudo -u postgres pgbackrest --stanza=chargeops --type=full --start-fast backup"
```

그 뒤: 앱 접속을 온프렘으로 되돌림 → 클라우드 DB 서버 정리(EC2 는 `aws.sh off`) → `patroni.yml` 의 bootstrap 블록을 원래대로(Ansible `patroni.yml` 다시 적용) → 확인이 끝나면 `data.before-failback` 지우기(사람이 판단)

## 3. 설정 파일

`/etc/patroni/patroni.yml` (db-01) 의 `bootstrap:` 아래에 넣는다. Patroni 는 클러스터를 **처음 만들 때만** 이 방식을 쓰므로, 평소 운영 · 재시작 · 장애 전환에는 영향이 없다.

```yaml
bootstrap:
  method: pgbackrest
  pgbackrest:
    command: pgbackrest --stanza=chargeops --repo=2 --pg1-path=/var/lib/pgsql/17/data restore
    keep_existing_recovery_conf: false
    no_params: true
    recovery_conf:
      recovery_target_timeline: latest
      recovery_target_action: promote
      restore_command: pgbackrest --stanza=chargeops --repo=2 archive-get %f "%p"
```

| 값 | 왜 |
|---|---|
| `--repo=2` | 클라우드 DB 가 백업 · WAL 을 올린 곳은 S3(repo2) 뿐. MinIO(repo1)에는 클라우드 시기의 기록이 없다 |
| `recovery_target_timeline: latest` | 클라우드에서 승격하며 생긴 **새 타임라인**을 따라가야 클라우드에서 쓴 데이터까지 온다 |
| `recovery_target_action: promote` | 끝까지 재생한 뒤 스스로 Primary 가 됨 → Patroni 가 Leader 로 등록 |
| `no_params: true` | Patroni 가 `--scope` 같은 인자를 붙이지 않게 (pgBackRest 가 모르는 인자) |

## 4. 확인 방법

```
# ===== 실행 결과 (2026-10-23, 실습 — mgmt · db-01 · db-02 Patroni, 클라우드 역할은 빈 VM + 팀 계정 S3 시험 버킷) =====
온프렘 → 클라우드     빈 서버에서 S3 만으로 복원 · 승격 75초 (두 번째 111초)
클라우드 재백업       새 타임라인 전체 백업 145초 — 그중 95초는 체크포인트 대기 (→ dr-restore.sh 에 --start-fast 반영)
클라우드 → 온프렘     db-01 복원 · 승격 · Leader 약 2분, db-02 pg_rewind · Replica 복귀 13초
RPO                  복귀 전 클라우드에 넣은 표시 행(108번)까지 온프렘에 그대로 → 잃은 데이터 0건
저장소               한 저장소(repo2)에 타임라인이 끊기지 않고 이어짐 (온프렘 → 클라우드 → 온프렘)
```

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 클라우드 복원본이 시작 직후 `FATAL: could not load server certificate file` | 실습 DB 는 인증서를 데이터 폴더 밖(`/var/lib/pgsql/ssl`)에 둠 → 설정만 백업으로 오고 파일은 없음. 그때는 임시 인증서를 손으로 만들어 해결 → **`dr-restore.sh` ⑤ 가 자동으로 확인 · 임시 발급** (팀 서버는 데이터 폴더 안이라 해당 없음) |
| 클라우드 첫 전체 백업이 95초 동안 진행 없음 | 백업 시작 때 다음 체크포인트까지 기다림 → `--start-fast` (이 런북 · `dr-restore.sh` ⑧) |
| Patroni 를 그냥 켜면 옛 데이터로 뜸 | etcd 에 클러스터 기록이 남아 있으면 bootstrap 을 하지 않는다 → 데이터 폴더를 비우고 `patronictl remove` 뒤 시작 |

## 6. 팀에서 정할 것 · 다음

- **멘토 서버(Patroni 없이 수동 전환)** 에서의 복귀 — 미검증. 같은 원리로: db-01 데이터 폴더를 비우고 `pgbackrest --stanza=chargeops --repo=2 --target-timeline=latest restore` → 시작(끝까지 재생 후 승격) → db-02 는 `db/ops/failover/rejoin.sh` 로 다시 붙임. 4.5 때 시험
- 앱 접속 전환(온프렘 ↔ 클라우드)을 어떻게 할지 — DNS 이름 · PgBouncer 대상 바꾸기 (4.5)
- 2-1 ③ 뒤에 클라우드 DB 를 실수로 다시 켜지 않게: EC2 는 바로 중지 · 종료
