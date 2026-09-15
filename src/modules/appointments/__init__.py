"""Appointments: bookable slots that share a queue's daily limit with walk-ins (Issue 80).

* :mod:`.schedule`: which slots a queue's windows give, against the clinic's hours (pure).
* :mod:`.capacity`: the one place walk-ins and appointments are counted against one daily limit,
  with the database locks and constraint that make that hold under concurrency.
* :mod:`.availability`: whether a slot is on offer (pure), shared by the day view and booking.
* :mod:`.service`: the clinic's policy, windows, overrides, generation and blocks.
"""
