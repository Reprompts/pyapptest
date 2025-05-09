<!-- README.md for PyApptest -->
# PyApptest

![Downloads](https://static.pepy.tech/badge/pyapptest/month) 

`pyapptest` is a Streamlit-based CLI tool to **discover**, **test**, and **report** API endpoints in Python frameworks (FastAPI, Flask, Django).

## Features

- **Static endpoint discovery**: scans your current directory for existing API routes in FastAPI, Flask, or Django projects.
- **Interactive UI**: launches a Streamlit UI to run and view tests.
- **Multiple framework support**: works out-of-the-box with FastAPI, Flask, and Django endpoints.
- **Faker integration**: generate sample payloads automatically.

## Installation

```bash
pip install pyapptest

Usage

# Launch the testing UI
pyapptest

Once your testing session is complete, you can uninstall:

pip uninstall pyapptest

