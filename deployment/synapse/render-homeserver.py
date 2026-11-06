#!/usr/bin/env python3
"""Render a private Synapse configuration without adding a templating dependency."""

import os
import stat
from pathlib import Path
from string import Template

template = Path("/config/homeserver.yaml.template").read_text(encoding="utf-8")
rendered = Template(template).substitute(os.environ)
target = Path(os.environ.get("SYNAPSE_CONFIG_PATH", "/data/homeserver.yaml"))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(rendered, encoding="utf-8")
target.chmod(stat.S_IRUSR | stat.S_IWUSR)
os.chown(target, 991, 1000)

# The guard writes a dedicated registry, readable by the non-admin OrgBot and
# Export Service (UID/GID 1000). No Synapse keys or database files are shared.
registry_directory = Path("/chat-guard")
registry_directory.mkdir(parents=True, exist_ok=True)
os.chown(registry_directory, 991, 1000)
registry_directory.chmod(0o2750)
