Feature: Dispatcher robustness
  The command dispatcher (dispatch_command + the _COMMAND_HANDLERS registry)
  must route known commands to the correct handler and degrade gracefully on
  errors: unknown commands and handlers whose underlying CLI method raises must
  return exit code 1 instead of crashing the process.

  Background:
    Given a seeded library with a wired CLI

  Scenario: An unknown command returns exit code 1
    Given the command "totally-not-a-command" is not in the registry
    When the dispatcher runs the unknown command "totally-not-a-command"
    Then the dispatcher returns exit code 1

  Scenario: A handler whose CLI method raises is caught and returns 1
    Given the "view" handler's CLI method raises an exception
    When the dispatcher runs "view Favorites"
    Then the dispatcher returns exit code 1
    And the raising method was invoked exactly once
    And the process did not crash

  Scenario: A known command routes to the correct handler
    When the dispatcher runs "view Favorites"
    Then the dispatcher returns exit code 0
    And the playlist view output contains the seeded track "First Light"
    And the Spotify edge was queried for the "Favorites" playlist

  Scenario: A handler returning a non-zero code propagates that code
    Given the "view" handler is replaced with one returning code 1
    When the dispatcher runs "view Favorites"
    Then the dispatcher returns exit code 1
