# SniperX-V3

This repository contains scripts used for monitoring tokens and running automated analyses.

## Environment Variables

Some scripts rely on environment variables for locating required data files. The most important one is:

- `TOKEN_RISK_ANALYSIS_CSV` – Optional. Absolute path to `token_risk_analysis.csv` used by `Monitoring.py`. If not set, the file is expected to be located in the same directory as the script.

Set this variable in your environment or a `.env` file to ensure the scripts can locate the CSV on both Windows and Unix-like systems.

## Python Dependencies

Install the required packages with pip:

```bash
pip install -r requirements.txt
```

The project relies on the new `python-telegram-bot` API (v20+). If you encounter
`ImportError: cannot import name 'Application'` when running the Telegram bot,
ensure the package is upgraded:

```bash
pip install -U "python-telegram-bot>=20.0"
```

## Monitoring Watchdog

`run_monitoring_on_analysis_change.py` monitors `token_risk_analysis.csv` and normally restarts `Monitoring.py` when the file changes. The watchdog now checks for the `monitoring_active.lock` file created by `Monitoring.py`. If the lock exists and the process is still running, the watchdog skips the restart to avoid interrupting an active token analysis.
