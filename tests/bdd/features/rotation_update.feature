Feature: Playlist rotation update
  The `update` command selects songs from the seeded library for a playlist's
  next rotation. A dry run previews the selection without touching Spotify or
  the rotation history; a real update records a new generation into the SQLite
  rotation tables; and selection prefers songs that have never been used.

  Background:
    Given a seeded library

  Scenario: Dry run previews a selection without writing anything
    When I run the update command "update Favorites --count 3 --dry-run"
    Then it exits with code 0
    And the dry-run output lists the selected songs
    And no rotation generation rows were added
    And the playlist was not refreshed on Spotify

  Scenario: A real update records a new rotation generation
    Given a rotation-compatible playlist "Mixtape" with 2 generations
    When I run a real update of "Mixtape" requesting 3 songs
    Then a new rotation generation row was added for "Mixtape"
    And new generation_tracks rows were recorded for "Mixtape"
    And the playlist current generation advanced by one
    And the playlist was refreshed on Spotify

  Scenario: Selection prefers previously-unused songs
    Given two brand-new songs that have never been in any rotation
    When I select 2 songs for the next rotation of "Favorites"
    Then the selection consists only of the brand-new songs
