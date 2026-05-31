Feature: Stats command
  As a CLI user with a seeded library
  I want to run the stats command
  So that I can see database statistics

  Scenario: Stats command exits successfully on a seeded library
    Given a seeded library
    When I run the stats command
    Then it exits with code 0
    And the database reports the seeded track count
