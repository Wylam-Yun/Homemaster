import subprocess
import sys


def test_package_version() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import task_brain; print(task_brain.__version__)"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.0"
