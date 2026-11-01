"""Exercise the actual pipeline build shell: overlap, bounds and failure barrier."""
import os
from pathlib import Path
import subprocess

import pytest

from test_ci import BUILD, IMAGES, bash


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_build_overlap_is_bounded_and_all_children_finish(bash, tmp_path, limit):
    result, events = execute(bash, tmp_path, limit)
    assert result.returncode == 0, result.stderr
    active = peak = 0
    for event in events:
        active += 1 if event.startswith('start ') else -1
        peak = max(peak, active)
        assert 0 <= active <= limit
    assert active == 0 and peak == limit
    assert len(events) == 12


def execute(bash, tmp_path, limit, fail=False):
    log = tmp_path / 'events'
    env = dict(os.environ, BUILD_IMAGES=' '.join(i.name for i in IMAGES),
               IMAGE_BUILD_PARALLELISM=str(limit), PUBLISH_IMAGES='false',
               REGISTRY='registry.test/apps', IMAGE_TAG='test',
               EVENT_LOG=log.as_posix(), FAIL_BUILD='1' if fail else '0')
    stub = '''docker() {
      printf 'start %s\n' "$4" >> "$EVENT_LOG"
      sleep 0.3
      printf 'end %s\n' "$4" >> "$EVENT_LOG"
      if [ "$FAIL_BUILD" = 1 ] && [ "$4" = apps/ai-gateway/Dockerfile ]; then return 31; fi
    }
'''
    result = subprocess.run([bash], input=stub + BUILD, cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=30)
    return result, log.read_text().splitlines() if log.exists() else []


def test_failed_batch_waits_for_sibling_and_stops_later_builds(bash, tmp_path):
    result, events = execute(bash, tmp_path, 2, fail=True)
    assert result.returncode == 31
    assert len(events) == 4
    assert sum(e.startswith('end ') for e in events) == 2


def test_invalid_concurrency_does_not_start_docker(bash, tmp_path):
    result, events = execute(bash, tmp_path, 'unlimited')
    assert result.returncode != 0 and not events
