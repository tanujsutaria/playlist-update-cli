# Transcript Analytics - New Chart Type Specification
> Ingest the information from this file, implement the Low-Level Tasks, and generate the code that will satisfy the High and Mid-Level Objectives.

## High-Level Objective

- Add a new chart type to the transcript analytics application.

## Mid-Level Objective

- Implement a new chart function in `chart.py` based on the provided description.
- Update the CLI application to support generating the new chart type.
- Ensure the new chart integrates smoothly with existing functionality.

## Implementation Notes

- Use only the dependencies listed in `pyproject.toml`.
- Comment every function thoroughly.
- Carefully review each low-level task for precise code changes.

## Context

### Beginning Context

- `src/aider_has_a_secret/main.py`
- `src/aider_has_a_secret/chart.py`
- `pyproject.toml` (readonly)

### Ending Context

- `src/aider_has_a_secret/main.py` (updated)
- `src/aider_has_a_secret/chart.py` (updated)
- `pyproject.toml`

## Low-Level Tasks
> Ordered from start to finish

1. Create a New Chart Function in `chart.py`

```aider
UPDATE src/aider_has_a_secret/chart.py:
    ADD a new function `create_<chart_type>_chart(word_counts: WordCounts)` that implements the new chart type based on the following 
    description: '<description>'
```

2. Update the CLI Application to Support the New Chart Type

```aider
UPDATE src/aider_has_a_secret/main.py:
    UPDATE the analyze_transcript(...):
        ADD new chart type in the `chart_type` parameter
        Call the new chart function based on the new chart type
```
