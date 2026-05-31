Feature: View command
  As a CLI user with a seeded library
  I want to run the view command for a playlist
  So that I can list its tracks, even when the name case differs
  And I get a graceful result when the playlist does not exist

  Scenario: Viewing an existing playlist lists its tracks
    Given a seeded library
    When I run the view command for "Favorites"
    Then it exits with code 0
    And the playlist tracks are printed to output

  Scenario: Viewing a missing playlist reports not-found gracefully
    Given a seeded library
    When I run the view command for "Nonexistent"
    Then it exits with code 0
    And no track rows are printed to output

  Scenario: A playlist found only via pagination resolves case-insensitively
    Given a real Spotify manager whose "Favorites" playlist lives on a later page
    When I view the playlist using the lowercase name "favorites"
    Then it exits with code 0
    And every page of the paginated playlists was fetched
    And the paginated playlist tracks are printed to output
