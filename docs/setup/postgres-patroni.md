# PostgreSQL 자동 장애 전환 (Patroni + etcd + watchdog)

- 담당: 강영수 (리뷰 조인성)
- 관련 WBS: 4.4 DB 장애 전환 — 수동 전환(`failover.sh`)을 **자동 전환**으로 확장 (2026-10-16 시험 → 도입)
- 버전: versions.md 참고 (Patroni 4.1 · etcd 3.5.17 추가)
- 파일: `infra/ansible/patroni.yml` · `infra/ansible/templates/patroni.yml.j2` (실습 묶음에서는 `ansible/`) · `db/ops/patroni/` (`convert.sh` · `status.sh` · `drill.sh` · `switchover.sh` · `rollback.sh` · `patroni-on-role-change.sh`)
- 앞 단계: [`postgres-replication.md`](postgres-replication.md)(복제 · PgBouncer 두 곳), [`postgres-failover.md`](postgres-failover.md)(수동 전환 — 이 문서의 비교 기준)

## 1. 왜 / 무엇을

수동 전환은 **명령을 친 뒤** 16.6초면 쓰기가 돌아오지만, 그 전에 사람이 알아채고(4~5분) 판단하는(0~5분) 시간이 든다. 무중단이 목표라서, 사람 없이 전환되게 한다.
다만 자동 전환 도구는 잘못 쓰면 **Primary 가 두 대**가 된다. 그래서 도입 전에 시험 클러스터(5433, 운영과 별개)에서 위험부터 실측했다.

| 시험 (2026-10-16, 영수 PC) | 일부러 만든 장애 | 결과 | Primary 두 대 | 쓰기 멈춤 |
|---|---|---|---|---|
| ① watchdog 없음 | Patroni 만 죽음 (PostgreSQL 은 살아 있음) | 옛 Primary 가 계속 쓰기를 받음 | **발생 — 279건 손실** | — |
| ② 서버 장애 | Patroni + PostgreSQL 죽음 | 자동 승격 (사람 0회) | 없음 | 31.5초 |
| ③ watchdog 있음 | Patroni 만 죽음 | 27.7초 뒤 그 서버 강제 재부팅 → 그다음 승격 | 없음 | 5.6초 |
| ④ 펜싱 | Leader 가 etcd 에서 고립 | 17.6초 뒤 스스로 읽기 전용으로 강등 → 그다음 승격 | 없음 | 11.8초 |

**결정**: Patroni 를 쓰되 **watchdog 은 `required`**(못 켜면 Leader 가 되는 것 자체를 거부). ①이 그 이유다.

## 2. 구성 — 무엇이 바뀌나

```
          앱(CSMS · 수집기)  host=db-01,db-02 port=6432 target_session_attrs=read-write   ← 그대로
                 │
     ┌───────────┴───────────┐
  PgBouncer(db-01)        PgBouncer(db-02)        ← 그대로. 역할이 바뀌면 Patroni 콜백이 restart
     │                       │
  PostgreSQL 17 ◀─복제─▶ PostgreSQL 17           ← 데이터 · 설정(conf.d) · pg_hba 그대로
     ▲                       ▲
  Patroni(db-01)          Patroni(db-02)          ← 새로: PostgreSQL 을 띄우고 감시 · 승격 · 되감기
  + watchdog              + watchdog              ← 새로: Patroni 가 멈추면 25초 뒤 재부팅
     └──────── etcd 3대 (db-01 · db-02 · mgmt) ─────┘  ← 새로: "지금 Leader 가 누구인가" 를 과반으로 정함
```

| 항목 | 지금 (수동) | Patroni 뒤 |
|---|---|---|
| PostgreSQL 실행 | `postgresql-17` 서비스 | `patroni` 서비스가 띄움 (`postgresql-17` 은 masked) |
| 장애 전환 | 사람이 `failover.sh` | **자동** (ttl 30초) |
| 옛 Primary 재편입 | 격리 부팅 + `rejoin.sh` | **자동** — 다시 켜지면 Patroni 가 pg_rewind 로 Replica 로 붙임 |
| 계획 교대 | `switchover.sh` + `rejoin.sh` | `TO=db-01 bash db/ops/patroni/switchover.sh` |
| 역할 바뀐 PgBouncer | 스크립트가 restart | Patroni 콜백(`on_role_change`)이 restart |
| 복제 슬롯 | `db02` (수동) | `db_02` · `db_01` (Patroni 관리) |
| `postgresql.conf` | 직접 | Patroni 가 쓰고, 원래 파일은 `postgresql.base.conf` 로 그대로 읽음 (`conf.d/chargeops.conf` 유지) |
| 서버별 설정 `conf.d/zz-local.conf` | 데이터 폴더 안 | `/var/lib/pgsql/17/zz-local.conf` 로 옮김 — Patroni 의 자동 pg_rewind 가 conf.d 를 상대 서버 것으로 덮기 때문 (convert.sh 가 옮기고 `include_if_exists` 추가) |
| 백업 · WAL 보관 · PITR · 대시보드 | — | 그대로 (백업은 이미 'Primary 일 때만') |
| `failover.sh` · `switchover.sh` · `rejoin.sh` | 사용 | Patroni 가 돌면 **스스로 중단** (둘이 동시에 손대지 않게) |

포트 (group_vars `fw_rules`, after-up 이 매번 적용): db 서버 2379 · 2380 · 8008 ← 데이터망 · 관리망, mgmt 2379 · 2380 ← 데이터망.

ttl · 시간 값 (`group_vars/all/main.yml` 의 `ha:`): ttl 30 · loop_wait 10 · retry_timeout 10 · watchdog 25초(ttl − 5) · 1MB 넘게 뒤처진 Standby 는 자동 승격 안 함.

## 3. 준비

전제: 복제 · PgBouncer 두 곳 정상, mgmt 에서 DB 서버로 SSH(sudo), mgmt 의 `~/chargeops` 가 최신 (`rsync -a --chmod=go-w --exclude .vagrant --exclude keys --exclude backup /vagrant/ ~/chargeops/`).

mgmt 의 `~/.pgpass`(600) 에 이 줄들이 있어야 한다. 값은 각자 — 이 문서 · 저장소에는 없다. 확인은 값을 내지 않고 줄 수만 센다.

| 줄 (호스트:포트:DB:계정) | 쓰는 곳 |
|---|---|
| `db-0X:5432:*:postgres` | Patroni 설정(관리자 · pg_rewind) — `patroni.yml` 이 읽어 DB 서버의 `/etc/patroni/patroni.yml`(600)에만 씀 |
| `db-0X:5432:replication:replicator` | Patroni 설정(복제) |
| `db-01:6432:chargeops:app_writer` · `db-02:…` | 전환 · 시험 스크립트의 쓰기 확인 |
| `db-01:5432:*:dba` · `db-02:…` | `drill.sh` 의 'Primary 두 대' 감시 |

```bash
# ===== 실행할 명령어 ===== (mgmt — 줄 수만 확인. 6 이상이면 됨)
grep -cE '^db-0[12]:(5432:\*:(postgres|dba)|5432:replication:replicator|6432:chargeops:app_writer):' ~/.pgpass
```

## 4. 설치 (A) — 운영 DB 는 아직 그대로

```bash
# ===== 실행할 명령어 ===== (mgmt — 방화벽 규칙 먼저, 그다음 etcd · watchdog · Patroni 설치)
cd ~/chargeops/ansible && source ~/venv/bin/activate
ansible-playbook site.yml --limit 'db:mgmt' --tags firewall      # 2379 · 2380 · 8008 (없으면 patroni.yml 이 첫 작업에서 멈춤)
ansible-playbook patroni.yml
```

성공 결과: `1-1. etcd 확인` 에 `is healthy` 3줄, 마지막 `PLAY RECAP` 이 모두 `failed=0`. 이 단계에서는 Patroni 를 **시작하지 않는다** — 운영 DB 는 그대로 postgresql-17 로 돈다.

## 5. 전환 (B) — `convert.sh` (사용자 확인 뒤)

```bash
# ===== 실행할 명령어 ===== (mgmt — 중간에 백업 시점 · 동의를 묻는다)
bash ~/chargeops/db/ops/patroni/convert.sh
```

| 단계 | 하는 일 | 성공 표시 |
|---|---|---|
| ① 점검 | 역할 자동 판별, 서비스 · watchdog · etcd 3대 · 복제 streaming · 앱 쓰기 | 모두 통과해야 다음 |
| ② 백업 시점 | 최근 pgBackRest 백업 3개 표시 → Enter | 오늘 백업이 없으면 먼저 `chargeops-backup` |
| ③ Standby | postgresql-17 정지 · mask, 수동 Standby 설정(`zz-standby.conf` · auto.conf 의 primary_conninfo) 걷어 냄 — 원본은 `*.pre-patroni`. 서버별 설정을 데이터 폴더 밖으로 | |
| ④ Primary | 체크포인트 → postgresql-17 정지 · mask → **Patroni 시작** | `Leader (running)` |
| ⑤ 쓰기 | PgBouncer restart → 앱 경로 쓰기 | `쓰기 재개 — 멈춘 시간 N초` (기대 10~30초) |
| ⑥ Standby | Patroni 시작 → Replica | `Replica (streaming)` |
| ⑦ 슬롯 | 옛 슬롯 `db01`·`db02` 정리 (전환 동안은 `ignore_slots` 로 지켜서 Standby 가 붙기 전 WAL 이 지워지지 않게) | |
| ⑧ 확인 | 구성원 표 · ttl 값 · `Software Watchdog activated` · pgBackRest check | `✅ 전환 완료` |

중간에 멈추면 스크립트가 원인 명령을 보여 준다. 되돌리려면 8장.

## 6. 시험 (C) — `drill.sh` (사용자 확인 뒤, 하나씩)

```bash
# ===== 실행할 명령어 ===== (mgmt — 대상은 그때의 Leader. 끝나면 status.sh 로 2~3분 뒤 원상 확인)
MODE=isolate      bash ~/chargeops/db/ops/patroni/drill.sh     # 재부팅 없음 — 먼저
MODE=freeze-patroni bash ~/chargeops/db/ops/patroni/drill.sh   # ⚠ Leader VM 재부팅 (watchdog) — 시험 ③
MODE=kill-all     bash ~/chargeops/db/ops/patroni/drill.sh     # 프로세스 강제 종료 → 5초 뒤 Patroni 재시작 · 스스로 복구 또는 승격
MODE=poweroff     bash ~/chargeops/db/ops/patroni/drill.sh     # 스크립트가 보여 주는 VBoxManage 명령을 사람이 PowerShell 에서
bash ~/chargeops/db/ops/patroni/status.sh
TO=db-01 bash ~/chargeops/db/ops/patroni/switchover.sh         # 평소 방향(db-01 Leader)으로
```

- 측정은 앱과 같은 길(6432)로 0.5초마다 쓰기 확인(`txid_current()` — 표에는 아무것도 쓰지 않음) + 두 서버에 직접 'Primary 인가' 를 1초마다.
- **PASS** = 쓰기 받는 서버가 한 번만 바뀌고(옛 서버로 돌아가지 않음) 같은 순간 Primary 두 대가 0회.
  **PASS (스스로 복구)** = 승격 없이 옛 Leader 가 다시 쓰기를 받음 + Primary 두 대 0회 — `kill-all`(systemd 재시작), `freeze-patroni`(watchdog 재부팅이 확인된 경우).
  기록은 `~/patroni-drill-<MODE>-<시각>.log`.
- **쓰기 멈춘 시간** = 옛 Leader 마지막 쓰기 → 장애 뒤 첫 쓰기. `poweroff` 의 '장애 시각' 은 사람이 Enter 를 누른 시각이라 몇 초 늦다.
- 시험 전에 **etcd 리더**를 보여 준다. DB Leader 와 같은 서버면 서버째 멈추는 시험(poweroff · freeze-patroni)에서 전환이 약 30초 늦어질 수 있다 (11장).
- 재부팅 · 전원을 끈 서버는 다시 켜지면 **격리 부팅 없이** Patroni 가 Replica 로 되감아 붙인다 (Leader 열쇠가 이미 다른 서버에 있으므로).
- 마지막에 `bash deploy/final-server/check-all.sh` — Patroni 항목이 하나 늘어 합계 PASS 22.

**운영 DB 시험 결과 (영수 PC, 2026-10-16 ~ 17)** — 네 가지 모두 Primary 두 대 0회 · 데이터 손실 0

| 시험 | 판정 | 쓰기 멈춤 | 무엇이 일어났나 |
|---|---|---|---|
| `isolate` (Leader 를 etcd 에서 고립) | PASS | 15.1초 | db-01 스스로 읽기 전용 강등 → db-02 승격 |
| `kill-all` (Patroni + PostgreSQL 강제 종료) | PASS (스스로 복구) | 약 15초 | systemd 가 5초 뒤 Patroni 재시작 → 열쇠가 남아 있어 db-01 이 그대로 Leader |
| `freeze-patroni` (Patroni 얼림) | PASS (재부팅 뒤 스스로 복구) | 약 38초 | 약 21초 뒤 watchdog 재부팅 → db-01 이 etcd 리더도 겸해 열쇠 연장 → 재부팅된 db-01 이 다시 Leader |
| `poweroff` (VM 전원 끔) | PASS | 35.5초 | db-02 자동 승격 (TL 17) · 다시 켠 db-01 은 격리 부팅 없이 Replica 로 붙음 |
| 원복 `switchover` (db-02 → db-01) | — | 4.0초 | 명령부터 db-01 쓰기 확인까지 · 뒤이어 `check-all` PASS 22 |

## 7. 평소 운영

| 하고 싶은 것 | 명령 (mgmt) |
|---|---|
| 상태 | `bash db/ops/patroni/status.sh` |
| 역할 교대 (점검 · 원복) | `TO=db-01 bash db/ops/patroni/switchover.sh` |
| 자동 전환을 잠시 멈춤 (etcd · mgmt 점검, VM 재부팅 작업 전) | `ssh db-01 "sudo -u postgres patronictl -c /etc/patroni/patroni.yml pause --wait"` → 끝나면 `resume` |
| 클러스터 값(ttl 등) 바꾸기 | `ssh db-01 "sudo -u postgres patronictl -c /etc/patroni/patroni.yml edit-config"` (group_vars 의 `ha:` 도 같이) |
| PostgreSQL 재시작 (설정 반영) | `ssh db-01 "sudo -u postgres patronictl -c /etc/patroni/patroni.yml restart chargeops-db db-02 --force"` (Replica 먼저) |
| Replica 를 처음부터 다시 복사 | `… reinit chargeops-db db-02 --force` |

**VM 끄고 켜기** — 끌 때는 **Replica 먼저, Leader 나중** (누가 Leader 인지는 `status.sh`).
Leader 를 먼저 끄면 Patroni 가 정상 종료하면서 열쇠를 놓아, 남은 Replica 가 바로 승격된다(데이터 손실은 없지만 역할이 뒤바뀜).
예: db-01 이 Leader 면 `vagrant halt loadgen edge k8s-w01 k8s-w02 k8s-cp01` → `vagrant halt db-02` → `vagrant halt db-01` → `vagrant halt mgmt`.
켤 때는 지금처럼 mgmt → db-01 · db-02 (etcd 2대가 떠야 과반) → after-up. 가장 최근 WAL 을 가진 서버가 Leader 가 된다.

**하지 말 것**

- `systemctl start postgresql-17` · `pg_ctl` 로 직접 띄우기 — Patroni 몰래 띄운 PostgreSQL 은 감시 밖이다 (그래서 mask).
- Patroni 를 `kill` 로 끄기 — 죽으면 systemd 가 5초 뒤 다시 띄우지만(Restart=on-failure), 멈춘(hang) 상태가 25초 넘으면 watchdog 이 그 서버를 재부팅한다. 끌 때는 `systemctl stop patroni` (정상 종료는 watchdog 도 정상 해제).
- etcd 2대를 동시에 끄기 (예: mgmt 재부팅 + DB 하나 점검) — 과반을 잃으면 Leader 가 **스스로 읽기 전용**으로 내려간다(④ 펜싱과 같은 동작). 그런 작업 전에는 `pause`.

## 8. 되돌리기 — `rollback.sh` (사용자 확인 뒤)

```bash
# ===== 실행할 명령어 ===== (mgmt — Leader 였던 서버가 Primary 로 남는다. 끝에 다음 명령을 보여 줌)
bash ~/chargeops/db/ops/patroni/rollback.sh
OLD=<Replica 였던 서버> NEW=<Leader 였던 서버> bash ~/chargeops/db/ops/failover/rejoin.sh
```

Patroni 를 정상 종료 → `postgresql.base.conf` 를 `postgresql.conf` 로 되돌림 → postgresql-17 unmask · 시작 → Patroni 슬롯 정리 → etcd 안의 기록 삭제. etcd · watchdog 모듈 · 패키지는 남는다(아무도 watchdog 을 열지 않으면 재부팅은 없다).

## 9. 멘토 서버 적용 (D)

1. 영수 PC 에서 4~6장이 모두 PASS 인 상태의 저장소를 멘토 서버 `C:\chargeops-lab` 에 맞춘다 → mgmt 에서 rsync.
2. 오늘 백업 확인 (`convert.sh` ② 가 보여 줌). db VM 에는 vagrant snapshot 을 쓰지 않는다.
3. 4장 → 5장 → **12장 ①**(pg_hba 한 줄 — PGDATA 안 파일이라 저장소를 맞춰도 서버에는 안 들어감) → `status.sh` → `check-all.sh`.
4. 시험(6장)은 멘토 서버에서는 `DRILL_ON_SERVER=yes` 를 붙여야 돈다 — 빠른 실행표 30 처럼 사용자 확인 뒤, `isolate` 하나만 권장.
5. Claude Code 로 진행할 때: 이 문서의 명령을 그대로 실행하게 하고, 새로 만들게 하지 않는다. `convert.sh` · `drill.sh` · `rollback.sh` 는 사용자 확인 뒤에만.

## 10. 한계 (실습 범위에서 받아들인 것)

| 한계 | 실서비스라면 |
|---|---|
| etcd · Patroni REST 가 TLS · 인증 없이 평문 (데이터망 · 관리망만 방화벽 허용) | etcd 클라이언트 인증서, REST `authentication` |
| softdog 은 소프트웨어 — 커널째 멈추면 동작 못 함 | 하드웨어 watchdog (iTCO, IPMI) |
| etcd 3번째가 mgmt — 관리 서버와 DB 판정이 한 장애에 묶임 | 별도 작은 서버 3대 또는 다른 랙 |
| 동기 복제 아님 — 자동 전환 때 최대 1MB(마지막 수 초) 손실 가능 | `synchronous_mode: true` (쓰기 지연과 교환) |
| etcd 가 DB 서버에 같이 있음 — DB Leader 가 etcd 리더도 겸하다 서버째 죽으면 etcd 리더 교체 때 Leader 열쇠가 ttl(30초)만큼 연장되어 전환이 늦어짐 (2026-10-17 실측) | etcd 를 DB 와 다른 서버 3대에. 실습에서는 계획 작업 전 `etcdctl move-leader` 로 etcd 리더를 mgmt 로 옮길 수 있음 (선택) |

## 11. 막혔던 점 (시험 클러스터 2026-10-16 · 운영 DB 2026-10-16 ~ 17)

| 증상 | 원인 | 해결 |
|---|---|---|
| Patroni 를 죽였는데 PostgreSQL 이 계속 Primary → 두 대 | `pkill -f "patroni …"` 가 명령을 보낸 셸 자신도 맞춰 죽어서 뒤 명령(PostgreSQL 종료)이 안 돌았다 + watchdog 없음 | 패턴에 `[p]atroni` (자기 자신은 안 맞음). watchdog `required` |
| `patronictl` 권한 오류 | 설정 파일이 600 postgres | `sudo -u postgres patronictl -c …` |
| etcd 패키지 없음 | Rocky 9 · PGDG 저장소에 etcd 서버 없음 | GitHub 배포판 + SHA256SUMS 검증 (`patroni.yml` 1단계) |
| 재부팅한 서버의 etcd 에 `No route to host` | etcd 가 자동 시작이 아니었고 포트도 임시로만 열었음 | etcd · Patroni `enabled`, 포트는 fw_rules(영구) |
| Patroni 로컬 접속이 거부될 뻔함 (설계 검토에서 발견) | TCP 로 붙으면 localhost(127.0.0.1) 인데 pg_hba 는 127.0.0.1 의 postgres 를 거부 | `use_unix_socket: true` → `local all postgres peer` |
| 운영 DB drill `isolate`: 강등 · 승격은 됐는데 앱 쓰기가 2분 넘게 실패 (2026-10-16 20:56) | ① 강등된 db-01 의 PgBouncer 가 PostgreSQL 이 꺼진 채 앱 접속을 받아 최대 120초 붙잡음 ② Patroni 가 승격 명령 직후(아직 읽기 전용) 콜백을 불러, 그때 restart 한 db-02 PgBouncer 가 '읽기 전용' 을 기억 | 콜백을 on_start · on_stop · on_role_change 에 걸고, 내려가면 PgBouncer stop · 역할이 실제로 바뀐 것을 확인한 뒤 restart |
| 운영 DB drill `freeze-patroni`: watchdog 재부팅은 됐는데 db-02 가 승격하지 않고 재부팅된 db-01 이 다시 Leader (2026-10-17 01:12, FAIL 로 판정됨) | db-01 이 **etcd 리더**였다 (01:12:56.5 db-02 의 etcd 에 `lost leader c` → `became leader`). etcd 는 리더가 바뀔 때 모든 열쇠의 만료를 다시 ttl 만큼 늘린다 → Leader 열쇠 만료가 약 01:13:26 으로 밀림 → 그사이 db-01 이 부팅해(01:13:03) 자기 이름의 열쇠를 다시 잡음 (`I am the leader but not owner of the lease`). Primary 두 대 0 · 손실 0 | 안전한 동작으로 보고 drill 판정에 'PASS (재부팅 뒤 스스로 복구)' 추가. 시험 · status 에 etcd 리더 표시 (10장 한계) |
| Replica 의 Patroni 로그에 10초마다 `no pg_hba.conf entry for replication connection from host "127.0.0.1", user "replicator"` (2026-10-17 발견) | Patroni 가 자기 timeline 을 localhost 복제 연결로 읽는데 pg_hba 는 replicator 를 10.10.30.0/24 에서만 받음 → `timeline=None`. 승격은 됐지만 판단 정보 하나가 빔 | pg_hba `local replication replicator scram-sha-256` + patroni.yml `use_unix_socket_repl: true` (12장) |
| drill 결과의 '쓰기 멈춘 시간' 이 `?초`, 옛 Leader 마지막 쓰기가 복구 뒤 시각으로 찍힘 | 승격이 없으면 계산을 안 했고, 마지막 쓰기를 기록 전체에서 찾았음 | 첫 실패 직전의 마지막 쓰기 → 장애 뒤 첫 쓰기(어느 서버든)로 계산 |

## 12. 2026-10-17 보강 적용 (이미 전환을 끝낸 서버)

pg_hba 한 줄 + Patroni 설정 한 줄. **PostgreSQL · Patroni 재시작 없음** (pg_hba 는 reload, Patroni 는 `systemctl reload` = 설정 다시 읽기). 쓰기 중단 없음.

```bash
# ===== 실행할 명령어 ===== (mgmt — 한 줄씩. ① pg_hba 두 서버 → ② Patroni 설정 → ③ 확인)
for h in db-01 db-02; do ssh $h "sudo grep -q '^local *replication *replicator' /var/lib/pgsql/17/data/pg_hba.conf || sudo sed -i '/^local *all *all *scram-sha-256/a local   replication        replicator                                           scram-sha-256' /var/lib/pgsql/17/data/pg_hba.conf; sudo -u postgres psql -XAtc 'select pg_reload_conf()'; sudo grep -n '^local' /var/lib/pgsql/17/data/pg_hba.conf"; done
cd ~/chargeops/ansible && ansible-playbook patroni.yml --tags patroni
ssh db-02 "sudo journalctl -u patroni --since '-1min' --no-pager | grep -c 'no pg_hba.conf entry'"
```

- ① 서버마다 `t`(reload 성공) 와 `local` 세 줄(postgres peer · all · replication replicator)이 보이면 됨.
- ② 끝에 `RUNNING HANDLER [Patroni 설정 다시 읽기]` 가 보이고 failed=0.
- ③ 1분쯤 뒤 `0` 이면 끝 (Replica 의 오류가 사라짐). 0 이 아니면 Replica 의 Patroni 만 재시작: `ssh db-02 "sudo systemctl restart patroni"` — Replica 는 쓰기와 무관, PostgreSQL 은 그대로 (`KillMode=process`).
- pg_hba.conf 는 PGDATA 안에 있어 pg_rewind · 복제 재생성 때 상대 서버 것이 복사된다 → **두 서버 모두** 같은 줄이 있어야 한다.

