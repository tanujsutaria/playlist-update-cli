Feature: Backup, list, and restore the data directory
  As a maintainer of the playlist CLI
  I want to snapshot, enumerate, and restore the on-disk data folder
  So that I can recover the SQLite database after a bad change

  Background:
    Given a hermetic project root with a data directory
    And the data directory holds a SQLite database with 3 tracks

  Scenario: Backup creates a snapshot of the data directory
    When I run "backup snap1"
    Then the command returns 0
    And a backup named "snap1" exists under backups
    And the backup contains a SQLite database with 3 tracks

  Scenario: list-backups reports the created snapshot
    Given the data has been backed up as "snap1"
    When I run "list-backups"
    Then the command returns 0
    And the output mentions the backup "snap1"

  Scenario: Restore atomically brings the data back intact
    Given the data has been backed up as "snap1"
    And the live database is corrupted
    When I run "restore snap1"
    Then the command returns 0
    And the live database again has 3 tracks
    And no staging or data_old directories are left behind

  Scenario: Restoring a missing backup leaves the live data untouched
    When I run "restore does-not-exist"
    Then the command returns 0
    And the live database still has 3 tracks
    And no backup named "does-not-exist" exists under backups
