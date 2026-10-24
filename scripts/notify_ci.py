"""Best-effort Slack lifecycle notifications; never include console output."""
import json
import os
from pathlib import Path
import re
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def message(environ, directory=Path('artifacts')):
    event = environ.get('NOTIFY_EVENT', 'CI/CD status')
    allowed = {'CI started', 'Dev verification passed', 'Source PR ready', 'Ops Canary started',
               'Pipeline succeeded', 'Pipeline failed', 'Pipeline aborted', 'Recovery completed', 'Recovery failed'}
    if event not in allowed:
        event = 'CI/CD status'
    branch = environ.get('CHECKOUT_BRANCH', '')
    branch = branch if branch in ['kim_chanju', 'dev'] else 'other'
    tag = environ.get('IMAGE_TAG', '')
    tag = tag if re.fullmatch(r'build-(?:(?:kim_chanju|dev)-)?\d+-[a-f0-9]{12}', tag) else '(not built)'
    lines = ['[ChargeOps] ' + event, 'branch: ' + branch + ' | image tag: ' + tag]
    if event == 'CI started':
        directory = directory / 'no-previous-build-artifacts'
    for filename, fields in [('candidate.json', ['image']), ('production-delivery.json', ['phase', 'previous_image'])]:
        path = directory / filename
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            for field in fields:
                value = str(data.get(field, ''))
                if field == 'phase' and value in ['prepared', 'pending', 'recovering', 'recovered', 'succeeded']:
                    lines.append('delivery: ' + value)
                elif re.fullmatch(r'registry\.chargeops\.lab:5000/chargeops/chargeops-csms@sha256:[a-f0-9]{64}', value):
                    lines.append(field + ': ' + value)
    pr = directory / 'pull-request.json'
    if pr.exists():
        url = json.loads(pr.read_text(encoding='utf-8')).get('url', '')
        if re.fullmatch(r'https://github\.com/raspberry-2jo/chargeops/pull/\d+', url):
            lines.append('PR: ' + url)
    url = environ.get('BUILD_URL', '')
    if url.startswith(('http://', 'https://')) and not any(c in url for c in ['\n', '\r', '<', '>', '&']):
        lines.append('Jenkins: ' + url)
    return '\n'.join(lines)


def send(environ=os.environ):
    token = environ.get('SLACK_CICD_TOKEN', '')
    channel = environ.get('SLACK_CICD_CHANNEL', 'C0CBS4DFPGW')
    if not token or not re.fullmatch(r'[CG][A-Z0-9]+', channel):
        print('Slack notification unavailable: check credential and channel configuration')
        return False
    payload = {'channel': channel, 'text': message(environ), 'mrkdwn': False,
               'unfurl_links': False, 'unfurl_media': False}
    request = Request('https://slack.com/api/chat.postMessage', method='POST',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + token,
                                                    'Content-Type': 'application/json; charset=utf-8'})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        if json.load(response).get('ok') is not True:
            print('Slack notification failed; check chat:write permission and channel membership')
            return False
    print('Slack notification sent')
    return True


if __name__ == '__main__':
    try:
        send()
    except Exception:
        # Alerts must not turn a healthy delivery into a rollback.
        print('Slack notification unavailable; no credential or response body printed')
