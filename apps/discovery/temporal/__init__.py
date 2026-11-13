"""Discovery on Temporal.

- workflows.py: the durable steps. Temporal replays them, so they import nothing but
  temporalio and do no I/O; every real action is an activity, called by name.
- activities.py: the actions (run a search, fail a run, the scheduler tick, the sweep).
  They call the same plain functions the tests call (apps.discovery.tasks and .jobs).
- client.py: starting a run's workflow from Django.

The worker and the Schedules are wired up in config/temporal.py.

Kept empty on purpose: Temporal's sandbox imports this package with the workflows.
"""
