# pyapitest/cli/main.py

import os
import subprocess
import sys

def launch_ui():
    # Determine the path to the top-level ui.py script.
    ui_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'ui.py')
    )
    # Launch Streamlit via: python -m streamlit run <ui.py>
    command = [sys.executable, "-m", "streamlit", "run", ui_script]
    subprocess.run(command)

def main():
    """Console-script entry point."""
    launch_ui()

if __name__ == "__main__":
    main()
