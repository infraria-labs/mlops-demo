"""Update only image tags in Git desired state; never read or mutate a cluster."""
import argparse
from pathlib import Path
import re

import yaml


def update(path: Path, sha: str) -> bool:
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Release requires a full Git SHA')
    original = path.read_text()
    values = yaml.safe_load(original)
    for target in ('inference', 'training'):
        image = values['images'][target]
        if not image['repository'] or not re.fullmatch(r'[0-9a-f]{40}', str(image['tag'])):
            raise ValueError('Desired state requires repositories and full Git SHA tags')
    # Preserve comments, model choice and every other byte of configuration.
    changed, count = re.subn(r'(?m)^(    tag: )[0-9a-f]{40}$',
                            lambda match: match[1] + sha, original)
    if count != 2:
        raise ValueError('Expected exactly two image tags')
    desired = yaml.safe_load(changed)
    for target in ('inference', 'training'):
        if desired['images'][target]['tag'] != sha:
            raise ValueError('Unexpected desired-state structure')
        desired['images'][target]['tag'] = values['images'][target]['tag']
    if desired != values:
        raise ValueError('Release must change only image tags')
    if original == changed:
        return False
    path.write_text(changed)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--values', type=Path, default=Path('gitops/aws/values.yaml'))
    args = parser.parse_args()
    print('Updated image desired state' if update(args.values, args.sha) else 'Already current')


if __name__ == '__main__':
    main()
