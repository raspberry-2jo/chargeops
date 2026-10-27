"""Observe source PR decisions with existing Jenkins GitHub/Slack credentials.

Only GitHub GET requests and Slack posts are made. Delivery state is written
after each successful post; failed posts are retried on the next scheduled run.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import github_cd as github
import notify_ci

ROOT = '/repos/' + github.REPOSITORY
WINDOW = timedelta(minutes=2)


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('GitHub notification timestamps must include a timezone')
    return result


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def source_pr(pr):
    return (pr.get('base', {}).get('ref') == 'dev'
            and pr.get('base', {}).get('repo', {}).get('full_name') == github.REPOSITORY
            and pr.get('head', {}).get('ref') == 'kim_chanju'
            and pr.get('head', {}).get('repo', {}).get('full_name') == github.REPOSITORY)


def login(user):
    value = (user or {}).get('login', '')
    return value if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9\[\]-]{0,99}', value) else '(unknown)'


def decisions(pr, since):
    number = int(pr['number'])
    url = 'https://github.com/' + github.REPOSITORY + '/pull/' + str(number)
    events = []
    for review in github.pages(ROOT + '/pulls/' + str(number) + '/reviews'):
        state = review.get('state')
        if state not in ['APPROVED', 'CHANGES_REQUESTED'] or not review.get('submitted_at'):
            continue
        at = timestamp(review['submitted_at'])
        if at < since:
            continue
        label = 'PR 리뷰 승인' if state == 'APPROVED' else 'PR 수정 요청 (Request changes)'
        lines = ['[ChargeOps] ' + label, 'PR #' + str(number) + ' | kim_chanju → dev',
                 '리뷰어: ' + login(review.get('user')), '시간: ' + at.isoformat(), url]
        if review.get('commit_id') != pr.get('head', {}).get('sha'):
            lines.append('이전 커밋에 대한 리뷰입니다. 현재 변경의 승인으로 간주하지 않습니다.')
        if state == 'APPROVED':
            lines.append('리뷰 승인 알림입니다. Ops 배포는 PR Merge 후 CI·Dev 검증을 통과해야 진행됩니다.')
        else:
            lines.append('수정 요청이며 PR은 열려 있습니다. 이 리뷰 이벤트로 배포하지 않습니다.')
        events.append((at, 'review:' + str(number) + ':' + str(int(review['id'])), '\n'.join(lines)))
    if pr.get('state') == 'closed' and pr.get('closed_at'):
        at = timestamp(pr['closed_at'])
        if at >= since:
            merged = bool(pr.get('merged'))
            label = 'PR Merge 완료' if merged else 'PR 거부·종료 (Merge 없이 닫힘)'
            lines = ['[ChargeOps] ' + label, 'PR #' + str(number) + ' | kim_chanju → dev',
                     '시간: ' + at.isoformat(), url]
            if merged:
                lines.extend(['Merge 처리자: ' + login(pr.get('merged_by')),
                              'dev Jenkins Job의 승인 확인·CI·Dev 재검증 대상입니다. Ops 배포 완료 알림은 별도입니다.'])
            else:
                lines.append('이 종료로 Ops를 승격하거나 Dev를 롤백하지 않습니다.')
            events.append((at, 'closed:' + str(number) + ':' + pr['closed_at'], '\n'.join(lines)))
    return events


def check(state_path, now=None):
    path = Path(state_path)
    started = now or datetime.now(timezone.utc)
    if path.exists():
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('schema') != 1 or state.get('repository') != github.REPOSITORY or not isinstance(state.get('delivered'), list):
            raise ValueError('Invalid notification state; restore the last archived state before retrying')
    else:
        state = {'schema': 1, 'repository': github.REPOSITORY, 'started_at': started.isoformat(),
                 'checked_at': started.isoformat(), 'delivered': []}
        save(path, state)
    since = timestamp(state['started_at']) - WINDOW
    cutoff = timestamp(state['checked_at']) - WINDOW
    seen = set(state['delivered'])
    pulls = {}
    # Always inspect open PRs: approval detection must not depend on whether
    # GitHub changed the PR's updated_at timestamp for a submitted review.
    for item in github.pages(ROOT + '/pulls?state=open&base=dev'):
        if source_pr(item):
            pulls[int(item['number'])] = item
    for item in github.pages(ROOT + '/pulls?state=closed&base=dev&sort=updated&direction=desc'):
        if timestamp(item['updated_at']) < cutoff:
            break
        if source_pr(item):
            pulls[int(item['number'])] = item
    events = []
    for number in pulls:
        pr = github.api('GET', ROOT + '/pulls/' + str(number))
        if source_pr(pr):
            events.extend(decisions(pr, since))
    delivered = 0
    for _, key, text in sorted(events):
        if key in seen:
            continue
        if not notify_ci.send_text(text):
            raise RuntimeError('Slack delivery failed; notification cursor preserved for retry')
        seen.add(key)
        state['delivered'].append(key)
        save(path, state)
        delivered += 1
    state['checked_at'] = started.isoformat()
    save(path, state)
    print('PR notification check completed; sent=' + str(delivered))
    return delivered


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='.pr-notifications/state.json')
    args = parser.parse_args()
    try:
        check(args.state)
    except Exception as error:
        print('PR notification check failed: ' + error.__class__.__name__
              + '; check GitHub/Slack credentials and connectivity. Cursor retained; no API body or credential printed.')
        raise SystemExit(1)
