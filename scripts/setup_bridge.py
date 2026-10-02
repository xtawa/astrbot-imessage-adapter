"""Install the locked Node dependencies and build the bridge on all platforms."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def npm_command(node: str, npm: str | None) -> list[str]:
    # A custom Node runtime can coexist with npm from another installation.
    candidates = [Path(node).parent / "node_modules/npm/bin/npm-cli.js"]
    if npm:
        candidates.append(Path(npm).parent / "node_modules/npm/bin/npm-cli.js")
        if Path(npm).resolve().name == "npm-cli.js":
            candidates.append(Path(npm).resolve())
    for cli in candidates:
        if cli.is_file():
            return [node, str(cli)]
    if npm and os.name != "nt":
        return [npm]
    raise SystemExit("npm is required; install the full Node.js distribution")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    node = shutil.which(args.node)
    if not node:
        raise SystemExit("Install Node.js 22+ first")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        raise SystemExit("Node.js 22+ is required")
    sidecar = Path(__file__).resolve().parent.parent / "sidecar"
    # npm.cmd isn't a native executable; invoke the shipped JS CLI directly.
    command = npm_command(node, shutil.which("npm"))
    subprocess.run([*command, "ci", "--no-audit", "--no-fund"], cwd=sidecar, check=True)
    subprocess.run([*command, "run", "build"], cwd=sidecar, check=True)
    print("Photon bridge setup complete")


if __name__ == "__main__":
    main()
