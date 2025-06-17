import time
import os
import subprocess
import sys
import signal
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- Configuration ---
# Name of the CSV file to monitor
CSV_FILENAME = "sniperx_results_1m.csv"
# Name of the Python script to launch (assumed to be in the same directory as this watchdog script)
TARGET_SCRIPT_FILENAME = "test_chrome.py"
# Name of the log file for the target script's output
TARGET_SCRIPT_LOG_FILENAME = "test_chrome_output.log" # Log for the Bubblemaps script
# Cooldown period in seconds between launches - This watchdog doesn't use a cooldown itself,
# but prevents re-launch if target is running. Target script handles its own processing rate.

# --- Resolve full paths ---
# Directory where this watchdog script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Absolute path to the CSV file
CSV_FILE_PATH = os.path.join(SCRIPT_DIR, CSV_FILENAME)

# Absolute path to the target Python script
TARGET_SCRIPT_PATH = os.path.join(SCRIPT_DIR, TARGET_SCRIPT_FILENAME)
# Absolute path to the log file for the target script
TARGET_SCRIPT_LOG_PATH = os.path.join(SCRIPT_DIR, TARGET_SCRIPT_LOG_FILENAME)
PID_FILE = os.path.join(SCRIPT_DIR, 'test_chrome.pid')


class CSVChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_mtime = 0
        self.running_process = None  # Stores the subprocess.Popen object
        self.last_launch_time = 0 # To implement a simple cooldown for watchdog itself if needed
        self.cooldown_seconds = 5 # Cooldown for watchdog reacting to multiple quick changes
        self._cleanup_previous_process()

    def _terminate_pid(self, pid: int):
        try:
            os.kill(pid, signal.SIGTERM)
            start_time = time.time()
            while time.time() - start_time < 5:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except OSError:
                    break
            else:
                os.kill(pid, signal.SIGKILL)
                print(f"Watchdog: Force killed PID {pid} after timeout.")
            print(f"Watchdog: Terminated previous PID {pid}.")
        except ProcessLookupError:
            print(f"Watchdog: Previous PID {pid} not running.")
        except Exception as e:
            print(f"Watchdog: Error terminating PID {pid}: {e}")

    def _cleanup_previous_process(self):
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as pf:
                    old_pid = int(pf.read().strip())
                self._terminate_pid(old_pid)
            except Exception as e:
                print(f"Watchdog: Failed to read PID file: {e}")
            finally:
                try:
                    os.remove(PID_FILE)
                except FileNotFoundError:
                    pass

    def on_modified(self, event):
        if event.is_directory:
            return

        try:
            event_abs_path = os.path.abspath(str(event.src_path))
        except Exception:
            return

        if event_abs_path == CSV_FILE_PATH:
            try:
                current_mtime = os.path.getmtime(CSV_FILE_PATH)
            except FileNotFoundError:
                print(f"Watchdog: Monitored CSV file '{CSV_FILE_PATH}' seems to have been deleted.")
                self.last_mtime = 0 # Reset mtime
                return

            if current_mtime == self.last_mtime:
                # print(f"Watchdog: '{CSV_FILENAME}' modified event, but mtime is unchanged ({current_mtime}). Skipping.")
                return # No actual content change likely
            
            self.last_mtime = current_mtime

            # Simple cooldown for watchdog itself to avoid rapid-fire launches from editor saves etc.
            current_time = time.time()
            if current_time - self.last_launch_time < self.cooldown_seconds:
                print(f"Watchdog: '{CSV_FILENAME}' changed, but still in cooldown. Skipping launch.")
                return

            if not os.path.exists(TARGET_SCRIPT_PATH):
                print(f"Watchdog: ERROR - Target script '{TARGET_SCRIPT_PATH}' not found. Cannot launch.")
                return

            if self.running_process and self.running_process.poll() is None:
                print(f"Watchdog: Terminating previous '{TARGET_SCRIPT_FILENAME}' (PID: {self.running_process.pid})...")
                self._terminate_pid(self.running_process.pid)
                self.running_process = None
                try:
                    os.remove(PID_FILE)
                except FileNotFoundError:
                    pass

            print(f"Watchdog: '{CSV_FILENAME}' content changed (mtime: {current_mtime}). Launching '{TARGET_SCRIPT_FILENAME}'...")
            try:
                with open(TARGET_SCRIPT_LOG_PATH, "a", encoding="utf-8") as logf:
                    logf.write(f"\n--- Watchdog launching {TARGET_SCRIPT_FILENAME} at {time.asctime()} due to {CSV_FILENAME} change ---\n")
                    
                    self.running_process = subprocess.Popen(
                        [sys.executable, TARGET_SCRIPT_PATH],
                        stdout=logf,
                        stderr=logf,
                        text=True,
                        cwd=SCRIPT_DIR
                    )
                    with open(PID_FILE, 'w') as pf:
                        pf.write(str(self.running_process.pid))
                    self.last_launch_time = current_time # Update last launch time
                    print(f"Watchdog: Successfully launched '{TARGET_SCRIPT_FILENAME}' with PID {self.running_process.pid}. "
                          f"Output logged to '{TARGET_SCRIPT_LOG_PATH}'.")
            except Exception as e:
                print(f"Watchdog: ERROR - Failed to launch '{TARGET_SCRIPT_FILENAME}': {e}")


if __name__ == "__main__":
    if not os.path.exists(CSV_FILE_PATH):
        print(f"Watchdog: WARNING - Monitored CSV file '{CSV_FILE_PATH}' does not exist. "
              "Waiting for it to be created by SniperX V2.")

    watch_directory = os.path.dirname(CSV_FILE_PATH)
    if not os.path.isdir(watch_directory):
        print(f"Watchdog: ERROR - Cannot watch directory '{watch_directory}' as it does not exist. Exiting.")
        sys.exit(1)

    print(f"--- Watchdog Initializing ---")
    print(f"Monitoring CSV file: '{CSV_FILE_PATH}'")
    print(f"In directory       : '{watch_directory}'")
    print(f"Target script      : '{TARGET_SCRIPT_PATH}'")
    print(f"Target script log  : '{TARGET_SCRIPT_LOG_PATH}'")
    print(f"---------------------------")

    event_handler = CSVChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=watch_directory, recursive=False)
    
    print("Watchdog: Observer starting. Press Ctrl+C to stop.")
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nWatchdog: KeyboardInterrupt received. Stopping observer...")
    except Exception as e:
        print(f"Watchdog: An unexpected error occurred in the main loop: {e}")
    finally:
        observer.stop()
        print("Watchdog: Observer stopped.")
        observer.join()
        print("Watchdog: Observer joined.")

        if event_handler.running_process and event_handler.running_process.poll() is None:
            print(f"Watchdog: Terminating running target script '{TARGET_SCRIPT_FILENAME}' (PID: {event_handler.running_process.pid})...")
            event_handler.running_process.terminate()
            try:
                event_handler.running_process.wait(timeout=5)
                print(f"Watchdog: Target script PID {event_handler.running_process.pid} terminated gracefully.")
            except subprocess.TimeoutExpired:
                print(f"Watchdog: Target script PID {event_handler.running_process.pid} did not terminate gracefully. Sending SIGKILL...")
                event_handler.running_process.kill()
                try:
                    event_handler.running_process.wait(timeout=2)
                    print(f"Watchdog: Target script PID {event_handler.running_process.pid} killed.")
                except Exception as e_kill:
                    print(f"Watchdog: Error during SIGKILL for PID {event_handler.running_process.pid}: {e_kill}")
            except Exception as e_term:
                 print(f"Watchdog: Error during termination for PID {event_handler.running_process.pid}: {e_term}")
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
        print("Watchdog: Exiting.")