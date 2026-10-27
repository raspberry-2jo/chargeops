# PostgreSQL 서버 SSL 설치 기록

- 담당: 윤하빈
- 관련 WBS: 3.16 · 3.17 DB 지원 (AI 게이트웨이 · RAG 접속 암호화), 3.5 사설 CA
- 버전: versions.md 참고 (PostgreSQL 17, OpenSSL 은 OS 기본)
- 적용: 2026-10-22 db-02 → db-01

## 1. 왜 / 무엇을
인프라(D 환경)의 AI 게이트웨이가 네트워크를 건너 DB 에 붙게 되면서, 오가는 데이터(비밀번호 · 쿼리 · 결과)가 암호화되도록 서버 SSL 을 켬.
서버 인증서는 우리 사설 CA(mgmt `~/chargeops/pki`, 충전기 mTLS 와 같은 CA)로 서명. 키는 DB 서버 안에서 만들고 밖으로 꺼내지 않음.
`ssl` 은 reload 로 바뀌는 설정이라 **재시작 없이** 켬. 기존 접속(복제 · PgBouncer · CSMS)은 `sslmode=prefer`(기본값)라 끊기지 않고, 새 연결부터 암호화됨.

## 2. 설치 명령어 (db-02 먼저 — Standby 라 앱이 안 씀 → 확인 뒤 db-01)
아래는 db-02 기준. db-01 은 `db-02` → `db-01`, `10.10.30.12` → `10.10.30.11` 로 바꿈.
```bash
# ===== 실행할 명령어 ===== (mgmt)
# 1) DB 서버 안에서 키 + 서명 요청서 (키는 postgres 소유 600)
ssh -n db-02 "sudo -u postgres openssl req -new -newkey rsa:2048 -nodes -keyout /var/lib/pgsql/17/data/server.key -subj '/O=ChargeOps Lab/OU=db/CN=db-02.chargeops.lab' -out /tmp/db-02.csr && sudo chmod 600 /var/lib/pgsql/17/data/server.key"
# 2) 요청서만 mgmt 로
ssh -n db-02 "sudo cat /tmp/db-02.csr" > ~/db-02.csr
# 3) 인증서에 넣을 이름 · IP (접속에 쓰는 이름과 같아야 verify-full 통과)
printf 'subjectAltName=DNS:db-02,DNS:db-02.chargeops.lab,IP:10.10.30.12\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n' > ~/db-02.ext
# 4) CA 로 서명 (CA 만료 2036-10-12 보다 짧게 3600일)
cd ~/chargeops/pki && openssl x509 -req -in ~/db-02.csr -CA ca.crt -CAkey ca.key -CAserial ca.srl -days 3600 -sha256 -extfile ~/db-02.ext -out ~/db-02.crt; cd -
# 5) 검증
openssl verify -CAfile ~/chargeops/pki/ca.crt ~/db-02.crt && openssl x509 -in ~/db-02.crt -noout -enddate -ext subjectAltName
# 6) 설치 (644, postgres 소유) + 요청서 지움
cat ~/db-02.crt | ssh db-02 "sudo install -o postgres -g postgres -m 644 /dev/stdin /var/lib/pgsql/17/data/server.crt && sudo rm -f /tmp/db-02.csr"
# 7) 권한 · 인증서와 키가 한 쌍인지 (해시 두 줄이 같아야 함)
ssh -n db-02 "sudo ls -lZ /var/lib/pgsql/17/data/server.crt /var/lib/pgsql/17/data/server.key; sudo openssl x509 -in /var/lib/pgsql/17/data/server.crt -noout -pubkey | sha256sum; sudo openssl pkey -in /var/lib/pgsql/17/data/server.key -pubout | sha256sum"
# 8) 켜기 (재시작 없이 reload)
ssh -n db-02 "sudo -u postgres psql -Atc 'ALTER SYSTEM SET ssl = on' && sudo -u postgres psql -Atc 'SELECT pg_reload_conf()' && sleep 2 && sudo -u postgres psql -Atc 'SHOW ssl'"
```

## 3. 설정 파일
- `postgresql.conf` 에 이미 있던 값: `ssl_cert_file = server.crt`, `ssl_key_file = server.key`, `ssl_min_protocol_version = TLSv1.2`
- `ssl = on` 은 `ALTER SYSTEM` 으로 `postgresql.auto.conf` 에 들어감 (`db/ops/postgresql/chargeops.conf` 는 바꾸지 않음 — 인증서 없이 새 서버에 `ssl = on` 이 먼저 들어가면 PostgreSQL 이 안 뜸)
- 접속 허용은 `db/ops/postgresql/pg_hba.conf` 의 `hostssl` 줄 (SSL 접속만 허용)

## 4. 확인 방법
```bash
# ===== 실행할 명령어 =====
ssh -n db-02 "sudo sh -c 'tail -5 /var/lib/pgsql/17/data/log/postgresql-$(date +%a).log'"
psql "host=db-02 port=5432 dbname=chargeops user=dba sslmode=verify-full sslrootcert=$HOME/chargeops/pki/ca.crt" -Atc "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid();"
ssh -n db-02 "sudo -u postgres psql -Atc 'SELECT status FROM pg_stat_wal_receiver'"
# db-01 적용 뒤: 기존 접속이 안 끊겼는지
psql -h db-01 -U dba -d chargeops -Atc "SELECT a.usename, s.ssl, count(*) FROM pg_stat_activity a JOIN pg_stat_ssl s USING (pid) WHERE a.usename IS NOT NULL GROUP BY 1,2 ORDER BY 1,2"
```
```
# ===== 실행 결과 ===== (2026-10-22)
... LOG:  received SIGHUP, reloading configuration files
... LOG:  parameter "ssl" changed to "on"
t|TLSv1.3
streaming
app_reader|t|1        ← 켠 뒤 새로 생긴 연결은 암호화
app_writer|f|2        ← 원래 있던 연결은 그대로 유지
```
- 인증서: notAfter 2036-08-30, SAN `DNS:db-0N, DNS:db-0N.chargeops.lab, IP:10.10.30.1N`
- CSMS 4/4 · 복제 streaming 그대로

## 5. 막혔던 점
- `cd /var/lib/pgsql/17/data` 가 vagrant 계정으로 막힘 (폴더가 postgres 전용 700) → 전체 경로 + `sudo` 로
- 되돌리기: `ALTER SYSTEM RESET ssl;` → `SELECT pg_reload_conf();`
- Standby 를 `pg_basebackup` · `pg_rewind` 로 다시 만들면 데이터 폴더의 `server.crt` · `server.key` 가 상대 서버 것으로 덮임 → 6) · 7) 을 그 서버 이름으로 다시
- 인증서 만료(3600일) 전에 같은 순서로 다시 발급
