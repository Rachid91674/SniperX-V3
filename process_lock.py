import os
import time
import sys
from pathlib import Path

class ProcessLock:
    def __init__(self, lock_name="process.lock"):
        """
        Initialize a process lock.
        
        Args:
            lock_name (str): Name of the lock file
        """
        self.script_dir = Path(__file__).parent.absolute()
        self.lock_file = self.script_dir / lock_name
        self.lock_acquired = False
    
    def acquire(self, blocking=True, timeout=30):
        """
        Acquire the lock.
        
        Args:
            blocking (bool): If True, wait for the lock to be released
            timeout (int): Maximum time to wait for the lock in seconds (only if blocking=True)
            
        Returns:
            bool: True if lock was acquired, False otherwise
        """
        if self.lock_acquired:
            return True
            
        start_time = time.time()
        
        while True:
            try:
                # Try to create the lock file exclusively
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                with os.fdopen(fd, 'w') as f:
                    f.write(f"{os.getpid()}\n{time.time()}")
                self.lock_acquired = True
                return True
            except (OSError, IOError):
                # Lock is held by another process
                if not blocking:
                    return False
                    
                # Check if we've exceeded the timeout
                if time.time() - start_time >= timeout:
                    return False
                    
                # Wait a bit before retrying
                time.sleep(0.1)
    
    def release(self):
        """Release the lock if it's held by this process."""
        if not self.lock_acquired:
            return
            
        try:
            # Verify we own the lock before releasing
            if os.path.exists(self.lock_file):
                with open(self.lock_file, 'r') as f:
                    pid = f.readline().strip()
                    if pid == str(os.getpid()):
                        os.unlink(self.lock_file)
        except (OSError, IOError):
            pass
            
        self.lock_acquired = False
    
    def is_locked(self):
        """Check if the lock is currently held by any process."""
        return os.path.exists(self.lock_file)
    
    def __enter__(self):
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

# Global instance for easy import
process_lock = ProcessLock()

def should_pause_execution():
    """
    Check if the current script should pause execution based on the process lock.
    
    Returns:
        bool: True if execution should be paused, False otherwise
    """
    # Get the name of the current script
    current_script = os.path.basename(sys.argv[0]).lower()
    
    # These scripts are exempt from pausing
    exempt_scripts = ['monitoring.py', 'telegram_manager.py', 'wallet_manager.py']
    
    if current_script in exempt_scripts:
        return False
        
    return process_lock.is_locked()

def pause_if_processing(func):
    """
    Decorator that checks if a token is being processed before executing the function.
    If a token is being processed, the function will be paused until the processing is complete.
    
    Usage:
        @pause_if_processing
        def my_function():
            # This function will only execute when no token is being processed
            pass
    """
    def wrapper(*args, **kwargs):
        while should_pause_execution():
            print(f"⏸️ Pausing {func.__name__} - Token is being processed")
            time.sleep(1)  # Check every second
        return func(*args, **kwargs)
    return wrapper
