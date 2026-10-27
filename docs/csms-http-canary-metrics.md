# CSMS HTTP 오류율 계측 — 기존 Canary를 위한 앱 변경

현재 적용 범위는 `kim_chanju` → Kubernetes `dev` namespace의 계측 검증까지다.
앱 `dev` 브랜치 Merge, Ops 배포, Prometheus 기반 운영 판정 활성화는 이번 범위에 포함하지 않는다.

`apps/csms/http_metrics.py`를 FastAPI ASGI middleware로 설치한다. 기존 Prometheus
registry와 `:9100/metrics`를 사용하며 새로운 포트·패키지·외부 수집 서비스는 필요 없다.
개발 테스트에만 기존 앱 의존성인 prometheus_client를 requirements-dev.txt에 추가했다.

## 지표

`chargeops_csms_http_requests_total{route,method,status_class,revision}`

- route는 FastAPI의 경로 템플릿이다. 미등록 URL은 `__unmatched__`, 미등록 HTTP method는 `OTHER`로 묶는다.
- 원본 URL, query string, 충전기 ID, 헤더, 토큰, 요청·응답 본문은 기록하지 않는다.
- revision은 `HOSTNAME=csms-<ReplicaSet hash>-<5자 suffix>`에서 얻는다. Argo의 Latest hash와 대응한다.
  다른 이름에서는 `unknown`으로 표시하므로 운영 판정을 활성화하기 전 실제 값을 확인해야 한다.
- `/healthz` GET의 각 상태 class는 0으로 초기화한다. 정상적인 오류 0건과 계측 누락을 구분한다.
- 처리된 HTTP 4xx/5xx와 처리되지 않은 예외의 5xx를 기록한다. 예외는 다시 발생시켜 기존 HTTP 동작을 유지한다.
- HTTP middleware는 OCPP WebSocket 수신 서버·DB 동작·복구 명령을 변경하지 않는다.

## 적용 순서

1. 앱 변경을 kim_chanju의 기존 빌드·Dev 검증 경로로 반영한다. 이 기능 브랜치 자체를 빌드해도 기존 publish 조건을 만족하지 않는다.
2. Dev CSMS 이미지가 계측 코드를 포함하는지 확인한다. 앱 코드 ConfigMap 덮어쓰기를 추가하지 않는다.
3. Dev의 CSMS metrics를 Prometheus가 수집하는지 확인한다. Ops의 기존 ServiceMonitor는 5초 간격이며,
   Dev에도 적절한 수집 경로가 있어야 한다. 없는 경우 Ops 권한을 넓히는 대신 별도 Dev ServiceMonitor를 만든다.
4. Grafana Explore에서 아래를 조회하고 namespace, pod, revision 라벨을 확인한다.

```promql
chargeops_csms_http_requests_total{namespace="dev",route="/healthz"}
```

5. `/healthz` 요청 시 2xx가 증가하고 5xx=0이 존재하는지 확인한다.
6. 이후 GitOps의 `docs/prometheus-canary-gate.md`에 따라 Prometheus 판정을 활성화한다.
   앱 변경만 반영해서는 기존 AnalysisTemplate이나 복구 기준이 바뀌지 않는다.

이 카운터는 서버가 관측한 HTTP 응답을 센다. 연결 실패·클라이언트 시간 초과는 서버 카운터만으로
완전히 측정할 수 없으므로 기존 probe.py의 HTTP/DB·Pod 검사를 유지한다.
이번 첫 판정 그래프는 전체 사용자 API 오류율이 아니라 `/healthz` 합성 HTTP 응답 오류율이다.

테스트: `python -m pytest -q tests/test_csms_http_metrics.py`.
ASGI 입력을 직접 실행해 응답 전달, 503, 예외, 경로 비식별화, WebSocket/lifespan 우회와 hash 추출을 확인한다.
실제 클러스터 배포·DB·충전기 연결은 로컬 테스트 범위가 아니다.
