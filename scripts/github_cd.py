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


class GitHubRequestError(cd.DeliveryPreflightError):
    """Structured, sanitized failure; never retain the response body or token."""
    def __init__(self, status_code, reason_code='validation', fields=()):
        self.status_code, self.reason_code = status_code, reason_code
        reasons = {
            'no_changes': 'no new commits between the source and base branches',
            'duplicate_pr': 'a source pull request already exists; recheck the open PR',
            'invalid_head': 'invalid head branch; check kim_chanju exists in the app repository',
            'invalid_base': 'invalid base branch; check dev exists in the app repository',
            'validation': 'request validation failed; check branch differences and PR fields',
            'credential': 'credential rejected; check the Jenkins GitHub token',
            'forbidden': 'access forbidden; check token permissions and organization approval',
            'not_found': 'resource unavailable; check repository, branch and credential access',
            'rate_limit': 'GitHub rate limit exceeded; wait for the limit to reset',
            'conflict': 'repository state conflict; refresh branch and PR state',
            'service': 'GitHub service unavailable; retry after checking GitHub availability',
            'unknown': 'request rejected; inspect the HTTP status and repository state',
        }
        message = 'GitHub request failed: HTTP ' + str(status_code) + '; ' + reasons.get(reason_code, reasons['unknown'])
        if fields:
            message += '; validation fields: ' + ', '.join(fields)
        super().__init__(message)


def github_http_failure(error):
    """Classify bounded JSON into fixed messages, never log arbitrary API text."""
    try:
        raw = error.read(16385)
        document = json.loads(raw) if len(raw) <= 16384 else {}
    except Exception:
        document = {}
    finally:
        error.close()
    if not isinstance(document, dict):
        document = {}
    errors = document.get('errors', [])
    errors = errors[:16] if isinstance(errors, list) else []
    messages = [document.get('message', '')] + [
        e.get('message', '') if isinstance(e, dict) else e for e in errors]
    messages = ' '.join(m.lower() for m in messages if isinstance(m, str))
    code = error.code
    reason = {401:'credential', 403:'forbidden', 404:'not_found', 409:'conflict',
              422:'validation', 429:'rate_limit'}.get(code, 'service' if code >= 500 else 'unknown')
    if code in (403, 429) and ('rate limit' in messages or 'secondary rate' in messages):
        reason = 'rate_limit'
    if code == 422:
        if 'no commits between' in messages:
            reason = 'no_changes'
        elif 'pull request already exists' in messages:
            reason = 'duplicate_pr'
        elif any(s in messages for s in ["head sha can't be blank", 'head ref must be a branch',
                                         'head branch is invalid', 'head is not a valid ref']):
            reason = 'invalid_head'
        elif 'base branch is invalid' in messages or 'base is not a valid ref' in messages:
            reason = 'invalid_base'
    fields = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        # Only known schema metadata is printable; ignore value/message/unknown strings.
        resource, field, validation = (item.get(k) for k in ('resource','field','code'))
        if (resource in ('PullRequest','Issue','Commit','Reference','Repository')
                and field in ('head','base','title','body','state','sha','ref','description')
                and validation in ('missing','missing_field','invalid','already_exists','custom','unprocessable')):
            fields.append(resource + '.' + field + '.' + validation)
    return GitHubRequestError(code, reason, sorted(set(fields)))


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
        raise github_http_failure(error) from None
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


def approved_merge(source, allow_ancestor=False):
    """Reject direct pushes, stale heads, self/bot reviews and old approvals."""
    sha(source)
    root = '/repos/' + REPOSITORY
    ref = api('GET', root + '/git/ref/heads/dev')
    current = ref.get('object', {}).get('sha')
    if current != source:
        if not allow_ancestor:
            raise cd.DeliveryPreflightError('This build is no longer the current dev head')
        # Retry only a reviewed merge still contained in dev with identical apps.
        comparison = api('GET', root + '/compare/' + source + '...' + sha(current))
        if (comparison.get('status') not in ('ahead', 'identical')
                or comparison.get('behind_by') != 0 or apps_tree(source) != apps_tree(current)):
            raise cd.DeliveryPreflightError('dev changed its apps context after the reviewed merge; rebuild and reverify the current source')
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


def apps_tree(source):
    document = api('GET', '/repos/' + REPOSITORY + '/git/trees/' + sha(source))
    apps = [item['sha'] for item in document.get('tree', [])
            if item.get('path') == 'apps' and item.get('type') == 'tree']
    if len(apps) != 1:
        raise cd.DeliveryPreflightError('Cannot bind the merged apps build context to the verified image')
    return apps[0]


def approved_candidate(candidate, pr_path='artifacts/pull-request.json'):
    """Revalidate the exact PR, its merge/reviews and its image build context."""
    if not cd.Path(pr_path).is_file():
        raise cd.DeliveryPreflightError('A reviewed source PR receipt is required before Ops promotion')
    saved = cd.load(pr_path)
    if saved.get('source') != candidate['source'] or saved.get('image') != candidate['image']:
        raise cd.DeliveryPreflightError('Source PR does not belong to the verified candidate')
    number = int(saved['number'])
    pr = api('GET', '/repos/' + REPOSITORY + '/pulls/' + str(number))
    if not pr.get('merged') or pr.get('head', {}).get('sha') != candidate['source']:
        raise cd.DeliveryPreflightError('Verified source PR must be merged without changing its head')
    if saved.get('reused_merged_pr') is True:
        result = approved_merge(sha(pr.get('merge_commit_sha', '')), allow_ancestor=True)
    else:
        result = approved_merge(sha(pr.get('merge_commit_sha', '')))
    if result['number'] != number or result['head'] != candidate['source']:
        raise cd.DeliveryPreflightError('Merged PR is not the verified source PR')
    if apps_tree(candidate['source']) != apps_tree(result['source']):
        raise cd.DeliveryPreflightError('Merge changed the apps build context; rebuild and reverify before Ops')
    result.update(candidate_source=candidate['source'], image=candidate['image'])
    return result


def close_expired_pr(candidate, saved):
    """Close only the still-open PR owned by this preview build."""
    build = os.environ.get('BUILD_URL', '')
    if not build or saved.get('build_url') != build:
        return 'SKIP: PR has no matching build ownership receipt'
    path = '/repos/' + REPOSITORY + '/pulls/' + str(int(saved['number']))
    pr = api('GET', path)
    if pr.get('merged') or pr.get('state') != 'open':
        return 'SKIP: PR already merged or closed'
    head, base = pr.get('head', {}), pr.get('base', {})
    if (saved.get('source') != candidate['source'] or saved.get('image') != candidate['image']
            or head.get('sha') != candidate['source']
            or head.get('ref') != 'kim_chanju' or base.get('ref') != 'dev'
            or head.get('repo', {}).get('full_name') != REPOSITORY
            or base.get('repo', {}).get('full_name') != REPOSITORY
            or '<!-- chargeops-preview-build: ' + build + ' -->' not in (pr.get('body') or '')):
        return 'SKIP: PR source, branches or build ownership changed'
    result = api('PATCH', path, {'state': 'closed'})
    if result.get('merged'):
        return 'SKIP: PR merged while timeout cleanup was running'
    if result.get('state') != 'closed':
        raise cd.DeliveryPreflightError('GitHub did not confirm PR closure')
    return 'CLOSED: source PR #' + str(saved['number']) + ' after approval/merge timeout'


def wait_for_merge(candidate_path, receipt_path, pr_path, output, timeout=2700, interval=15):
    if os.environ.get('CHECKOUT_BRANCH') != 'kim_chanju' or os.environ.get('CHANGE_ID'):
        raise cd.DeliveryPreflightError('Only the original trusted preview build may wait for source approval')
    candidate, receipt, saved = cd.load(candidate_path), cd.load(receipt_path), cd.load(pr_path)
    cd.validate_receipt(candidate, receipt, time.time())
    if saved.get('source') != candidate['source'] or saved.get('image') != candidate['image']:
        raise cd.DeliveryPreflightError('Source PR does not belong to this Dev verification')
    deadline = time.monotonic() + timeout
    print('Waiting for source PR #' + str(saved['number']) + ' approval AND merge; Ops unchanged', flush=True)
    while time.monotonic() < deadline:
        cd.validate_receipt(candidate, receipt, time.time())
        pr = api('GET', '/repos/' + REPOSITORY + '/pulls/' + str(int(saved['number'])))
        if pr.get('head', {}).get('sha') != candidate['source']:
            raise cd.DeliveryPreflightError('PR head changed while waiting; verify the new source')
        if pr.get('merged'):
            approval = approved_candidate(candidate, pr_path)
            cd.save(output, approval)
            print('Source PR approved and merged; same verified image may continue to Ops', flush=True)
            return approval
        if pr.get('state') == 'closed':
            raise cd.DeliveryPreflightError('Source PR closed without merge; Ops unchanged')
        time.sleep(interval)
    try:
        print(close_expired_pr(candidate, saved), flush=True)
    except Exception as error:
        print('Unable to close timed-out PR: ' + type(error).__name__ + '; inspect GitHub permissions', flush=True)
    raise cd.DeliveryPreflightError('Source approval/merge wait timed out; Ops unchanged')


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
        'Review the full source diff. Approval and merge resume this same Jenkins build. '
        'It rechecks reviews and the merged apps tree, then promotes this exact Dev-verified digest to ops. '
        'Production has not been changed by this preview build.', candidate['image'])


def reusable_merged_pr(source):
    """Find a real reviewed merged PR for an unchanged source, never invent approval."""
    root = '/repos/' + REPOSITORY
    current = sha(api('GET', root + '/git/ref/heads/dev').get('object', {}).get('sha'))
    if apps_tree(source) != apps_tree(current):
        raise cd.DeliveryPreflightError('dev already contains this source but its apps context changed; sync the current dev source and reverify before retrying Ops')
    for associated in pages(root + '/commits/' + sha(source) + '/pulls'):
        pr = api('GET', root + '/pulls/' + str(int(associated['number'])))
        head, base = pr.get('head', {}), pr.get('base', {})
        if (not pr.get('merged') or not pr.get('merged_at') or head.get('sha') != source
                or head.get('ref') != 'kim_chanju' or base.get('ref') != 'dev'
                or head.get('repo', {}).get('full_name') != REPOSITORY
                or base.get('repo', {}).get('full_name') != REPOSITORY):
            continue
        approval = approved_merge(sha(pr.get('merge_commit_sha', '')), allow_ancestor=True)
        if (approval['number'] == pr['number'] and approval['head'] == source
                and apps_tree(source) == apps_tree(approval['source'])):
            return pr
    raise cd.DeliveryPreflightError('dev already contains this source, but no independently approved merged PR matches its exact head; Ops retry is not authorized')


def source_pr(source, output, body, image=None):
    sha(source)
    if os.environ.get('CHECKOUT_BRANCH') != 'kim_chanju' or os.environ.get('CHANGE_ID'):
        raise cd.DeliveryPreflightError('Only the trusted kim_chanju branch may create the source PR')
    head = api('GET', '/repos/' + REPOSITORY + '/git/ref/heads/kim_chanju')
    if head.get('object', {}).get('sha') != source:
        raise cd.DeliveryPreflightError('The source branch advanced; verify the new head before opening its PR')
    build = os.environ.get('BUILD_URL', '')
    if re.fullmatch(r'https?://[^\s<>]+', build):
        body += '\n\n<!-- chargeops-preview-build: ' + build + ' -->'
    else:
        build = ''
    root = '/repos/' + REPOSITORY + '/pulls'
    reused_merged_pr = False
    listing = root + '?' + urlencode({'state': 'open', 'base': 'dev', 'head': 'raspberry-2jo:kim_chanju'})
    existing = list(pages(listing))
    if len(existing) > 1:
        raise cd.DeliveryPreflightError('Multiple source PRs match; inspect them before continuing')
    if existing:
        pr = existing[0]
        if pr.get('head', {}).get('sha') != source:
            raise cd.DeliveryPreflightError('Open PR head does not match the verified source')
        pr = api('PATCH', root + '/' + str(int(pr['number'])), {'body': body})
    else:
        try:
            pr = api('POST', root, {'title': 'chore(release): promote kim_chanju changes to dev',
                'head': 'kim_chanju', 'base': 'dev', 'body': body})
        except GitHubRequestError as error:
            if error.status_code != 422:
                raise
            # Another actor can create the PR between listing and POST. Recheck once;
            # never POST repeatedly or attach a receipt to a different source head.
            raced = list(pages(listing))
            if len(raced) > 1:
                raise cd.DeliveryPreflightError('Multiple source PRs match after HTTP 422; inspect them') from None
            if raced:
                if raced[0].get('head', {}).get('sha') != source:
                    raise cd.DeliveryPreflightError('Open PR head changed during creation; reverify the new source') from None
                pr = api('PATCH', root + '/' + str(int(raced[0]['number'])), {'body': body})
            else:
                try:
                    comparison = api('GET', '/repos/' + REPOSITORY + '/compare/dev...' + source)
                except cd.DeliveryPreflightError:
                    raise error from None
                no_changes = (isinstance(comparison, dict) and comparison.get('ahead_by') == 0
                              and comparison.get('status') in ('identical', 'behind'))
                if no_changes or error.reason_code == 'no_changes':
                    pr = reusable_merged_pr(source)
                    reused_merged_pr = True
                else:
                    raise error from None
    if pr.get('head', {}).get('sha') != source:
        raise cd.DeliveryPreflightError('PR head changed during creation; its new head must pass Dev checks')
    result = {'number': pr['number'], 'url': pr['html_url'], 'source': source}
    if reused_merged_pr:
        result['reused_merged_pr'] = True
    if build:
        result['build_url'] = build
    if image:
        result['image'] = image
    cd.save(output, result)
    print(('Reviewed merged PR reused for retry: ' if reused_merged_pr else 'Source PR ready: ') + result['url'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate-merge', 'wait-merge', 'status', 'open-pr', 'open-ci-pr'])
    parser.add_argument('--source', default='')
    parser.add_argument('--state', default='pending')
    parser.add_argument('--context', default=CONTEXT)
    parser.add_argument('--repo', default='.gitops-delivery')
    parser.add_argument('--candidate', default='artifacts/candidate.json')
    parser.add_argument('--receipt', default='artifacts/verification.json')
    parser.add_argument('--output', default='artifacts/pull-request.json')
    parser.add_argument('--timeout', type=int, default=2700)
    args = parser.parse_args()
    if args.command == 'validate-merge':
        result = approved_merge(args.source)
        cd.save('artifacts/source-approval.json', result)
        print('Approved dev merge verified: PR #' + str(result['number']))
    elif args.command == 'wait-merge':
        wait_for_merge(args.candidate, args.receipt, args.output, 'artifacts/source-approval.json', args.timeout)
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
