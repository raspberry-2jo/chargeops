# infra/ansible

| 파일 | 내용 |
|---|---|
| `patroni.yml` · `templates/patroni.yml.j2` | DB 자동 장애 전환 설치 (etcd 3대 · watchdog · Patroni). 절차: [`docs/setup/postgres-patroni.md`](../../docs/setup/postgres-patroni.md) |

실행은 실습 묶음(`chargeops-lab/ansible/`)에서 한다 — 그쪽 inventory · `group_vars` 와 `../db/ops/patroni/` 경로를 쓰기 때문. 이 playbook 이 읽는 값:

```yaml
# group_vars/all/main.yml
versions: { etcd: "3.5.17", patroni: "4.1" }
ha:
  scope: chargeops-db
  etcd_members: [db-01, db-02, mgmt]
  ttl: 30
  loop_wait: 10
  retry_timeout: 10
  watchdog_safety_margin: 5
  maximum_lag_on_failover: 1048576
# group_vars/db.yml 의 fw_rules: 2379 · 2380 · 8008 ← 데이터망 · 관리망
# group_vars/mgmt.yml 의 fw_rules: 2379 · 2380 ← 데이터망
```

비밀번호는 저장소에 없다 — 실행하는 서버(mgmt)의 `~/.pgpass` 에서 읽어 DB 서버의 `/etc/patroni/patroni.yml`(600)에만 쓴다.
