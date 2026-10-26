"""권한 정책 — '누가 무엇을 할 수 있는가'를 한 곳에 모은 순수 함수 (pytest 로 CI 검증)"""
from dataclasses import dataclass

RANK = {"Viewer": 0, "Operator": 1, "Lead": 2, "Admin": 3}

# 동작별 최소 역할과 승인 필요 인원(0 = 즉시 실행)
RULES = {
    "detail":         ("Viewer", 0),
    "ask":            ("Viewer", 0),
    "watch":          ("Operator", 0),
    "false_positive": ("Operator", 0),
    "soft_reset":     ("Operator", 0),
    "hard_reset":     ("Lead", 1),     # 요청자 ≠ 승인자
    "disable":        ("Lead", 1),     # 사유 필수
    "batch_reset":    ("Lead", 2),     # 5대 이상 일괄 → Lead 2인 승인
    "deploy":         ("Admin", 0),
    "dr_drill":       ("Admin", 1),    # Admin 요청 + Lead 승인
    "role_grant":     ("Admin", 0),
}
REASON_REQUIRED = {"disable", "false_positive"}


@dataclass
class Decision:
    allowed: bool
    approvals_needed: int = 0
    reason: str = ""


def decide(action: str, role: str, severity: str | None = None, targets: int = 1) -> Decision:
    """요청 시점 판정: 실행 가능 여부 + 필요한 승인 수"""
    if action not in RULES:
        return Decision(False, reason=f"알 수 없는 동작: {action}")
    need, approvals = RULES[action]
    if action == "soft_reset" and severity in ("SEV1", "SEV2"):
        need = "Lead"                                   # 고등급 장애는 Lead 이상만
    if action == "soft_reset" and targets >= 5:
        action, (need, approvals) = "batch_reset", RULES["batch_reset"]
    if action == "batch_reset" and role == "Admin":
        return Decision(False, reason="일괄 리셋은 Lead 만 요청할 수 있습니다")
    if RANK.get(role, 0) < RANK[need]:
        return Decision(False, reason=f"{need} 이상 권한이 필요합니다 (현재: {role})")
    return Decision(True, approvals)


def can_approve(action: str, approver_role: str, requester: str, approver: str,
                already_approved: list[str]) -> Decision:
    """승인 시점 판정: Lead 이상 · 요청자 본인 금지 · 같은 사람 중복 승인 금지"""
    if approver == requester:
        return Decision(False, reason="요청자 본인은 승인할 수 없습니다 (요청자 ≠ 승인자)")
    if approver in already_approved:
        return Decision(False, reason="이미 승인했습니다")
    if RANK.get(approver_role, 0) < RANK["Lead"]:
        return Decision(False, reason=f"승인은 Lead 이상만 가능합니다 (현재: {approver_role})")
    return Decision(True)
