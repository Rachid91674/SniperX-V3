"""
Example script demonstrating how to use the process lock to pause execution
when a token is being processed by Monitoring.py
"""
import time
from process_lock import pause_if_processing, should_pause_execution

def regular_function():
    print("This is a regular function that will be paused if a token is being processed")
    print("Doing some work...")
    time.sleep(2)
    print("Work complete!")

@pause_if_processing
def decorated_function():
    print("This function is decorated with @pause_if_processing")
    print("It will automatically pause if a token is being processed")
    print("Doing some work...")
    time.sleep(2)
    print("Work complete!")

def check_lock_status():
    if should_pause_execution():
        print("🔒 Token is being processed - execution should pause")
    else:
        print("🔓 No token is being processed - execution can proceed")

if __name__ == "__main__":
    print("=== Process Lock Example ===")
    
    # Check lock status
    check_lock_status()
    
    # This function will be paused if a token is being processed
    decorated_function()
    
    # This function will run immediately regardless of lock status
    print("\nCalling regular function (not decorated):")
    if not should_pause_execution():
        regular_function()
    else:
        print("Skipping regular function - token is being processed")
