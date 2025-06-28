import os
import subprocess
import sys
import time
import signal
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

CSV_FILENAME = "token_risk_analysis.csv"
TARGET_SCRIPT_FILENAME = "Monitoring.py"
LOG_FILENAME = "monitoring_watchdog.log"
LOCK_FILENAME = "monitoring_active.lock"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.join(SCRIPT_DIR, CSV_FILENAME)
TARGET_SCRIPT_PATH = os.path.join(SCRIPT_DIR, TARGET_SCRIPT_FILENAME)
LOG_FILE_PATH = os.path.join(SCRIPT_DIR, LOG_FILENAME)
LOCK_FILE_PATH = os.path.join(SCRIPT_DIR, LOCK_FILENAME)
PID_FILE = os.path.join(SCRIPT_DIR, "monitoring_watchdog.pid")

class RiskCSVHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_mtime = 0
        self.running_process = None
        self.cooldown_seconds = 5
        self.last_launch_time = 0
        self._cleanup_previous_process()

    def _terminate_pid(self, pid: int):
        try:
            os.kill(pid, signal.SIGTERM)
            start = time.time()
            while time.time() - start < 5:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except OSError:
                    break
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    def _pid_is_running(self, pid: int) -> bool:
        """Check whether a PID is currently running."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True

    def _cleanup_previous_process(self):
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, "r") as pf:
                    old_pid = int(pf.read().strip())
                self._terminate_pid(old_pid)
            except Exception:
                pass
            finally:
                try:
                    os.remove(PID_FILE)
                except FileNotFoundError:
                    pass

    def on_modified(self, event):
        if event.is_directory:
            return
        if os.path.abspath(str(event.src_path)) != CSV_FILE_PATH:
            return
        try:
            current_mtime = os.path.getmtime(CSV_FILE_PATH)
        except FileNotFoundError:
            self.last_mtime = 0
            return
        if current_mtime == self.last_mtime:
            return
        self.last_mtime = current_mtime
        current_time = time.time()
        if current_time - self.last_launch_time < self.cooldown_seconds:
            return
        if os.path.exists(LOCK_FILE_PATH):
            pid_in_lock = None
            try:
                with open(LOCK_FILE_PATH, "r") as lf:
                    pid_in_lock = int(lf.read().strip())
            except Exception:
                pid_in_lock = None

            if pid_in_lock and self._pid_is_running(pid_in_lock):
                print(
                    f"Watchdog: Monitoring already active (lock file {LOCK_FILE_PATH}, PID {pid_in_lock}). Skipping restart."
                )
                return
            else:
                try:
                    os.remove(LOCK_FILE_PATH)
                    print(f"Watchdog: Removed stale lock file {LOCK_FILE_PATH}.")
                except Exception:
                    pass
        self.last_launch_time = current_time
        if self.running_process and self.running_process.poll() is None:
            self._terminate_pid(self.running_process.pid)
            self.running_process = None
            try:
                os.remove(PID_FILE)
            except FileNotFoundError:
                pass
        if not os.path.exists(TARGET_SCRIPT_PATH):
            print(f"Watchdog: Target script '{TARGET_SCRIPT_PATH}' not found.")
            return
        try:
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as logf:
                logf.write(f"\n--- Launching {TARGET_SCRIPT_FILENAME} at {time.asctime()} due to {CSV_FILENAME} change ---\n")
                self.running_process = subprocess.Popen(
                    [sys.executable, TARGET_SCRIPT_PATH],
                    stdout=logf,
                    stderr=logf,
                    text=True,
                    cwd=SCRIPT_DIR,
                )
                with open(PID_FILE, "w") as pf:
                    pf.write(str(self.running_process.pid))
            print(f"Watchdog: Started '{TARGET_SCRIPT_FILENAME}' with PID {self.running_process.pid}.")
        except Exception as e:
            print(f"Watchdog: Failed to launch '{TARGET_SCRIPT_FILENAME}': {e}")

if __name__ == "__main__":
    if not os.path.exists(os.path.dirname(CSV_FILE_PATH)):
        print(f"Watchdog: Directory for CSV '{CSV_FILE_PATH}' does not exist. Exiting.")
        sys.exit(1)
    handler = RiskCSVHandler()
    observer = Observer()
    observer.schedule(handler, path=os.path.dirname(CSV_FILE_PATH), recursive=False)
    print("--- Token risk CSV Watchdog Initialised ---")
    print(f"Monitoring: {CSV_FILE_PATH}")
    print(f"Target script: {TARGET_SCRIPT_PATH}")
    print("Press Ctrl+C to stop.")
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nWatchdog: KeyboardInterrupt received. Stopping observer...")
    finally:
        observer.stop()
        observer.join()
        if handler.running_process and handler.running_process.poll() is None:
            handler._terminate_pid(handler.running_process.pid)
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
        print("Watchdog: Exiting.")
