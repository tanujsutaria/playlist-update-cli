Feature: Search command
  As a CLI user with a seeded library
  I want to run a deep search backed by the provider pipeline
  So that new candidate songs are surfaced, cached, and persisted to SQLite
  (with the external LLM/web-search provider boundary fully mocked)

  Background:
    Given a seeded library
    And the search provider returns canned results

  Scenario: A fresh search surfaces results and caches CLI state
    When I run the search command "upbeat indie tracks from 2021"
    Then it exits with code 0
    And the CLI has cached search results
    And the cached results were freshly fetched
    And no provider subprocess or network call was made

  Scenario: A fresh search persists candidate rows to SQLite
    When I run the search command "mellow synth ballads"
    Then it exits with code 0
    And the search run is recorded in the database
    And the candidate tracks are persisted in the database

  Scenario: Repeating the same search is served from the cache
    When I run the search command "energetic dance anthems"
    And I run the search command "energetic dance anthems"
    Then it exits with code 0
    And the cached results were served from the cache
    And only one search run is recorded in the database

  Scenario: A search with no provider results resets search state
    Given the search provider returns no results
    When I run the search command "obscure unmatchable nonsense"
    Then it exits with code 0
    And the CLI has no cached search results
