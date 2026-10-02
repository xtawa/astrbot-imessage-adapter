import importlib.util
from pathlib import Path


def load_setup():
    path = Path(__file__).parent.parent / "scripts/setup_bridge.py"
    spec = importlib.util.spec_from_file_location("photon_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_node_and_npm_installations(tmp_path):
    node = tmp_path / "custom-node/node.exe"
    npm = tmp_path / "other-node/npm"
    cli = npm.parent / "node_modules/npm/bin/npm-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// npm entry point")
    command = load_setup().npm_command(str(node), str(npm))
    assert command == [str(node), str(cli)]


def test_npm_symlinked_js_entry(tmp_path):
    # Path.resolve() returns the real JS entry for POSIX npm symlinks.
    cli = tmp_path / "npm-cli.js"
    cli.write_text("// npm entry point")
    assert load_setup().npm_command("node", str(cli)) == ["node", str(cli)]
