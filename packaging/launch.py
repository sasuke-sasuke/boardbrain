"""PyInstaller entry point with a visible startup failure on a windowed Mac."""
import os
from pathlib import Path
import subprocess
import sys
import traceback


def launch():
    try:
        from boardbrain_v2.desktop import main
        main()
    except Exception:
        # Explicit CLI modes must report a nonzero exit to the setup checks.
        if sys.platform != "darwin" or len(sys.argv) > 1:
            raise
        os.umask(0o077)
        path = Path.home() / "Library/Logs/BoardBrain/startup.log"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(traceback.format_exc())
        path.chmod(0o600)
        script = '''on run argv
display dialog "BoardBrain could not start. A startup log is available to help fix it. Your saved work remains on this Mac." with title "BoardBrain" buttons {"Close", "Show log"} default button "Show log" with icon caution
if button returned of result is "Show log" then do shell script "/usr/bin/open -R " & quoted form of item 1 of argv
end run'''
        subprocess.run(["/usr/bin/osascript", "-e", script, str(path)], check=False)
        raise SystemExit(1)

if __name__ == "__main__":
    launch()
