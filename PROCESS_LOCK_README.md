# Process Locking System

This system ensures that when `Monitoring.py` is processing a token, other Python scripts (except `telegram_manager.py` and `wallet_manager.py`) will pause their execution until the token processing is complete.

## How It Works

1. **Lock File**: A lock file (`process.lock`) is created when `Monitoring.py` starts processing a token and deleted when processing is complete.

2. **Exempt Scripts**:
   - `Monitoring.py` (the main processing script)
   - `telegram_manager.py` (exempt to ensure notifications work)
   - `wallet_manager.py` (exempt to ensure wallet operations work)

## How to Use in Your Code

### Option 1: Using the Decorator (Recommended)

```python
from process_lock import pause_if_processing

@pause_if_processing
def my_function():
    # This function will automatically pause if a token is being processed
    print("Doing some work...")
```

### Option 2: Manual Check

```python
from process_lock import should_pause_execution

def my_function():
    if should_pause_execution():
        print("Token is being processed - pausing execution")
        while should_pause_execution():
            time.sleep(1)  # Check every second
    
    print("Doing some work...")
```

### Option 3: Skip Execution

```python
from process_lock import should_pause_execution

def my_function():
    if should_pause_execution():
        print("Skipping execution - token is being processed")
        return
        
    print("Doing some work...")
```

## Example

See `example_usage.py` for a complete example of how to use the process locking system.

## Notes

- The lock is file-based, so it works across different Python processes
- The system checks for the lock file every second when waiting
- Only the scripts listed in the `exempt_scripts` list in `process_lock.py` are exempt from pausing
- The lock is automatically released when `Monitoring.py` finishes processing a token or encounters an error
