"""Vendor Argo references into a temporary KFP checkout before rendering."""
from pathlib import Path
import os
import re
import sys

kfp, argo = map(lambda s: Path(s).resolve(), sys.argv[1:])
for path in (kfp / 'manifests/kustomize/third-party/argo').rglob('kustomization.yaml'):
    text = path.read_text()
    text = re.sub(r'https://github.com/argoproj/argo-workflows/([^?\s]+)\?ref=v4\.0\.5(?:&timeout=60s)?',
                  lambda match: os.path.relpath(argo / match[1], path.parent), text)
    path.write_text(text)
