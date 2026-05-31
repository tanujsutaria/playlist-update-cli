Feature: Ingest tracks from Spotify into the SQLite cache
  As a user curating a music library
  I want to pull tracks from my Spotify account into the local store
  So that newly discovered songs become candidates in the SQLite cache

  Background:
    Given a seeded library with 5 tracks

  Scenario: Ingesting liked tracks adds new candidates to the store
    Given Spotify reports 2 liked tracks not yet in the store
    When I run "ingest liked"
    Then the command succeeds
    And the new liked tracks are stored as candidates
    And the total track count increases by 2

  Scenario: Ingesting recently played tracks adds new candidates to the store
    Given Spotify reports 1 recently played track not yet in the store
    When I run "ingest recent"
    Then the command succeeds
    And the new recent track is stored as a candidate

  Scenario: Ingesting an already-seeded track upserts rather than duplicates
    Given Spotify reports a liked track that already exists in the store
    When I run "ingest liked"
    Then the command succeeds
    And the total track count is unchanged

  Scenario: Tracks missing a name or artist are skipped during ingest
    Given Spotify reports 1 valid liked track and 1 malformed liked track
    When I run "ingest liked"
    Then the command succeeds
    And the total track count increases by 1
