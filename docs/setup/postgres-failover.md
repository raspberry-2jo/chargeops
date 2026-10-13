# PostgreSQL 장애 전환 · 재편입 · 원복 훈련 기록

- 담당: 강영수 (리뷰 조인성)
- 관련 WBS: 4.4 DB 장애 전환 훈련 · 쿼리 튜닝 — 완료 기준: 전환 시간 측정 · 튜닝 전후 비교 (이 문서는 **장애 전환** 부분)
- 버전: versions.md 참고 (PostgreSQL 17, PgBouncer 1.26)
- 파일: `db/ops/failover/` (`failover.sh` · `rejoin.sh` · `switchover.sh`)
- 앞 단계: [`postgres-replication.md`](postgres-replication.md)(복제 · PgBouncer 두 곳), [`pgbackrest-pitr.md`](pgbackrest-pitr.md)(백업)

## 1. 왜 / 무엇을

Primary(`db-01`)가 서버째 죽었을 때 Standby(`db-02`)를 승격하고, 죽었던 서버를 Standby로 다시 붙이고, 평소 상태(db-01 Primary)로 되돌리는 절차입니다. 앱은 주소 두 개 + `target_session_attrs=read-write`로 쓰기 되는 쪽을 스스로 고르므로, **앱 설정 · `pgbouncer.ini`는 바꾸지 않습니다.**

| 스크립트 | 언제 | 하는 일 |
|---|---|---|
| `failover.sh` | Primary가 죽었을 때 | 확인 → **펜싱**(구 Primary의 PgBouncer · PostgreSQL 중지 + 자동 시작 해제. 접속이 안 되면 사람이 전원 꺼짐을 확인) → 승격 → 새 Primary의 PgBouncer **restart** → 쓰기 재개 확인(감사 기록 한 줄) |
| `rejoin.sh` | 장애 전환 뒤 | 구 Primary를 `pg_rewind`로 새 Primary의 Standby로. 복제 슬롯 · 접속 비밀번호 · 서버별 설정(`zz-local.conf`) 처리, 자동 시작 되살리기 |
| `switchover.sh` | 둘 다 살아 있을 때 (원복) | 지금 Primary를 깨끗하게 멈춤 → 마지막 WAL을 Standby가 받았는지 확인(손실 0) → 승격 → 멈춘 쪽을 `rejoin.sh`로 재편입 |

세 스크립트 모두 **방향이 거꾸로면 아무것도 바꾸지 않고 중단**합니다(살아 있는 Primary를 되감는 사고 방지).

**규칙 세 가지** — 실습 PC 첫 훈련(2026-10-12)에서 어겨서 생긴 일입니다.

| 규칙 | 어기면 |
|---|---|
| ① 역할이 바뀐 서버의 PgBouncer는 **restart** (reload 아님) | PgBouncer 1.26은 서버의 '읽기 전용 / 쓰기 가능' 상태를 기억하고 reload로는 지우지 않음 → 승격 뒤에도 두 문 모두 `session is read-only`, **CSMS 쓰기 18분 멈춤** |
| ② 펜싱 = PgBouncer · PostgreSQL **둘 다** 멈추고 **자동 시작 해제** | PostgreSQL만 멈추면 PgBouncer가 옛 '쓰기 가능'을 기억한 채 살아 있어 쿼리가 최대 120초 대기. 자동 시작이 켜져 있으면 다시 켜지는 순간 Primary로 뜸 |
| ③ 꺼졌던 구 Primary는 **격리 부팅** — 망을 끊고 켜서 두 서비스를 끈 것을 **눈으로 확인한 뒤** 망 연결 | 그냥 켜면 Primary로 떠서 앱이 첫 주소인 그쪽에 씀. 그 쓰기는 재편입 때 되감겨 사라짐. 실습 PC: **38초 동안 Primary 두 대** |

## 2. 준비

전제: 2.7(복제 · PgBouncer 두 곳)이 끝남. 관리 서버(mgmt)에서 두 DB 서버로 SSH(sudo)가 됨. 저장소는 mgmt의 작업 폴더에 있음.

```bash
# ===== 실행할 명령어 ===== (mgmt — 비밀번호 파일. 값은 각자, 파일은 600. 스크립트에는 비밀번호가 없다)
#   한 줄 형식 (호스트:포트:DB:계정:비밀번호)
#     db-01:6432:chargeops:app_writer:<각자 설정>      ← 쓰기 재개 확인 · 감시 (두 서버 모두)
#     db-02:6432:chargeops:app_writer:<각자 설정>
#     db-01:5432:*:postgres:<각자 설정>                ← rejoin.sh 의 pg_rewind
#     db-01:5432:replication:replicator:<각자 설정>    ← rejoin.sh 의 복제 접속
(umask 077; cat >> ~/.pgpass)    # 붙여 넣고 Ctrl+D
chmod 600 ~/.pgpass
```

- `rejoin.sh`는 postgres · replicator 비밀번호를 이 파일에서 읽어 **표준입력으로** 구 Primary의 `/var/lib/pgsql/.pgpass`(600)에 넣습니다. 줄이 없으면 아무것도 바꾸지 않고 중단합니다.
- 서버 이름이 다르면 변수로: `OLD=… NEW=…`(구 · 새 Primary), `DB_HOSTS=…`(앱 주소 목록, 기본 `db-01,db-02`).

**창 세 개** — 감시와 명령을 한 창에서 하면 시간을 잴 수 없습니다.

| 창 | 할 일 |
|---|---|
| A (mgmt) | 감시만 — 아래 줄을 끝날 때까지 켜 둠 |
| B (mgmt) | 스크립트 |
| C (PC의 PowerShell) | VM 전원 · 케이블 (가상화 도구 명령은 **사람이 직접**) |

```bash
# ===== 실행할 명령어 ===== (창 A — 앱과 같은 접속으로 1초마다. 결과는 ~/ha-drill.log)
while true; do printf '%s ' "$(date +%T)"; psql "host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3" -XAt -c "select pg_is_in_recovery()" -c '\conninfo' 2>&1 | tr '\n' ' '; echo; sleep 1; done | tee ~/ha-drill.log
```

## 3. 훈련 순서

**① 서버째 다운** — 창 C (실습 VirtualBox 예시)
```powershell
vagrant halt --force db-01; Get-Date -Format HH:mm:ss
```
→ 창 A가 `timeout expired …(db-01) … session is read-only …(db-02)`로 바뀜. 이 시각부터 창 A의 첫 성공 줄까지가 **쓰기 중단 시간**.

**② 장애 전환** — 창 B
```bash
bash db/ops/failover/failover.sh
```
→ `⚠ db-01 에 접속할 수 없음`에서 멈추면 가상화 화면에서 db-01이 **전원 꺼짐**인지 보고 Enter. 켜진 채 망만 끊긴 것이면 승격하지 않음(망이 돌아오는 순간 Primary 둘). 끝에 `action_id … · on host "db-02"`, `✅ 전환 완료`.

**③ 격리 부팅** — 창 C → db-01 안 → 창 C (실습 VirtualBox 예시, 한 줄씩)
```powershell
& "$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe" modifyvm chargeops-db-01 --cableconnected2 off
& "$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe" startvm chargeops-db-01 --type headless
vagrant ssh db-01
```
```bash
ip -br link show eth1                                   # DOWN … NO-CARRIER = 실습망에서 떨어져 있음
sudo systemctl disable --now pgbouncer postgresql-17
systemctl is-active pgbouncer postgresql-17; systemctl is-enabled pgbouncer postgresql-17
exit
```
`inactive inactive disabled disabled`를 **본 뒤에만** 케이블 연결:
```powershell
& "$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe" controlvm chargeops-db-01 setlinkstate2 on
& "$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe" showvminfo chargeops-db-01 --machinereadable | Select-String cableconnected2
```
- `vagrant ssh db-01 -c '…'`처럼 한 줄에 넣지 않음 — 부팅 중이면 아무 출력 없이 실행되지 않음(실습 PC). 들어가서 눈으로 확인.
- `vagrant up` 대신 `startvm`: vagrant up은 NIC 설정을 다시 써서 케이블이 빠진 채라는 보장이 없음.
- 마지막 줄이 `"on"`이어야 다음 `vagrant up` 때도 망에 붙음.
- 다른 가상화 · 물리 서버면: **망을 끊고 켬 → 두 서비스 disable --now → 확인 → 망 연결**. 순서만 같으면 됨.

**④ 재편입** — 창 B
```bash
ssh db-01 "systemctl is-active pgbouncer postgresql-17"   # inactive inactive
bash db/ops/failover/rejoin.sh
```
→ 서버째 다운 뒤에는 `pg_rewind: Done!`(되감을 블록이 있음)이 정상. 끝에 `db-01 | … | streaming`, `db-01: PgBouncer active · Standby t`.

**⑤ 원복 (db-01 Primary)** — 창 B
```bash
bash db/ops/failover/switchover.sh
```
→ `③ … 수신 여부 t`(손실 0), `on host "db-01"`, 재편입은 `no rewind required`, 끝에 `db-02: PgBouncer active · Standby t`.

**⑥ 끝** — 창 B, 그리고 창 A는 `Ctrl+c`
```bash
ssh db-01 "sudo -u postgres psql -XAtc 'select application_name, state from pg_stat_replication'"   # db-02|streaming
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops --type=full backup'   # 타임라인이 바뀌었으니 새 전체 백업 (pgbackrest-pitr.md 5장)
```

## 4. 확인 결과

```
# ===== 실행 결과 ===== (실습 PC 2026-10-12 19:28, 이 스크립트 그대로 · 창 A 기록 기준 요약)
전원 끔 19:28:55 → 창 A 첫 성공 19:29:25 (db-02)          ← 서버째 다운 쓰기 중단 약 30초 (목표 60초)
failover.sh: 접속 확인 5초 → 사람 확인(Enter) 16초 → Enter 뒤 4.5초에 쓰기 확인
격리 부팅: eth1 NO-CARRIER, 두 서비스 disabled 확인 뒤 연결 → rejoin.sh: pg_rewind 664MB Done!, db-01 streaming
switchover.sh: ② ~ ⑤ 2.7초, 종료 체크포인트 수신 t, db-02 no rewind required
다운 뒤 db-01 로 쓴 줄 0 (Primary 두 대 없음), CSMS 충전기 3대 다시 연결(connected:3)
```

| 비교 (같은 날, 창 A 기록) | 손으로 친 명령 (오전) | 스크립트 (저녁) |
|---|---|---|
| 서버째 다운 → 쓰기 재개 | 55초 (10:58:49 → 10:59:44) | **30초** |
| 원복 쓰기 중단 | 18초 (11:03:54 → 11:04:12) | **3초** (19:32:00 → 19:32:03) |

추가로, 두 서버를 흉내 낸 환경(PostgreSQL 두 개 + PgBouncer 두 개)에서 이 PR의 경로 그대로 10가지를 돌려 모두 통과: 살아 있는 Primary 전환 · 재편입 · 원복, 서버째 다운 전환(Enter 확인) · 격리 부팅 · 재편입(`Done!`) · 원복(`no rewind required`), 방향을 거꾸로 준 세 스크립트 모두 중단. 감시 기록에서 다운 뒤 db-01로 쓴 줄 0.

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 승격 뒤에도 앱이 두 문 모두에서 `session is read-only` (18분) | PgBouncer 1.26의 reload는 상태 기억을 지우지 않음 → **restart** (스크립트에 반영, 규칙 ①) |
| 구 Primary를 켜자 38초 동안 Primary 두 대 | 자동 시작으로 Primary로 뜸 → **격리 부팅** (규칙 ③) |
| 격리 부팅의 `vagrant ssh db-01 -c '…'`가 아무 출력 없음 | VM이 아직 부팅 중이라 명령이 안 돎 → 직접 들어가서 `inactive` · `disabled`를 보고 연결 |
| 재편입 뒤 복제가 안 붙음 · Standby 메모리 설정이 Primary에 남음 | `pg_rewind`가 설정 파일까지 새 Primary 것으로 덮어씀 → `rejoin.sh`가 `primary_conninfo` · 슬롯 줄을 지우고 `zz-local.conf`를 되돌리고 접속 정보는 `conf.d/zz-standby.conf` 한 곳에 |
| 장애 중 창 A가 4초 간격 · `action_id`가 건너뜀 | 꺼진 db-01을 `connect_timeout` 3초 기다림(격리 부팅으로 망에 붙이면 즉시 거부 → 1초 간격) · 시퀀스는 WAL에 32개씩 미리 기록됨 — 둘 다 정상 |

## 6. 남은 것 (4.4 일정 10/27)

- **팀 훈련**: 10/27에 인성 님과 위 순서 그대로 1회 — 창 A 기록(`ha-drill.log`)으로 전환 시간 측정
- **쿼리 튜닝 (4.4 뒷부분)**: 대상이 통계 뷰(`db/views`, WBS 3.8)라서 뷰가 들어온 뒤 튜닝 전후 비교를 따로 올림
- 승인 연결: 지금은 사람이 승인 뒤 mgmt에서 실행. Slack 승인 버튼(3.11)과 잇는 것은 그 작업 뒤에
