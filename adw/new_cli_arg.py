"""
Automates adding a new CLI argument and corresponding functionality
to this application's source code. Uses Aider for Git-based AI editing.

References:
  - ai_docs/aider-scripting.md
  - reference_docs/new_chart.py
  - reference_docs/new-chart-type.md
"""
import sys
from pathlib import Path
from aider.coders import Coder
from aider.models import Model
from aider.io import InputOutput

def new_cli_arg(instruction: str):
    """
    Create or modify CLI argument(s) in the application's source code, based on
    the user-provided instruction.
    """
    # Identify files to edit in src/
    files_to_edit = [
        "src/arg_parse.py",
        "src/main.py",
    ]

    # Create an overall prompt for Aider
    prompt = (
        "Add or modify a CLI argument in the application's code based on the following instruction:\n\n"
        f"{instruction}\n\n"
        "Update src/arg_parse.py to register the argument.\n"
        "Update src/main.py to implement the functionality.\n"
        "Follow best practices and ensure the new argument works seamlessly.\n"
    )

    # Initialize the model
    model = Model(
        "gpt-4-turbo",
        editor_model="gpt-4-turbo",
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
