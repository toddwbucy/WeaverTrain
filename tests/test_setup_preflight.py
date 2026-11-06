"""Exercise the driver query through the setup script's real preflight."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def preflight(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts" / "setup-env.sh"
    shutil.copyfile(source, scripts / source.name)
    (tmp_path / ".env").write_text(
        "CUDA_DEVICE_ORDER=PCI_BUS_ID\nCUDA_VISIBLE_DEVICES=0,1\n"
        "WEAVER_TRAIN_STATE_DIR=./var\n"
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("uv", "rsync", "git"):
        tool = tools / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    driver = tools / "nvidia-smi"
    driver.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        "import signal\n"
        "import sys\n"
        "if len(sys.argv) == 1:\n"
        "    print('CUDA Version: 13.0')\n"
        "else:\n"
        "    assert sys.argv[1:] == ['--query-gpu=driver_version', '--format=csv,noheader']\n"
        "    signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
        "    os.write(1, b'615.71.09\\n')\n"
        "    chunk = b'999.00.00\\n' * 1024\n"
        "    for _ in range(256):\n"
        "        remaining = chunk\n"
        "        while remaining:\n"
        "            remaining = remaining[os.write(1, remaining):]\n"
        "    Path('driver-query-finished').write_text('all rows emitted')\n"
        "    sys.exit(int(os.environ.get('DRIVER_QUERY_EXIT', '0')))\n"
    )
    driver.chmod(0o755)
    env = dict(os.environ, PATH=f"{tools}{os.pathsep}{os.environ['PATH']}")

    def run(exit_code):
        return subprocess.run(
            ["bash", str(scripts / source.name), "--only", "preflight"],
            cwd=tmp_path, env=dict(env, DRIVER_QUERY_EXIT=str(exit_code)),
            text=True, capture_output=True, timeout=20,
        )

    return run, tmp_path


def test_driver_query_drains_all_rows_and_keeps_first_version(preflight):
    run, root = preflight
    result = run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "driver-query-finished").read_text() == "all rows emitted"
    assert "driver 615.71.09 (CUDA 13)" in result.stdout
    assert "999.00.00" not in result.stdout


def test_driver_query_failure_stops_preflight_after_valid_output(preflight):
    run, root = preflight
    result = run(7)
    assert (root / "driver-query-finished").read_text() == "all rows emitted"
    assert result.returncode == 1
    assert 'driver_ver=' in result.stderr
    assert "FAILED: line" in result.stderr
    assert "driver 615.71.09" not in result.stdout
