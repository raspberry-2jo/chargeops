"""Resolve a verified image-comparison commit; uncertainty always selects all images."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True,
                          timeout=45, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})


def api_base(job_url, remote, username='', token=''):
    parsed = urlsplit(job_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('invalid_job_url')
    tree = 'result,actions[_class,remoteUrls,lastBuiltRevision[SHA1]]'
    url = job_url.rstrip('/') + '/lastSuccessfulBuild/api/json?' + urlencode({'tree': tree})
    headers = {'Accept': 'application/json'}
    if username and token:
        headers['Authorization'] = 'Basic ' + base64.b64encode((username + ':' + token).encode()).decode()
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=15) as response:
        data = json.load(response)
    if data.get('result') != 'SUCCESS':
        raise ValueError('api_build_not_successful')
    # A delivery job also checks out GitOps. Never compare against its SHA.
    actions = [a for a in data.get('actions', []) if isinstance(a, dict)
               and isinstance(a.get('lastBuiltRevision'), dict)]
    matching = [a for a in actions if remote in (a.get('remoteUrls') or [])]
    if not matching:
        raise ValueError('api_source_repository_not_found')
    commits = {a['lastBuiltRevision'].get('SHA1') for a in matching}
    if len(commits) != 1:
        raise ValueError('api_source_revision_ambiguous')
    return commits.pop()


def resolve(root, checkout_base='', job_url='', username='', token=''):
    result = {'base': '', 'source': 'none', 'candidate': '', 'fetch': 'not_needed', 'reason': ''}
    candidate = checkout_base.strip()
    if candidate:
        result['source'] = 'checkout_vars'
    elif job_url:
        try:
            remote_result = git(root, 'config', '--get', 'remote.origin.url')
            if remote_result.returncode or not remote_result.stdout.strip():
                raise ValueError('source_remote_unavailable')
            candidate = api_base(job_url, remote_result.stdout.strip(), username, token)
            result['source'] = 'jenkins_api'
        except HTTPError as error:
            result['reason'] = 'api_http_' + str(error.code)
        except (URLError, OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            result['reason'] = 'api_unavailable_or_invalid'
    if not candidate:
        result['reason'] = result['reason'] or 'no_previous_successful_commit'
        return result
    # Reject revisions/options supplied by malformed API or checkout metadata.
    if not isinstance(candidate, str) or not re.fullmatch(r'[a-fA-F0-9]{40}', candidate):
        result['reason'] = 'invalid_commit_sha'
        return result
    candidate = candidate.lower()
    result['candidate'] = candidate
    try:
        if git(root, 'cat-file', '-e', candidate + '^{commit}').returncode:
            result['fetch'] = 'attempted'
            # Fetch only the required object; do not move branches or checkout.
            git(root, 'fetch', '--no-tags', 'origin', candidate)
            if git(root, 'cat-file', '-e', candidate + '^{commit}').returncode:
                result['reason'] = 'commit_unavailable_after_fetch'
                return result
            result['fetch'] = 'fetched'
        result['base'] = candidate
        return result
    except (OSError, subprocess.SubprocessError):
        result['reason'] = 'git_verification_or_fetch_failed'
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = resolve(Path.cwd(), os.environ.get('CHECKOUT_PREVIOUS_SUCCESSFUL_COMMIT', ''),
                     os.environ.get('JOB_URL', ''), os.environ.get('JENKINS_API_USER', ''),
                     os.environ.get('JENKINS_API_TOKEN', ''))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('Image comparison base=' + (result['base'] or '(none)') + ' source=' + result['source']
          + ' fetch=' + result['fetch'] + ' reason=' + (result['reason'] or 'verified'))
    if not result['base']:
        print('Image comparison base unavailable; building all images')


if __name__ == '__main__':
    main()
