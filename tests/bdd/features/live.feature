Feature: Opt-in live Spotify smoke test
  As a maintainer
  I want a read-only sanity check against the real Spotify API
  So that I can confirm credentials authenticate and playlists list
  Without ever modifying any playlist

  # This whole feature is SKIPPED by default. It runs only when BOTH
  # RUN_LIVE_SPOTIFY=1 is set AND real Spotify credentials are present
  # (SPOTIFY_CLIENT_ID, etc., via config/.env or the environment). See
  # test_live_smoke.py and the `make test-live` target for how to run it.

  @live
  Scenario: A real SpotifyManager authenticates and lists playlists read-only
    Given live Spotify credentials are available
    When I construct a real SpotifyManager
    Then it has an authenticated user id
    And it can list the account playlists without modifying anything
