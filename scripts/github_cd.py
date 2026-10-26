"""Source PR creation/status and an independently reviewed dev-merge gate.

The token is read from the environment, never command arguments or artifacts.
This module never approves or merges pull requests.
"""
import argparse
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

import env_cd as cd

REPOSITORY = 'raspberry-2jo/chargeops'
CONTEXT = 'chargeops/dev-integration'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api(method, path, data=None):
    if not path.startswith('/repos/' + REPOSITORY + '/'):
        raise cd.DeliveryPreflightError('GitHub requests must target the ChargeOps app repository')
    token = os.environ.get('GITHUB_PR_TOKEN', '')
    if not token:
        raise cd.DeliveryPreflightError('Missing GitHub PR credential')
    request = Request('https://api.github.com' + path, method=method,
                      data=None if data is None else json.dumps(data).encode(), headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        # The merge-provenance gate requires merge_commit_sha, absent in the
        # 2026-03-10 PR response. Pin the supported contract that provides it.
        'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise cd.DeliveryPreflightError('GitHub request failed: HTTP ' + str(error.code)
                                       + '; check token permissions, organization approval and repository access') from None
    except URLError:
        raise cd.DeliveryPreflightError('GitHub connection failed; check the agent network and CA') from None


def sha(value):
    if not re.fullmatch(r'[a-f0-9]{40}', value or ''):
        raise cd.DeliveryPreflightError('A full source commit is required')
    return value


def pages(path):
    for page in range(1, 101):
        items = api('GET', path + ('&' if '?' in path else '?') + urlencode({'per_page': 100, 'page': page}))
        if not isinstance(items, list):
            raise cd.DeliveryPreflightError('Unexpected GitHub list response')
        yield from items
        if len(items) < 100:
            return
    raise cd.DeliveryPreflightError('GitHub pagination exceeded the verification bound')


def approved_merge(source):
    """Reject direct pushes, stale heads, self/bot reviews and old approvals."""
    sha(source)
    root = '/repos/' + REPOSITORY
    ref = api('GET', root + '/git/ref/heads/dev')
    if ref.get('object', {}).get('sha') != source:
        raise cd.DeliveryPreflightError('This build is no longer the current dev head')
    associated = list(pages(root + '/commits/' + source + '/pulls'))
    for association in associated:
        pr = api('GET', root + '/pulls/' + str(int(association['number'])))
        if (not pr.get('merged') or not pr.get('merged_at') or pr.get('merge_commit_sha') != source
                or pr.get('base', {}).get('ref') != 'dev'
                or pr.get('base', {}).get('repo', {}).get('full_name') != REPOSITORY
                or pr.get('head', {}).get('repo', {}).get('full_name') != REPOSITORY):
            continue
        latest = {}
        for review in pages(root + '/pulls/' + str(pr['number']) + '/reviews'):
            user = review.get('user') or {}
            if user.get('type') != 'User' or user.get('login') == pr.get('user', {}).get('login'):
                continue
            if review.get('state') in ['APPROVED', 'CHANGES_REQUESTED', 'DISMISSED']:
                if review.get('id', 0) > latest.get(user.get('login'), {}).get('id', -1):
                    latest[user['login']] = review
        approvers = sorted(name for name, review in latest.items()
                           if review.get('state') == 'APPROVED' and review.get('commit_id') == pr['head']['sha'])
        approvers = [name for name in approvers if api('GET', root + '/collaborators/' + quote(name, safe='')
                                                      + '/permission').get('permission') in ['write', 'maintain', 'admin']]
        if approvers and not any(r.get('state') == 'CHANGES_REQUESTED' for r in latest.values()):
            return {'source': source, 'number': pr['number'], 'head': pr['head']['sha'],
                    'url': pr['html_url'], 'approvers': approvers}
    raise cd.DeliveryPreflightError('dev must be merged through a PR approved by another person on its latest head')


def status(source, state, context=CONTEXT):
    sha(source)
    if state not in ['pending', 'success', 'failure', 'error']:
        raise cd.DeliveryPreflightError('Invalid GitHub status')
    if context not in [CONTEXT, 'chargeops/ci']:
        raise cd.DeliveryPreflightError('Invalid GitHub status context')
    data = {'state': state, 'context': context,
            'description': {'pending': 'Dev deployment verification running', 'success': 'Exact Dev digest verified',
                            'failure': 'Dev verification failed', 'error': 'Dev verification unavailable'}[state]}
    if context == 'chargeops/ci':
        data['description'] = 'ChargeOps pipeline ' + state
    url = os.environ.get('BUILD_URL', '')
    if url.startswith(('http://', 'https://')):
        data['target_url'] = url
    api('POST', '/repos/' + REPOSITORY + '/statuses/' + source, data)


def open_pr(repo, candidate_path, receipt_path, output):
    candidate, receipt = cd.load(candidate_path), cd.load(receipt_path)
    cd.validate_receipt(candidate, receipt, time.time())
    if os.environ.get('CHECKOUT_BRANCH') != 'kim_chanju' or os.environ.get('CHANGE_ID'):
        raise cd.DeliveryPreflightError('Only the trusted kim_chanju branch may create the source PR')
    cd.ensure_clean(repo)
    if (cd.fingerprint(cd.rendered(repo, cd.DEV)) != candidate['development_fingerprint']
            or not cd.live_candidate(candidate['image'])):
        raise cd.DeliveryPreflightError('Dev no longer runs the verified PR candidate')
    return source_pr(candidate['source'], output,
        'Dev integration checks passed for source `' + candidate['source'] + '`.\n\n'
        'Verified CSMS image: `' + candidate['image'] + '`.\n\n'
        'Review the full source diff. Merge into dev authorizes the dev pipeline to rebuild and '
        'verify the merged commit, then promote the same verified digest to ops. '
        'Production has not been changed by this preview build.', candidate['image'])


def source_pr(source, output, body, image=None):
    sha(source)
    if os.environ.get('CHECKOUT_BRANCH') != 'kim_chanju' or os.environ.get('CHANGE_ID'):
        raise cd.DeliveryPreflightError('Only the trusted kim_chanju branch may create the source PR')
    head = api('GET', '/repos/' + REPOSITORY + '/git/ref/heads/kim_chanju')
    if head.get('object', {}).get('sha') != source:
        raise cd.DeliveryPreflightError('The source branch advanced; verify the new head before opening its PR')
    root = '/repos/' + REPOSITORY + '/pulls'
    existing = list(pages(root + '?' + urlencode({'state': 'open', 'base': 'dev', 'head': 'raspberry-2jo:kim_chanju'})))
    if len(existing) > 1:
        raise cd.DeliveryPreflightError('Multiple source PRs match; inspect them before continuing')
    if existing:
        pr = existing[0]
        if pr.get('head', {}).get('sha') != source:
            raise cd.DeliveryPreflightError('Open PR head does not match the verified source')
        pr = api('PATCH', root + '/' + str(int(pr['number'])), {'body': body})
    else:
        pr = api('POST', root, {'title': 'chore(release): promote kim_chanju changes to dev',
            'head': 'kim_chanju', 'base': 'dev', 'body': body})
    if pr.get('head', {}).get('sha') != source:
        raise cd.DeliveryPreflightError('PR head changed during creation; its new head must pass Dev checks')
    result = {'number': pr['number'], 'url': pr['html_url'], 'source': source}
    if image:
        result['image'] = image
    cd.save(output, result)
    print('Source PR ready: ' + result['url'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate-merge', 'status', 'open-pr', 'open-ci-pr'])
    parser.add_argument('--source', default='')
    parser.add_argument('--state', default='pending')
    parser.add_argument('--context', default=CONTEXT)
    parser.add_argument('--repo', default='.gitops-delivery')
    parser.add_argument('--candidate', default='artifacts/candidate.json')
    parser.add_argument('--receipt', default='artifacts/verification.json')
    parser.add_argument('--output', default='artifacts/pull-request.json')
    args = parser.parse_args()
    if args.command == 'validate-merge':
        result = approved_merge(args.source)
        cd.save('artifacts/source-approval.json', result)
        print('Approved dev merge verified: PR #' + str(result['number']))
    elif args.command == 'status':
        status(args.source, args.state, args.context)
    elif args.command == 'open-ci-pr':
        source_pr(args.source, args.output, 'CI completed for source `' + sha(args.source)
                  + '`. CSMS was not selected, so no Dev CSMS or production image was changed. '
                  'Review the Jenkins checks and full diff before merging into dev.')
    else:
        open_pr(args.repo, args.candidate, args.receipt, args.output)


if __name__ == '__main__':
    try:
        main()
    except cd.DeliveryPreflightError as error:
        print('GitHub delivery stopped: ' + str(error))
        raise SystemExit(1)
    except Exception as error:
        print('GitHub delivery stopped: ' + error.__class__.__name__ + '; no API payload or credential printed')
        raise SystemExit(1)
