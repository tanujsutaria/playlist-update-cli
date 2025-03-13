# playlist-update-cli - New cli arg script specification
> Ingest the information from this file, implement the Low-Level Tasks, and generate the code that will satisfy the High and Mid-Level Objectives.

## High-Level Objective

- Create a new script such that a new cli arg and associated functionality can be added when it is run with user provided instructions

## Mid-Level Objective

- Create a new script in adw/new_cli_arg.py
- Use ai_docs/aider-scripting.md as a guide of how to do this. Additionally, use reference_docs/new_chart.py and reference_docs/new-chart-type.md as concrete examples of example output for a different project
- Ensure the new script is compatible with the existing application structure which is found in src/

## Implementation Notes

- Add aider as a dependency to `pyproject.toml`.
- Carefully review each low-level task for precise code changes.

## Context

### Beginning Context

- `ai_docs/aider-scripting.md` (readonly)        
- `reference_docs/new-chart-type.md` (readonly)
- `reference_docs/new_chart.py` (readonly)
- `src/__init__.py` (readonly)
- `src/arg_parse.py` (readonly)  
- `src/db_manager.py` (readonly)           
- `src/main.py` (readonly)               
- `src/models.py` (readonly)                  
- `src/rotation_manager.py` (readonly)   
- `src/setup.py` (readonly)
- `src/spotify_manager.py` (readonly)
- `src/test_delete_recreate.py` (readonly)
- `src/test_playlist_update.py` (readonly)
- `src/test_rotation.py` (readonly)
- `src/test_spotify.py` (readonly)
- `pyproject.toml` (editable)
- `adw/new_cli_arg.py` (editable)

### Ending Context

- `pyproject.toml` (updated)
- `adw/new_cli_arg.py` (updated)

## Low-Level Tasks
> Ordered from start to finish

1. Create a New Script in `new_cli_arg.py`

```aider
UPDATE adw/new_cli_arg.py:
    CREATE a new script using ai_docs/aider-scripting.md,reference_docs/new_chart.py and reference_docs/new-chart-type.md as references:
        This script should be able to be used by this cli application depicted in src/.
        This script enables passing a new cli arg and desired functionality and should automatically update src to enable the use of the same.
```
