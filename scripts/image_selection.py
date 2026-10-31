"""Select local Docker inputs from the previous successful Git commit; fail open to all builds."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess

from ci import ROOT, image_definitions


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def copy_inputs(root, image):
    context = (root / image.context).resolve()
    dockerfile = root / image.dockerfile
    if not context.is_relative_to(root) or not dockerfile.resolve().is_relative_to(root):
        raise ValueError('build input outside repository')
    dependencies = []
    text = re.sub(r'\\\r?\n\s*', ' ', dockerfile.read_text(encoding='utf-8'))
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        fields = line.split(None, 1)
        instruction, value = fields[0], fields[1] if len(fields) > 1 else ''
        if instruction.upper() == 'ONBUILD':
            raise ValueError('ONBUILD dependencies require review')
        if instruction.upper() == 'FROM' and '$' in value:
            raise ValueError('dynamic FROM')
        if instruction.upper() == 'RUN' and '--mount' in value:
            raise ValueError('RUN mount dependencies require review')
        if instruction.upper() not in ('COPY', 'ADD'):
            continue
        from_stage = False
        while value.startswith('--'):
            fields = value.split(None, 1)
            flag = fields[0]
            if len(fields) != 2 or '=' not in flag and flag != '--link':
                raise ValueError('unsupported COPY flag')
            value = fields[1]
            from_stage |= flag.startswith('--from=')
            value = value.lstrip()
        if from_stage:
            continue  # Inputs to the source stage are processed separately.
        parts = json.loads(value) if value.startswith('[') else shlex.split(value)
        if not isinstance(parts, list) or len(parts) < 2 or not all(isinstance(p, str) for p in parts):
            raise ValueError('unparseable COPY')
        for source in parts[:-1]:
            if '$' in source or '://' in source or PurePosixPath(source).is_absolute() or '..' in PurePosixPath(source).parts:
                raise ValueError('non-local COPY')
            source = source.removeprefix('./')
            if source in ('', '.'):
                dependencies.append((image.context.rstrip('/') + '/', True))
                continue
            if any(c in source for c in '*?['):
                # Docker's glob/directory/symlink semantics need a separate parser.
                raise ValueError('glob COPY dependencies require review')
            source = PurePosixPath(source).as_posix()
            path = (context / source).resolve()
            if not path.is_relative_to(context) or not path.exists():
                raise ValueError('missing/outside COPY input')
            if path != context / source or path.is_dir() and any(p.is_symlink() for p in path.rglob('*')):
                raise ValueError('symlink COPY dependency requires review')
            directory = path.is_dir()
            dependencies.append((image.context.rstrip('/') + '/' + source.rstrip('/') + ('/' if directory else ''), directory))
    if not dependencies:
        raise ValueError('no auditable local COPY inputs')
    return dependencies


def select(root=ROOT, base='', force_all=False):
    root = Path(root).resolve()
    images = image_definitions(root)
    reasons = {image.name: [] for image in images}
    result = {'base': None, 'head': git(root, 'rev-parse', 'HEAD').decode().strip(), 'reasons': reasons}
    def all_images(reason):
        result.update(selected=[image.name for image in images], fallback=reason)
        for image in images:
            reasons[image.name] = [reason]
        return result
    if force_all:
        return all_images('BUILD_ALL_IMAGES=true')
    try:
        if not base:
            return all_images('no previous successful commit')
        base = git(root, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}').decode().strip()
        result['base'] = base
        # Include deletions and both rename sides, without path or extension exclusions.
        changed = [p.decode('utf-8') for p in git(root, 'diff', '--name-only', '--no-renames', '-z', base, 'HEAD', '--').split(b'\0') if p]
        result['changed_files'] = changed
        previous_pipeline = git(root, 'show', base + ':Jenkinsfile').decode('utf-8')
        declarations = lambda text: re.findall(r'^\s*build_if_selected (\S+) (\S+) (\S+)\s*$', text, re.M)
        if declarations(previous_pipeline) != declarations((root / 'Jenkinsfile').read_text(encoding='utf-8')):
            return all_images('image build declarations changed')
        dependencies = {image.name: copy_inputs(root, image) for image in images}
        for image in images:
            controls = {image.dockerfile, image.context.rstrip('/') + '/.dockerignore', image.dockerfile + '.dockerignore'}
            for path in changed:
                matches = path in controls
                for source, directory in dependencies[image.name]:
                    matches |= path.startswith(source) if directory else path == source
                if matches:
                    reasons[image.name].append(path)
        result['selected'] = [image.name for image in images if reasons[image.name]]
        return result
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeError):
        return all_images('uncertain Git diff or Dockerfile dependencies; building all')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='')
    parser.add_argument('--force-all', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = select(base=args.base, force_all=args.force_all)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('Image comparison: ' + str(result['base']) + '..' + result['head'])
    for name, files in result['reasons'].items():
        print(name + ': ' + (', '.join(files) if files else 'not selected (no Docker input changed)'))
    print('Selected images: ' + (' '.join(result['selected']) or '(none)'))


if __name__ == '__main__':
    main()
