# Specification Template
> Ingest the information from this file, implement the Low-Level Tasks, and generate the code that will satisfy the High and Mid-Level Objectives.

## High-Level Objective

- Refactor this codebase such that a toml file can be used in conjunction with uv to manage dependencies instead of a requirments.txt file and remove requirements.txt when this refactor is completed 

## Mid-Level Objective
- No functionality loss in the existing application
- No code changes to the src/ files that aren't related to dependency management
- UPDATE README.md with the new functionality
- Run uv sync such that dependencies can be installed