"""
Automates adding a new CLI argument and corresponding functionality
to this application's source code. Uses Aider for Git-based AI editing.

References:
  - ai_docs/aider-scripting.md
  - reference_docs/new_chart.py
  - reference_docs/new-chart-type.md
"""
import sys
import os
from pathlib import Path

# Check if aider is installed
try:
    from aider.coders import Coder
    from aider.models import Model
    from aider.io import InputOutput
except ImportError:
    print("Error: 'aider' package not found. Please install it with:")
    print("  pip install aider-chat")
    print("  # or")
    print("  uv pip sync")
    sys.exit(1)

def new_cli_arg(instruction: str):
    """
    Create or modify CLI argument(s) in the application's source code, based on
    the user-provided instruction.
    """
    # Identify files to edit in src/
    files_to_edit = [
        "src/arg_parse.py",
        "src/main.py",
        "src/db_manager.py",
        "src/rotation_manager.py",
        "src/spotify_manager.py",
        "README.md"
    ]

    # Create an overall prompt for Aider
    prompt = (
        "Add or modify a CLI argument in the application's code based on the following instruction:\n\n"
        f"{instruction}\n\n"
        "Update src/arg_parse.py to register the argument.\n"
        "Update src/... to implement the functionality.\n"
        "Update README.md to reflect the new argument.\n"
        "Follow best practices and ensure the new argument works seamlessly.\n"
        "Do not add any other functionality to the code.\n"
        "Do not add any other files to the code.\n"
        "Do not change any existing functionality.\n"
        "Do not add any other dependencies to the code.\n"
    )

    # Initialize the model
    model = Model(
        "o1",
        editor_model="claude-3-7-sonnet-20250219",
        editor_edit_format="diff",
    )

    # Create coder with the identified files
    coder = Coder.create(
        main_model=model,
        edit_format="architect",
        io=InputOutput(yes=True),
        fnames=files_to_edit,
        auto_commits=False,
        suggest_shell_commands=False,
    )

    # Execute the code modification with the prompt
    coder.run(prompt)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python new_cli_arg.py '<instruction describing the new CLI arg>'")
        sys.exit(1)

    user_instruction = sys.argv[1]
    new_cli_arg(user_instruction)
